#!/usr/bin/env python3
"""Execute read-only content smoke checks on the final image digests, once.

No migrations, live services, image builds, pushes or retagging occur here.
This is bounded packaging acceptance, not production/canary readiness proof.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from release_manifest import ACCEPTANCE, COMPONENTS, build

SERVER_PROBE = r'''
import ast, json, os, tarfile, tomllib
from pathlib import Path
from alembic.script import ScriptDirectory
from shared.version import get_version
from shared.contract_version import CONTRACT_VERSION as server_contract
from bifrost.contract_version import CONTRACT_VERSION as cli_contract
expected = json.loads(os.environ["EXPECTED_RELEASE"])
assert get_version() == expected["version"], "baked server version differs"
assert server_contract == cli_contract == expected["contract"], "CLI/server contract differs"
assert sorted(ScriptDirectory("/app/alembic").get_heads()) == expected["heads"], "migration heads differ"
artifact = Path("/app/artifacts") / ("bifrost-cli-" + expected["version"] + ".tar.gz")
with tarfile.open(artifact, "r:gz") as archive:
    metadata = archive.extractfile("pyproject.toml")
    assert metadata is not None, "bundled CLI metadata missing"
    assert tomllib.loads(metadata.read().decode())["project"]["version"] == expected["version"], "bundled CLI version differs"
    contract_file = archive.extractfile("bifrost/contract_version.py")
    assert contract_file is not None, "bundled CLI contract missing"
    values = []
    for node in ast.parse(contract_file.read().decode()).body:
        targets = node.targets if isinstance(node, ast.Assign) else ([node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(target, ast.Name) and target.id == "CONTRACT_VERSION" for target in targets):
            values.append(ast.literal_eval(node.value))
    assert values == [expected["contract"]], "bundled CLI contract differs"
print("baked version, CLI contract/artifact and migration heads accepted")
'''
CLIENT_PROBE = r'''
set -eu
test -s /usr/share/nginx/html/index.html
test "$(cat /usr/share/nginx/html/bifrost-version.txt)" = "$EXPECTED_VERSION"
printf '%s\n' 'baked client version and static entrypoint accepted'
'''


def command(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def accept(document: dict) -> dict:
    # Recreate the unaccepted manifest from source; never trust an edited input.
    expected = build(document["tag"], document["source"]["commit"], document["build"]["run_id"], document["build"]["run_attempt"], {name: document["images"][name]["digest"] for name in COMPONENTS})
    if document != expected:
        raise ValueError("manifest differs from exact source/build inputs")
    release = json.dumps({
        "version": document["version"],
        "contract": document["compatibility"]["cli_contract"]["current"],
        "heads": document["compatibility"]["database"]["migration_heads"],
    })
    for name in COMPONENTS:
        image = document["images"][name]
        identity = image["repository"] + "@" + image["digest"]
        command("docker", "pull", "--platform", "linux/amd64", identity)
        platform = command("docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", identity)
        if platform != "linux/amd64":
            raise ValueError(f"unsupported {name} platform: {platform}")
        args = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--platform", "linux/amd64"]
        if name == "client":
            command(*args, "--env", "EXPECTED_VERSION=" + document["version"], "--entrypoint", "/bin/sh", identity, "-c", CLIENT_PROBE)
        else:
            command(*args, "--env", "EXPECTED_RELEASE=" + release, "--entrypoint", "python", identity, "-c", SERVER_PROBE)
    return {**document, "acceptance": ACCEPTANCE}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    document = accept(json.loads(args.manifest.read_text()))
    # Only a fully accepted document is published to the final path.
    args.manifest.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
