"""Pinned, tests-only source-helper capture; never execute authored code.

Run only in the README's supported, network-disabled Docker lane. The caller
verifies the image and mount/ref identities; this script checks exact producer
bytes, dependency versions and synthetic inputs before importing producers.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import os
import platform
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

PLATFORM_REF = "ba783472b770291e612433ad7ca9564fe671dade"
FOUNDATION_REF = "a5e129ac3cbcb1f3098e7dd0923721fccc84cb16"
WORKSPACE_REF = "83c1cb034dbbcfa29eb1723506735b13b4788536"
SOURCE_PATH = "features/utilities/workflows/check_integration_readiness.py"
SOURCE_HASH = "f49b1b935ef2467f66eccf3c4b2750773f664d1f0e41f8ff6b08e3f11035a1de"
IMAGE = "sha256:1ed49b5898986723c040dab6cf98f05951e71a57c226e025d353f15b300ca581"
EXPECTED_PYTHON = "3.14.7"
EXPECTED_VERSIONS = {
    "pydantic": "2.13.3",
    "pydantic-core": "2.46.3",
    "SQLAlchemy": "2.0.49",
    "cryptography": "50.0.0",
}
EXPECTED_SOURCES = {
    "src/services/solutions/deployment_manifest.py": "815284d4127a646cfbe1fa713ed01bc3ea3ba7e3854f162c9b53d4f216b618bc",
    "bifrost/workspace_release.py": "6e7d20a4ad7c5fd5b3a51b0932f7c70a69477c217c3f6496902051c61957ed92",
    "bifrost/solution_delivery_review.py": "a48d4d7f95ddcfc73fb34c896ad5fc22565faa84bdda1878e56db5155c7c8540",
    "src/services/solutions/deployment_runtime.py": "5282fb57793eab13861193590e3f47bcfca6901c52b38fb9b12dfc14c73cc331",
    "src/services/execution/attempts.py": "82ed8abc28a1a51d17ddcdaeeabf9473468ef8474c172bb01e7bc5b66b067afa",
    "src/services/work_delivery_store.py": "a208dbeb2c16fd50b6062b07362533278be00e1fe0c66aad152ab3c3c6bd4a6c",
}
EXPECTED_ENVIRONMENT = {
    "BIFROST_ENVIRONMENT": "testing",
    "BIFROST_WORK_DELIVERY_BACKEND": "postgres",
    "BIFROST_SECRET_KEY": "runtime-evidence-synthetic-fixture-key-0001",
    "BIFROST_DATABASE_URL": "postgresql+asyncpg://probe:probe@127.0.0.1:1/probe",
    "BIFROST_REDIS_URL": "redis://127.0.0.1:1/15",
}
parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
parser.add_argument("--source-root", type=Path, required=True, help="read-only pinned API tree (normally /app)")
parser.add_argument("--authored-source", type=Path, required=True, help="pinned authored file mounted as text; never imported")
ARGS = parser.parse_args()
SOURCE_ROOT = ARGS.source_root.resolve(strict=True)
AUTHORED_SOURCE = ARGS.authored_source.resolve(strict=True)
ORIGINAL_DUMPS = json.dumps
ARTIFACT = {
    "schema": "bifrost.synthetic-canonical-source-probe/v1",
    "procedure": "mtg-engineering-flow/2026-09-30.1",
    "evidence": "supported-Docker synthetic source-helper execution only",
    "platform_ref": PLATFORM_REF,
    "mounted_foundation_ref": FOUNDATION_REF,
    "workspace_ref": WORKSPACE_REF,
    "image": IMAGE,
    "python": platform.python_version(),
    "isolation": {
        "network": "none",
        "mounts": "readonly source/shared/SDK/probe/authored text",
        "authored_workflow_import": False,
        "database_or_workload_execution_called": False,
        "observer": "delegating json.dumps trace where helper returns only digest/ciphertext",
    },
    "versions": {},
    "sources": {},
    "vectors": [],
    "observations": [],
    "barriers": [],
}


class ForbiddenProbeEffect(RuntimeError):
    pass


def audit(event, _args):
    if event in {
        "socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
        "socket.gethostbyaddr", "subprocess.Popen", "os.system", "os.exec",
        "os.posix_spawn", "os.fork", "os.forkpty",
    }:
        raise ForbiddenProbeEffect(event)


sys.addaudithook(audit)
sys.dont_write_bytecode = True


def sha(data):
    return hashlib.sha256(data).hexdigest()


def describe(value):
    if value is None:
        return {"kind": "null"}
    if type(value) is bool:
        return {"kind": "bool", "value": value}
    if type(value) is int:
        return {"kind": "int", "decimal": str(value)}
    if type(value) is float:
        return {"kind": "f64", "repr": repr(value), "bits_be_hex": struct.pack(">d", value).hex()}
    if isinstance(value, str):
        return {"kind": "string", "value": value}
    if isinstance(value, dict):
        return {"kind": "object", "entries": [[key, describe(item)] for key, item in value.items()]}
    if isinstance(value, (list, tuple)):
        return {"kind": "array", "items": [describe(item) for item in value]}
    if hasattr(value, "model_dump"):
        return {"kind": "model", "class": type(value).__name__, "model_dump_json": describe(value.model_dump(mode="json"))}
    raise TypeError("unsupported synthetic input")


def source(function):
    path = Path(inspect.getsourcefile(function)).resolve(strict=True)
    label = str(path.relative_to(SOURCE_ROOT))
    if sha(path.read_bytes()) != EXPECTED_SOURCES.get(label):
        raise RuntimeError("producer source differs from pin")
    ARTIFACT["sources"][label] = {"ref": PLATFORM_REF, "sha256": sha(path.read_bytes())}
    return {"path": label, "function": function.__name__, "line": inspect.getsourcelines(function)[1], "ref": PLATFORM_REF}


def barrier(profile, phase, exc):
    ARTIFACT["barriers"].append({"profile": profile, "phase": phase, "exception_type": type(exc).__name__})


def load(name, profile):
    try:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve(strict=True)
        expected_path = SOURCE_ROOT / (name.replace(".", "/") + ".py")
        if path != expected_path or sha(path.read_bytes()) != EXPECTED_SOURCES.get(str(path.relative_to(SOURCE_ROOT))):
            raise RuntimeError("source module differs from pinned mounted producer")
        ARTIFACT["sources"][str(path.relative_to(SOURCE_ROOT))] = {"ref": PLATFORM_REF, "sha256": sha(path.read_bytes())}
        return module
    except Exception as exc:
        barrier(profile, "import", exc)
        return None


def record(profile, name, function, value, *, direct_bytes=False, digest=False, expect_failure=False, call=None):
    entry = {"profile": profile, "name": name, "producer": source(function), "input": describe(value), "fixture": "synthetic"}
    captures = []

    def observed_dumps(*args, **kwargs):
        result = ORIGINAL_DUMPS(*args, **kwargs)
        captures.append({"bytes": result.encode("utf-8"), "input": describe(args[0]), "options": kwargs})
        return result

    try:
        if direct_bytes:
            raw = function(value)
        else:
            json.dumps = observed_dumps
            try:
                returned = call() if call else function(value)
            finally:
                json.dumps = ORIGINAL_DUMPS
            if len(captures) != 1:
                raise RuntimeError("unexpected serializer call count")
            raw = captures[0]["bytes"]
            entry["serialized_input"] = captures[0]["input"]
            entry["dump_options"] = captures[0]["options"]
            if digest:
                if returned != "sha256:" + sha(raw):
                    raise RuntimeError("helper digest differs from captured bytes")
                entry["helper_digest"] = returned
        entry.update({"outcome": "encoded", "utf8": raw.decode("utf-8"), "hex": raw.hex(), "sha256": sha(raw), "digest": "sha256:" + sha(raw)})
        if expect_failure:
            entry["expected_failure_observed"] = False
    except Exception as exc:
        json.dumps = ORIGINAL_DUMPS
        entry.update({"outcome": "exception", "exception_type": type(exc).__name__})
        if expect_failure:
            entry["expected_failure_observed"] = True
        else:
            barrier(profile, "helper", exc)
    ARTIFACT["vectors"].append(entry)


if {name for name in os.environ if name.startswith("BIFROST_")} != set(EXPECTED_ENVIRONMENT):
    raise RuntimeError("probe requires only the documented synthetic BIFROST inputs")
if any(os.environ.get(name) != value for name, value in EXPECTED_ENVIRONMENT.items()):
    raise RuntimeError("probe input differs from the documented synthetic fixture")
if platform.python_version() != EXPECTED_PYTHON:
    raise RuntimeError("probe Python version differs from capture")
for package, expected in EXPECTED_VERSIONS.items():
    observed = importlib.metadata.version(package)
    if observed != expected:
        raise RuntimeError("probe package version differs from capture")
    ARTIFACT["versions"][package] = observed
for label, expected in EXPECTED_SOURCES.items():
    if sha((SOURCE_ROOT / label).read_bytes()) != expected:
        raise RuntimeError("mounted producer source differs from pin")
authored = AUTHORED_SOURCE.read_bytes()
if sha(authored) != SOURCE_HASH:
    raise RuntimeError("authored source differs from pin")
sys.path.insert(0, str(SOURCE_ROOT))

manifest = load("src.services.solutions.deployment_manifest", "deployment")
workspace = load("bifrost.workspace_release", "workspace")
review = load("bifrost.solution_delivery_review", "selected-A-model")

numbers = [
    ("integer-zero", 0), ("float-zero", 0.0), ("float-negative-zero", -0.0),
    ("integer-one", 1), ("float-one", 1.0), ("float-1e-4", 1e-4),
    ("float-1e-5", 1e-5), ("float-1e-6", 1e-6), ("float-1e-7", 1e-7),
    ("float-1e15", 1e15), ("float-1e16", 1e16), ("float-1e20", 1e20),
    ("negative-1e-5", -1e-5), ("smallest-subnormal", float.fromhex("0x0.0000000000001p-1022")),
    ("largest-finite", float.fromhex("0x1.fffffffffffffp+1023")),
    ("roundtrip", float.fromhex("0x1.0000000000001p+0")),
]
objects = [
    ("null-present", {"startup": None, "git_commit_sha": None, "workflow_value": 0.0}),
    ("null-absent", {"workflow_value": 0.0}),
    ("nested-order", {"z": 2, "a": {"z": 1, "a": None}, "ordered": [2, 1]}),
    ("unicode-and-escapes", {"😀": "astral", "\ue000": "private", "é": "precomposed", "a": 'quote" slash\\ line\n\t\u0000 \u2028\u2029'}),
    ("unicode-composed", {"name": "é"}), ("unicode-decomposed", {"name": "e\u0301"}),
]
if manifest:
    for name, value in numbers:
        record("PythonDeploymentEvidenceV1", name, manifest.canonical_json, {"workflow_value": value}, direct_bytes=True)
    for name, value in objects:
        record("PythonDeploymentEvidenceV1", name, manifest.canonical_json, value, direct_bytes=True)
    for name, value in (("nan", float("nan")), ("positive-infinity", float("inf")), ("negative-infinity", float("-inf"))):
        record("PythonDeploymentEvidenceV1", name, manifest.canonical_json, {"workflow_value": value}, direct_bytes=True, expect_failure=True)
    uuid1, uuid2 = UUID(int=1), UUID(int=2)
    entity = manifest.RuntimeEntityDefinition(portable_ref="synthetic.py::run", resolved_id=UUID(int=3), definition={"nested": {"default": None}}, source_ref=None, source_hash=None)
    model = manifest.CompiledDeploymentManifest(solution_id=uuid1, deployment_id=uuid2, bundle_hash="sha256:" + "a" * 64, resolution_map_hash="sha256:" + "b" * 64, source=manifest.DeploymentSource(artifact_key="synthetic.zip", runtime_prefix="synthetic/"), workflows={entity.portable_ref: entity})
    record("PythonDeploymentDocumentV1", "model-none-and-nested-null", manifest.canonical_json, model, direct_bytes=True)
    explicit_empty = manifest.CompiledDeploymentManifest.model_validate({**model.model_dump(mode="json"), "root_file_bindings": {}, "resources": {}, "shared_tables": {}})
    record("PythonDeploymentDocumentV1", "model-explicit-empty", manifest.canonical_json, explicit_empty, direct_bytes=True)

if workspace:
    for name, value in numbers:
        record("PythonWorkspaceIdentityV1", name, workspace.canonical_digest, {"workflow_value": value}, digest=True)
    for name, value in objects:
        record("PythonWorkspaceIdentityV1", name, workspace.canonical_digest, value, digest=True)
    for name, value in (("nan", float("nan")), ("positive-infinity", float("inf")), ("negative-infinity", float("-inf"))):
        record("PythonWorkspaceIdentityV1", name, workspace.canonical_digest, {"workflow_value": value}, digest=True)
    record("PythonWorkspaceManifestV1", "prefixed-leaf-normalization", workspace.workspace_manifest_id, {"features/demo.py": "sha256:" + "b" * 64, "modules/helper.py": "c" * 64}, digest=True)

if manifest and review:
    try:
        for name, kwargs in (("controls-default", {}), ("controls-explicit-int-zero", {"value": 0}), ("controls-explicit-float-zero", {"value": 0.0}), ("controls-negative-zero", {"value": -0.0}), ("controls-1e-5", {"value": 1e-5})):
            controls = review.WorkflowRegistrationControls(**kwargs)
            ARTIFACT["observations"].append({"name": name, "value_type": type(controls.value).__name__, "value": describe(controls.value), "model_dump_json": describe(controls.model_dump(mode="json")), "fixture": "synthetic model construction"})
            record("PythonDeploymentDocumentV1", name, manifest.canonical_json, controls, direct_bytes=True)
        ARTIFACT["sources"][SOURCE_PATH] = {"ref": WORKSPACE_REF, "sha256": SOURCE_HASH, "use": "read as bytes and AST only; never imported"}
        recipe = review.ReviewedWorkflowRecipe.model_validate({"schema_version": review.WORKFLOW_RECIPE_SCHEMA, "solution_id": str(UUID(int=1)), "files": {SOURCE_PATH: SOURCE_PATH}, "workflows": [{"id": str(UUID(int=3)), "path": SOURCE_PATH, "function_name": "check_integration_readiness", "organization_id": str(UUID(int=4)), "runtime_bounds": {"max_duration_seconds": 60, "max_external_calls": 10, "max_records_read": 100, "max_output_bytes": 4096}, "controls": {"timeout_seconds": 60, "role_ids": [str(UUID(int=5))], "endpoint_enabled": False, "public_endpoint": False}}]})
        record("PythonDeploymentDocumentV1", "selected-A-shaped-recipe", manifest.canonical_json, recipe, direct_bytes=True)
        compiled = review.compile_workflow_registrations(recipe, {SOURCE_PATH: authored})
        ARTIFACT["sources"]["bifrost/solution_delivery_review.py"]["compiler"] = source(review.compile_workflow_registrations)
        selected = next(iter(compiled.values()))
        definition = selected.definition
        ARTIFACT["observations"].append({"name": "selected-A-AST-compiled-value", "value": describe(definition["value"]), "fixture": "synthetic fixed UUID recipe / actual pinned authored bytes / AST compiler, not installed A"})
        record("PythonDeploymentEvidenceV1", "selected-A-compiled-definition", manifest.canonical_json, definition, direct_bytes=True)
        runtime = load("src.services.solutions.deployment_runtime", "selected-A-queue-helper")
        if runtime:
            pinned = runtime.PinnedWorkflowRuntime(workflow_id=UUID(int=3), solution_id=UUID(int=1), deployment_id=UUID(int=2), bundle_hash="sha256:" + "a" * 64, compiled_manifest_hash="sha256:" + "b" * 64, git_commit_sha=WORKSPACE_REF, runtime_storage_prefix="_solutions/00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002/", portable_ref=selected.portable_ref, name=definition["name"], function_name=definition["function_name"], path=definition["path"], source_hash="sha256:" + SOURCE_HASH, timeout_seconds=int(definition["timeout_seconds"]), time_saved=int(definition["time_saved"]), value=float(definition["value"]), execution_mode=definition["execution_mode"], workflow_type=definition["type"], cache_ttl_seconds=int(definition["cache_ttl_seconds"]), organization_id=str(UUID(int=4)), can_access_global_repo=False, source_hashes={SOURCE_PATH: "sha256:" + SOURCE_HASH}, runtime_bounds=definition["runtime_bounds"], parameters_schema=definition["parameters_schema"])
            evidence = pinned.queue_evidence()
            ARTIFACT["observations"].append({"name": "selected-A-synthetic-queue-helper", "queue_producer": source(runtime.PinnedWorkflowRuntime.queue_evidence), "value": describe(evidence["workflow_value"]), "fixture": "constructed dataclass from compiled definition; DB pin resolver/install NOT invoked; bundle/manifest hashes synthetic"})
            record("PythonDeploymentEvidenceV1", "selected-A-synthetic-queue-evidence", manifest.canonical_json, evidence, direct_bytes=True)
    except Exception as exc:
        barrier("selected-A", "model-or-AST-helper", exc)

attempts = load("src.services.execution.attempts", "attempt-policy")
if attempts:
    execution = SimpleNamespace(runtime_mode="deployment-v1", retry_policy={"version": "execution-retry/v1", "enabled": False, "max_attempts": 2, "retry_on": []})
    record("PythonAttemptPolicyV1", "selected-A-shaped-policy", attempts._policy_digest, {"runtime_mode": execution.runtime_mode, "retry_policy": execution.retry_policy}, digest=True, call=lambda: attempts._policy_digest(execution))
    unicode_execution = SimpleNamespace(runtime_mode="synthetic-é-😀", retry_policy=execution.retry_policy)
    record("PythonAttemptPolicyV1", "synthetic-ASCII-escaping", attempts._policy_digest, {"runtime_mode": unicode_execution.runtime_mode, "retry_policy": unicode_execution.retry_policy}, digest=True, call=lambda: attempts._policy_digest(unicode_execution))

delivery = load("src.services.work_delivery_store", "delivery-plaintext")
if delivery:
    value = {"body": {"z": "é😀", "a": None, "number": 1e-5}, "headers": {"synthetic": True}}
    record("PythonDeliveryPlaintextV1", "synthetic-envelope", delivery._encrypted, value)

ARTIFACT["vector_count"] = len(ARTIFACT["vectors"])
ARTIFACT["barrier_count"] = len(ARTIFACT["barriers"])
print(ORIGINAL_DUMPS(ARTIFACT, indent=2, ensure_ascii=True, allow_nan=False))
sys.exit(1 if ARTIFACT["barriers"] else 0)
