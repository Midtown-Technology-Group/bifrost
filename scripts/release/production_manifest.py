#!/usr/bin/env python3
"""Assemble one protected-main tuple from existing promotion receipts, without builds.

The signed document is evidence, not deployment authorization. Consumers must
qualify its exact successful CI run/attempt and protected-main signer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from release_manifest import COMPONENTS, DIGEST, REPOSITORY, SHA, WORKFLOW, compatibility, git

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api/scripts/ci"))
import candidate_image as candidate  # noqa: E402
import candidate_provenance  # noqa: E402

PREDICATE_TYPE = "https://bifrost.midtowntg.com/attestations/production-manifest/v2"
PLATFORMS = {"api": ("amd64",), "client": ("amd64",), "worker": ("amd64", "arm64")}


def platforms(image: str, digest: str) -> dict[str, str]:
    """Read and validate the immutable index's actual architecture digest set."""
    raw = candidate._inspect_json(f"{image}@{digest}", "{{json .Manifest}}")
    # buildx .Manifest contains the OCI index, including per-platform digests.
    entries = raw.get("manifests", [])
    result = {}
    for entry in entries:
        platform = entry.get("platform", {})
        if platform.get("os") == "unknown" and platform.get("architecture") == "unknown":
            continue  # BuildKit attestation manifests are not runnable platforms.
        name = f"{platform.get('os')}/{platform.get('architecture')}"
        value = entry.get("digest", "")
        if name in result or not DIGEST.fullmatch(value):
            raise ValueError("duplicate platform or invalid architecture digest")
        result[name] = value
    return result


def build(source: str, run_id: int, attempt: int, outputs: dict, receipts: dict,
          provenance_bundles: dict | None = None) -> dict:
    """Fail closed on substitution and mixed source/component/attempt receipts."""
    if not SHA.fullmatch(source) or git("rev-parse", "HEAD") != source:
        raise ValueError("checkout must equal the exact protected-main source")
    if any(type(v) is not int or v < 1 for v in (run_id, attempt)):
        raise ValueError("positive promotion run and attempt required")
    if set(outputs) != set(COMPONENTS) or set(receipts) != set(COMPONENTS):
        raise ValueError("one complete atomic component tuple required")
    tree = git("rev-parse", f"{source}^{{tree}}")
    parent = git("rev-parse", f"{source}^1")
    images = {}
    versions, candidate_sources, invocations = set(), set(), set()
    for component in COMPONENTS:
        output, receipt = outputs[component], receipts[component]
        image = f"ghcr.io/{REPOSITORY.lower()}-{component}"
        digest = output.get("digest", "")
        if output.get("run_id") != str(run_id) or output.get("run_attempt") != str(attempt):
            raise ValueError("promotion outputs must belong to one current run/attempt")
        if not DIGEST.fullmatch(digest):
            raise ValueError("exact component digest required")
        expected = {
            "schema_version": "bifrost.image-promotion/v1", "component": component,
            "repository": REPOSITORY, "protected_main_source_sha": source,
            "source_tree_sha": tree, "candidate_digest": digest,
            "candidate_ref": f"{image}:candidate-tree-{tree}",
            "candidate_source_sha": output.get("candidate_source_sha"),
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("promotion receipt differs from qualified component/source")
        candidate_source = receipt.get("candidate_source_sha", "")
        version = receipt.get("version", "")
        if not SHA.fullmatch(candidate_source) or not candidate.VERSION_RE.fullmatch(version):
            raise ValueError("exact candidate source and baked dev version required")
        if git("rev-parse", f"{candidate_source}^{{tree}}") != tree:
            raise ValueError("candidate and protected-main trees differ")
        verified = candidate.verify_candidate(image=image, tree_sha=tree, version=version,
                                              source_sha=candidate_source, expected_digest=digest)
        if verified.repository != REPOSITORY:
            raise ValueError("foreign candidate producer")
        provenance, bundle = candidate_provenance.verify(image, digest, candidate_source, tree, source)
        if provenance_bundles is not None:
            provenance_bundles[component] = bundle
        inventory = platforms(image, digest)
        if set(inventory) != {f"linux/{arch}" for arch in PLATFORMS[component]}:
            raise ValueError("component architecture set differs from supported contract")
        images[component] = {"repository": image, "digest": digest, "platforms": inventory,
                             "candidate_provenance": provenance}
        invocations.add((provenance["run_id"], provenance["run_attempt"], provenance["invocation_commit"]))
        versions.add(version)
        candidate_sources.add(candidate_source)
    if len(versions) != 1 or len(candidate_sources) != 1 or len(invocations) != 1:
        raise ValueError("component tuple mixes candidate source or baked version")
    return {
        "schema_version": 2, "product": "bifrost", "channel": "protected-main",
        "version": versions.pop(), "tag": None,
        "source": {"repository": REPOSITORY, "commit": source, "tree": tree,
                   "parent": parent, "candidate_commit": candidate_sources.pop()},
        "images": images,
        # This baseline is the source parent, not an assertion about live production.
        "compatibility": compatibility(source, parent),
        "build": {"workflow": WORKFLOW, "event": "push", "ref": "refs/heads/main",
                  "run_id": run_id, "run_attempt": attempt},
        "acceptance": {"kind": "signed-exact-tree-promotion", "components": list(COMPONENTS)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or
        os.environ.get("GITHUB_EVENT_NAME") != "push" or
        os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise ValueError("only the protected-main push producer may publish this receipt")
    outputs = {name: json.loads(os.environ[name.upper() + "_PROMOTION"]) for name in COMPONENTS}
    receipts = {name: json.loads(Path(outputs[name]["predicate_path"]).read_text()) for name in COMPONENTS}
    bundles = {}
    document = build(os.environ["GITHUB_SHA"], int(os.environ["GITHUB_RUN_ID"]),
                     int(os.environ["GITHUB_RUN_ATTEMPT"]), outputs, receipts, bundles)
    args.output.with_name("candidate-provenance.json").write_text(json.dumps(bundles, indent=2, sort_keys=True) + "\n")
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
