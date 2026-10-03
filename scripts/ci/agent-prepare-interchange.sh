#!/usr/bin/env bash
# Supported hosted CI only; never source/execute on the physical Proxmox host.
set -euo pipefail
exec python3 - "${1:?guard, pre-pr, prepare, format, verify or cleanup required}" <<'PY'
import hashlib
import json
import math
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import contextmanager, suppress
from pathlib import Path

EXPECTED_SOURCE = {
    "api/Dockerfile.dev": "54c271f4dd2f95c2b1644c7a7cce11aa5f21ca47126b8afe8ee2dff1c27bca2c",
    "api/_bifrost_workspace_effects.py": "c5ee9048f68315255ec8bdc20062df25dcf36916f3f4537c70565f158007f6ca",
    "api/entrypoint.sh": "d638f919a0b11e43cdac1c0c82678258f08811a659557bf78e90955b490167f7",
    "api/pytest.ini": "e2bf689a6604eddc5a6da276553dcdb84305f507c9da095e153f9bba049a3ff7",
    "api/scripts/check_github_action_pins.py": "373ad3a39139688b4080dc354e862b0191c916b0dc297dc35ca0b21daf23017b",
    "api/scripts/ci/prepare-test-images.sh": "a8a2fc0561ea470152998f2ccd87f1c9e07b816fc51505a899289de8b7092600",
    "api/scripts/init_container.py": "2bb87e240a50f0eba7c2e45ad108ebf7c72b54b575e97fa74eceb458ed2bab4c",
    "api/scripts/plan_affected_tests.py": "731caad3a659a9e0e47b88cb92cc9cf03f44122e0921d2525dcb7f2a5f3e009d",
    "api/scripts/quality_api.sh": "b7b83142ccceacbb846777e77875dd66c7f61da8c0303a18ddbcf75f9bc21084",
    "api/src/__init__.py": "880b63790f67030db2574c4d14bd4abf48a10c90538854c5a3b3aaa8e22442da",
    "api/src/runtime_protocol/__init__.py": "924a3683d7bf3f4f607c2d107fe1bc3a41f33885a098f0bc34ce26be6fb02041",
    "api/src/runtime_protocol/agent_prepare.py": "5af216a231e0056a7094928fa77ac6690a735775a54128c05941a63dc78322cd",
    "api/src/runtime_protocol/control.py": "77a54873539c5116a8645e6db0f74ed403529626b842b1654ef52bc13b5f2a30",
    "api/src/runtime_protocol/session.py": "a3f116706976d88c65a334377bf11f388c766b607108787bdb9a37191eeb7885",
    "api/tests/__init__.py": "9b8cec3603c47a22d0993d4a25ec288e23f34267510d11eb0dae3ab3a8a20a2e",
    "api/tests/runtime_protocol/__init__.py": "cf48305defe6b8f03cee2b6cd524aa650971e08420540e43b6dc96ae373819a2",
    "api/tests/runtime_protocol/agent_prepare_interchange.py": "2ad8d0c680c2204cb46e82641cdca66ba8416cb4d0cc65eeb0208b1b54bcb65d",
    "api/tests/runtime_protocol/interchange.py": "5d13a406929264de525eeb34c1b89d4050b7ba59297e17b436b49b87bf6a5575",
    "api/tests/runtime_protocol/test_agent_prepare.py": "70cb70c83ef191517bad25adf9fd056a508768ec329eb830d120c1468c1a16bb",
    "api/tests/runtime_protocol/test_control.py": "d9aca2dd1c52fbe7caa7d27d1c0bc2ef1768690629d24802bd5b6d28f965aeba",
    "core-rs/Cargo.lock": "7e765dc50da514ff62a210a67aa9e8ded6166b67759179187264a313568b3869",
    "core-rs/Cargo.toml": "4e22240845140ac0b22e8b17eb51147461048982f989d53616f6c58d421e2232",
    "core-rs/Dockerfile": "a42c446bb8c67462ad6afefdc1aa6bdde4a7d241ad4d1122c26af9e5cfe9270b",
    "core-rs/crates/bifrost-contracts/Cargo.toml": "3ad2b989985158f1bfbbe969dfdb932bca96ac0e76dfc66b7c4d27daa39d3fd3",
    "core-rs/crates/bifrost-contracts/examples/runtime_agent_prepare_vectors.rs": "d0b31ac83a12184fca40622471416ded013bd292ec07fd361721edf4cbc909c9",
    "core-rs/crates/bifrost-contracts/examples/runtime_control_vectors.rs": "0f6bfae7d6131014229d0f5867088e3c5255915b88e6ef2ecc398458c9c74d05",
    "core-rs/crates/bifrost-contracts/src/lib.rs": "35b8f1a85351a543df83abde2c3d13b1a759f0fc9730a2b4d874bb063f660d0f",
    "core-rs/crates/bifrost-contracts/src/runtime/agent_prepare.rs": "17565e241a262944664229420b3cd6a9614a75e8120066d942bcc359e941c6d4",
    "core-rs/crates/bifrost-contracts/src/runtime/agent_prepare_tests.rs": "4740743188f5229bb8dd035f607b1cb872767b3fc97ec86943f3d30a3c18bb69",
    "core-rs/crates/bifrost-contracts/src/runtime/codec.rs": "a921ec1c8a1e3ecf30e58266d3ce4bb6b385227e5ba724e3f76dddad77f9f843",
    "core-rs/crates/bifrost-contracts/src/runtime/control.rs": "33ee2987bdb5ff507afbd4a1a0b95681cd9b322be965a530145f68b788497243",
    "core-rs/crates/bifrost-contracts/src/runtime/mod.rs": "6dab977ea4a6c381f2002f3cd4b3bf55e15a83f4dca6f6b85c53a2a0285233fc",
    "core-rs/crates/bifrost-contracts/src/runtime/session.rs": "53965be75561bd5ed76ca366d6ca1a826411e66559d212d2cc4c53dfe9406a8e",
    "core-rs/crates/bifrost-contracts/src/runtime/tests.rs": "642808c15c0085fdabf9c65fbdcf341b6e6256881788e014375608f35cb551a1",
    "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/agent-prepare-vectors.json": "c8606adbc81e813c6c8f16d0d7226d95628c9c392052a57445a8675bff8e3480",
    "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json": "a5501a4fde472ebbae9567803e3b96313672ccfbad41fc8165030cbffd1786e6",
    "core-rs/crates/bifrost-core/Cargo.toml": "fb750efc8754a77cdd8de6aacca1ddf04b8004b4a7d86d3bd1d6eae5e3c84715",
    "core-rs/crates/bifrost-db/Cargo.toml": "817ae780e6345d98603934d8dfc4f1b772e72652ed71bf06ed94ab648c216cff",
    "core-rs/crates/bifrost-domain/Cargo.toml": "915b43efc797fe1eeb688bfba0b5e7ef9f6d758343972feba61c07a10bd8fcdd",
    "docker-compose.test.yml": "ea09ba47a32e6de364d66df34991984be6138bc85e7a68f8b79c94c72dd06052",
    "pyproject.toml": "7fac5222b03a31882095b894f4dc1b5de4ba6cd51a272f9cb0db354770feb05a",
    "requirements-pyright.lock": "464362c56fc226e0034621fb15b2237cf287de9c3d7c787417873abebc35b39b",
    "requirements.lock": "be76160e9eb3b3ba9ab0d9af9e77f311db3578cfaf02b7042d9697bd7c2feea1",
    "scripts/ci/detect-test-image-inputs.sh": "a40a10b9958751a7db99c423a1a595ba4846cb0bb9ccc16246cbab9cedbc66c8",
    "scripts/ci/workflow-sql-source.sh": "87da2b469e242251df9d57c4483a8e81fa784db5b3e04b216a7fa743f5d8f1eb",
    "scripts/lib/pre_pr_stage_evidence.py": "60da3f4c3e95cb220738d077c9dec59451bb66acde732b71067e8d44b9f5de11",
    "scripts/lib/test_helpers.sh": "c8328009720ceecbe5754d3934ce473e9dff894305c262bed6c3fd4d6f74555d",
    "scripts/stack_template_init.sh": "fc9c7d104dd82cf7ffdff9f6827674ee100aad32e94cd3d0cc866617e2972bb5",
    "test.sh": "19d75479975fcdfed46ce840081336d74db366f8ad008ca6aed9f1ce0f927cfb",
}

SOURCE_KEYS = [
    "core-rs/Dockerfile",
    "core-rs/Cargo.toml",
    "core-rs/Cargo.lock",
    "core-rs/crates/bifrost-contracts/Cargo.toml",
    "core-rs/crates/bifrost-contracts/src/lib.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/mod.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/codec.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/agent_prepare.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/agent_prepare_tests.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/control.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/session.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/tests.rs",
    "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json",
    "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/agent-prepare-vectors.json",
    "core-rs/crates/bifrost-contracts/examples/runtime_control_vectors.rs",
    "core-rs/crates/bifrost-contracts/examples/runtime_agent_prepare_vectors.rs",
    "core-rs/crates/bifrost-domain/Cargo.toml",
    "core-rs/crates/bifrost-db/Cargo.toml",
    "core-rs/crates/bifrost-core/Cargo.toml",
    "api/src/__init__.py",
    "api/src/runtime_protocol/__init__.py",
    "api/src/runtime_protocol/control.py",
    "api/src/runtime_protocol/session.py",
    "api/src/runtime_protocol/agent_prepare.py",
    "api/tests/__init__.py",
    "api/tests/runtime_protocol/__init__.py",
    "api/tests/runtime_protocol/test_control.py",
    "api/tests/runtime_protocol/test_agent_prepare.py",
    "api/tests/runtime_protocol/interchange.py",
    "api/tests/runtime_protocol/agent_prepare_interchange.py",
    "api/pytest.ini",
    "api/Dockerfile.dev",
    "api/entrypoint.sh",
    "api/_bifrost_workspace_effects.py",
    "pyproject.toml",
    "requirements.lock",
    "requirements-pyright.lock",
    "test.sh",
    "docker-compose.test.yml",
    "scripts/lib/test_helpers.sh",
    "scripts/lib/pre_pr_stage_evidence.py",
    "scripts/stack_template_init.sh",
    "scripts/ci/workflow-sql-source.sh",
    "api/scripts/check_github_action_pins.py",
    "api/scripts/plan_affected_tests.py",
    "api/scripts/quality_api.sh",
    "api/scripts/init_container.py",
    "api/scripts/ci/prepare-test-images.sh",
    "scripts/ci/detect-test-image-inputs.sh",
    "scripts/ci/agent-prepare-interchange.sh",
    ".github/workflows/agent-prepare.yml",
]

FROZEN_CORPORA = {
    "agent": {
        "direct_test_roster_sha256": "b2eca968e67705eb8ee551ef341bcf191ee8e88ba50968c2bb5f84a23f1ae6f3",
        "fixture_bytes": 914866,
        "fixture_sha256": "c8606adbc81e813c6c8f16d0d7226d95628c9c392052a57445a8675bff8e3480",
        "positive": [
            "hello",
            "prepare-parent",
            "prepared-unexpected-observations",
            "prepare-solution_deployment",
            "solution-optionals-present",
            "prepare-workspace_release",
            "caller-null",
            "raw-float-spellings",
            "raw-whitespace",
            "magic-adjacent",
            "retained-reordered-prepare",
            "fresh-business-map-extra-key",
        ],
        "wire": [
            "hello",
            "prepare-parent",
            "prepared-unexpected-observations",
            "prepare-solution_deployment",
            "solution-optionals-present",
            "prepare-workspace_release",
            "caller-null",
            "image-null-mismatch",
            "slot-order",
            "entry-duplicate",
            "namespace-dangling",
            "session-mismatch",
            "agent-mismatch",
            "run-mismatch",
            "evidence-mismatch",
            "bool-integer",
            "negative-count",
            "type-mismatch",
            "raw-float-spellings",
            "raw-whitespace",
            "magic-adjacent",
            "integer-token-9007199254740992",
            "integer-token--9007199254740992",
            "integer-token--0",
            "integer-token-1e0",
            "integer-token-1.0",
            "integer-token-NaN",
            "integer-token-1e9999",
            "duplicate-root",
            "duplicate-nested",
            "unknown-frame",
            "protocol-before-sequence",
            "protocol-non-string",
            "invalid-json",
            "empty-json",
            "trailing-json",
            "surrogate",
            "structure-1-unknown-parent",
            "structure-1-missing-parent",
            "structure-2-unknown-parent",
            "structure-2-missing-parent",
            "structure-3-unknown-parent",
            "structure-3-missing-parent",
            "structure-4-unknown-parent",
            "structure-4-missing-parent",
            "structure-5-unknown-parent",
            "structure-5-missing-parent",
            "structure-6-unknown-parent",
            "structure-6-missing-parent",
            "structure-7-unknown-parent",
            "structure-7-missing-parent",
            "structure-8-unknown-parent",
            "structure-8-missing-parent",
            "structure-9-unknown-parent",
            "structure-9-missing-parent",
            "structure-10-unknown-parent",
            "structure-10-missing-parent",
            "structure-11-unknown-parent",
            "structure-11-missing-parent",
            "structure-12-unknown-parent",
            "structure-12-missing-parent",
            "structure-13-unknown-parent",
            "structure-13-missing-parent",
            "structure-14-unknown-parent",
            "structure-14-missing-parent",
            "structure-15-unknown-parent",
            "structure-15-missing-parent",
            "structure-16-unknown-parent",
            "structure-16-missing-parent",
            "structure-17-unknown-parent",
            "structure-17-missing-parent",
            "structure-18-unknown-parent",
            "structure-18-missing-parent",
            "structure-19-unknown-parent",
            "structure-19-missing-parent",
            "structure-20-unknown-parent",
            "structure-20-missing-parent",
            "structure-21-unknown-parent",
            "structure-21-missing-parent",
            "structure-22-unknown-parent",
            "structure-22-missing-parent",
            "structure-23-unknown-parent",
            "structure-23-missing-parent",
            "structure-24-unknown-solution",
            "structure-24-missing-solution",
            "structure-25-unknown-workspace",
            "structure-25-missing-workspace",
            "structure-26-unknown-workspace",
            "structure-26-missing-workspace",
            "structure-27-unknown-prepared",
            "structure-27-missing-prepared",
            "structure-28-unknown-prepared",
            "structure-28-missing-prepared",
            "structure-29-unknown-prepared",
            "structure-29-missing-prepared",
            "structure-30-unknown-prepared",
            "structure-30-missing-prepared",
            "structure-31-unknown-prepared",
            "structure-31-missing-prepared",
            "structure-32-unknown-prepared",
            "structure-32-missing-prepared",
            "structure-33-unknown-prepared",
            "structure-33-missing-prepared",
            "structure-34-unknown-prepared",
            "structure-34-missing-prepared",
            "workspace-evidence-unknown",
            "workspace-evidence-missing",
            "closed-prepare-facts-duplicate",
            "closed-prepare-facts-type",
            "closed-prepare-facts-nonnull",
            "closed-agent-binding-duplicate",
            "closed-agent-binding-type",
            "closed-agent-binding-nonnull",
            "closed-agent-logical-duplicate",
            "closed-agent-logical-type",
            "closed-agent-logical-nonnull",
            "closed-agent-attempt-duplicate",
            "closed-agent-attempt-type",
            "closed-agent-attempt-nonnull",
            "closed-source-baseline-duplicate",
            "closed-source-baseline-type",
            "closed-source-baseline-nonnull",
            "closed-staged-closure-duplicate",
            "closed-staged-closure-type",
            "closed-staged-closure-nonnull",
            "closed-entrypoint-facts-duplicate",
            "closed-entrypoint-facts-type",
            "closed-entrypoint-facts-nonnull",
            "closed-parent-agent-evidence-duplicate",
            "closed-parent-agent-evidence-type",
            "closed-parent-agent-evidence-nonnull",
            "closed-staged-entry-duplicate",
            "closed-staged-entry-type",
            "closed-staged-entry-nonnull",
            "closed-expected-namespace-duplicate",
            "closed-expected-namespace-type",
            "closed-expected-namespace-nonnull",
            "closed-prepare-artifact-duplicate",
            "closed-prepare-artifact-type",
            "closed-prepare-artifact-nonnull",
            "closed-prepare-interpreter-duplicate",
            "closed-prepare-interpreter-type",
            "closed-prepare-interpreter-nonnull",
            "closed-prepare-sdk-duplicate",
            "closed-prepare-sdk-type",
            "closed-prepare-sdk-nonnull",
            "closed-admission-facts-duplicate",
            "closed-admission-facts-type",
            "closed-admission-facts-nonnull",
            "closed-caller-facts-duplicate",
            "closed-caller-facts-type",
            "closed-caller-facts-nonnull",
            "closed-effective-facts-duplicate",
            "closed-effective-facts-type",
            "closed-effective-facts-nonnull",
            "closed-limit-facts-duplicate",
            "closed-limit-facts-type",
            "closed-limit-facts-nonnull",
            "closed-agent-facts-duplicate",
            "closed-agent-facts-type",
            "closed-agent-facts-nonnull",
            "closed-prompt-facts-duplicate",
            "closed-prompt-facts-type",
            "closed-prompt-facts-nonnull",
            "closed-tool-facts-duplicate",
            "closed-tool-facts-type",
            "closed-tool-facts-nonnull",
            "closed-model-facts-duplicate",
            "closed-model-facts-type",
            "closed-model-facts-nonnull",
            "closed-solution-evidence-duplicate",
            "closed-solution-evidence-type",
            "closed-solution-evidence-nonnull",
            "closed-workspace-evidence-duplicate",
            "closed-workspace-evidence-type",
            "closed-workspace-evidence-nonnull",
            "closed-prepared-facts-duplicate",
            "closed-prepared-facts-type",
            "closed-prepared-facts-nonnull",
            "closed-observation-facts-duplicate",
            "closed-observation-facts-type",
            "closed-observation-facts-nonnull",
            "closed-observed-entry-duplicate",
            "closed-observed-entry-type",
            "closed-observed-entry-nonnull",
            "closed-observed-namespace-duplicate",
            "closed-observed-namespace-type",
            "closed-observed-namespace-nonnull",
            "closed-observed-artifact-duplicate",
            "closed-observed-artifact-type",
            "closed-observed-artifact-nonnull",
            "closed-observed-file-duplicate",
            "closed-observed-file-type",
            "closed-observed-file-nonnull",
            "closed-observed-package-duplicate",
            "closed-observed-package-type",
            "closed-observed-package-nonnull",
            "closed-observed-startup-duplicate",
            "closed-observed-startup-type",
            "closed-observed-startup-nonnull",
            "model-chain-empty",
            "model-chain-repeated-profile",
            "solution-optional-schema-null",
            "solution-optional-bounds-null",
            "admission-optional-verified-roles-null",
            "retained-reordered-prepare",
            "fresh-business-map-extra-key",
        ],
    },
    "control": {
        "binary": [
            "empty-stream",
            "truncated-prefix",
            "zero-length",
            "oversize-prefix",
            "truncated-payload",
            "partial-read-hello",
            "partial-read-start",
        ],
        "fixture_sha256": "a5501a4fde472ebbae9567803e3b96313672ccfbad41fc8165030cbffd1786e6",
        "sessions": [
            "valid-workflow-start-stop",
            "valid-agent-start-stop",
            "cancel-after-start",
            "in-flight-heartbeat-after-cancel",
            "prepared-heartbeat-and-rejection",
            "missing-start-authorization",
            "early-authorization",
            "start-before-hello",
            "executing-before-start",
            "wrong-process",
            "wrong-session",
            "wrong-direction",
            "wrong-artifact",
            "unknown-capability",
            "unknown-negotiated-version",
            "wrong-preparation",
            "wrong-commit",
            "wrong-duration",
            "duplicate-start-no-replay",
            "sequence-gap",
            "stale-start-heartbeat",
            "monotonic-regression",
            "wrong-stop-identity",
            "stop-completion-before-start",
            "reports-after-stop",
            "no-repeated-authorization",
            "typed-attempt-mismatch",
            "empty-process-binding",
            "launch-digest-not-child-proof",
            "queued-prepared-heartbeat-after-start",
            "queued-prepared-heartbeat-after-prestart-cancel",
            "queued-completed-stop-after-cancel",
            "queued-prepare-rejected-after-start",
            "no-completed-regression-after-cancel-ack",
            "no-executing-regression-after-cancel-ack",
        ],
        "wire": [
            "hello",
            "start",
            "prepared_heartbeat",
            "executing_heartbeat",
            "cancel",
            "cancelling_heartbeat",
            "completed",
            "cancelled",
            "prepare_rejected",
            "missing-envelope-protocol",
            "missing-envelope-type",
            "missing-envelope-session_id",
            "missing-envelope-message_id",
            "missing-envelope-sequence",
            "missing-envelope-correlation_id",
            "missing-envelope-body",
            "missing-Hello-runtime_incarnation_id",
            "missing-Hello-supported_protocols",
            "missing-Hello-capabilities",
            "missing-Hello-artifact",
            "missing-Start-prepare_message_id",
            "missing-Start-committed_start_id",
            "missing-Start-parent_duration_seconds",
            "missing-Heartbeat-start_message_id",
            "missing-Heartbeat-state",
            "missing-Heartbeat-monotonic_elapsed_ms",
            "missing-Cancel-cancel_id",
            "missing-Cancel-reason",
            "missing-Cancel-grace_ms",
            "missing-Stopped-start_message_id",
            "missing-Stopped-cancel_id",
            "missing-Stopped-reason",
            "missing-Stopped-result_message_id",
            "missing-Stopped-error",
            "missing-nested-artifact-image_digest",
            "missing-nested-artifact-requirements_lock_sha256",
            "missing-nested-artifact-sdk-distribution",
            "missing-nested-artifact-sdk-version",
            "missing-nested-error-traceback",
            "unknown-version",
            "version-not-string",
            "unknown-envelope-key",
            "unknown-body-key",
            "uppercase-uuid",
            "short-uuid",
            "uuid-non-hex",
            "bool-sequence",
            "float-sequence",
            "negative-sequence",
            "zero-sequence",
            "unsafe-sequence",
            "unexpected-correlation",
            "empty-artifact-id",
            "empty-sdk-version",
            "invalid-lock-hash",
            "missing-interpreter-version",
            "nested-unknown-key",
            "duplicate-capability",
            "unsupported-Prepare",
            "unsupported-Prepared",
            "unsupported-Result",
            "unsupported-LogBatch",
            "unsupported-ModelObservation",
            "unsupported-WorkflowToolRequested",
            "unsupported-ToolOutcome",
            "unsupported-ToolObservation",
            "unsupported-Usage",
            "unsupported-ExecutePython",
            "unsupported-empty",
            "bad-start-parent_duration_seconds-True",
            "bad-start-parent_duration_seconds-0",
            "bad-start-parent_duration_seconds-1.0",
            "bad-start-prepare_message_id-None",
            "start-without-correlation",
            "bool-heartbeat",
            "unknown-heartbeat-state",
            "coordinator-loss-as-cancel",
            "duplicate-envelope",
            "duplicate-nested",
            "nan",
            "infinity",
            "lone-surrogate",
            "trailing-json",
            "not-json",
            "empty-json",
            "depth-64",
            "depth-65",
            "invalid-utf8",
            "negative-zero-heartbeat",
            "negative-zero-cancel-grace",
            "negative-zero-sequence",
            "large-positive-finite-heartbeat",
            "large-negative-finite-heartbeat",
            "large-positive-overflow-heartbeat",
            "large-negative-overflow-heartbeat",
            "large-positive-finite-sequence",
            "large-negative-finite-sequence",
            "large-positive-overflow-sequence",
            "large-negative-overflow-sequence",
            "rawvalue-hidden-start",
            "rawvalue-hidden-start-duplicate",
            "rawvalue-hidden-start-depth",
            "rawvalue-hidden-hello-artifact",
            "rawvalue-hidden-stopped-error",
            "rawvalue-hidden-start-unsupported-protocol",
            "rawvalue-hidden-start-nonstring-protocol",
            "rawvalue-hidden-start-malformed-inner",
            "e0-unsupported-sequence-zero",
            "e0-unsupported-sequence-bool",
            "e0-unsupported-sequence-over-safe",
            "e0-unsupported-session-id",
            "e0-unsupported-message-id",
            "e0-unsupported-type-bool",
            "e0-unsupported-type-empty",
            "e0-unsupported-type-unknown",
            "e0-unsupported-correlation-id",
            "e0-unsupported-correlation-mismatch",
            "e0-unsupported-body",
            "e0-unsupported-body-fields",
            "e0-empty-protocol-sequence-zero",
            "e0-invalid-json-before-protocol",
            "e0-duplicate-before-protocol",
            "e0-surrogate-before-protocol",
            "e0-depth65-before-protocol",
            "e0-depth64-protocol-first",
            "e0-nonfinite-before-protocol",
            "e0-numeric-overflow-before-protocol",
            "e0-utf8-before-protocol",
            "e0-trailing-json-before-protocol",
            "e0-shape-missing-sequence",
            "e0-shape-missing-protocol",
            "e0-shape-extra-key",
            "e0-shape-root-array",
            "e0-protocol-bool",
            "e0-protocol-null",
            "e0-protocol-object",
            "e0-supported-unknown-type-bad-header",
            "e0-supported-unknown-type-bad-body",
            "e0-supported-bad-body",
        ],
    },
}

PYTHON_AGENT_FUNCTION_COUNTS = {
    "test_actual_binding_not_supplied_hash": 1,
    "test_actual_original_reemit_and_consumption": 3,
    "test_binding_rejects_distinct_actual_owners": 6,
    "test_byte_different_hashes": 1,
    "test_complete_wire_cap": 1,
    "test_depth": 3,
    "test_escaped_numeric_looking_string": 1,
    "test_every_nullable_and_optional_field_distinction": 1,
    "test_existing_control_rejects": 2,
    "test_fragmented_reader_and_partial_writer": 1,
    "test_framing_boundaries": 1,
    "test_fresh_typed_revalidates": 1,
    "test_initial_read_failure_stops_without_extra_reads": 5,
    "test_interrupted_read_continues_same_frame": 3,
    "test_interrupted_write_continues_same_output": 1,
    "test_mutable_input_is_copied_before_retained_ownership": 1,
    "test_new_positive_retained_and_fresh_custody": 2,
    "test_non_json": 2,
    "test_readonly_view_has_no_mutable_alias": 1,
    "test_required_fields_at_every_seeded_structural_depth": 1,
    "test_shared_corpus": 207,
    "test_source_optional_absence_preserved": 1,
}

RUST_CONTROL_TESTS = [
    "runtime::tests::shared_wire_vectors",
    "runtime::tests::shared_binary_vectors_with_partial_io",
    "runtime::tests::shared_parent_session_vectors",
    "runtime::tests::over_cap_payload_and_concatenated_frames",
    "runtime::tests::ordinary_json_retains_literal_private_looking_keys",
    "runtime::tests::ordinary_json_keeps_incumbent_structural_limits",
    "runtime::tests::protocol_precedence_preserves_size_and_depth_boundaries",
]

RUST_AGENT_TESTS = [
    "runtime::agent_prepare_tests::full_shared_corpus",
    "runtime::agent_prepare_tests::original_bytes_not_normalized",
    "runtime::agent_prepare_tests::byte_different_hashes",
    "runtime::agent_prepare_tests::fresh_encode_revalidates_opaque_floats",
    "runtime::agent_prepare_tests::binding_uses_retained_actual_hashes",
    "runtime::agent_prepare_tests::old_control_rejects_new_bodies",
    "runtime::agent_prepare_tests::missing_every_present_required_field",
    "runtime::agent_prepare_tests::optional_absence_survives_fresh_encoding",
    "runtime::agent_prepare_tests::invalid_utf8",
    "runtime::agent_prepare_tests::framing_empty_truncated_oversized",
    "runtime::agent_prepare_tests::direct_complete_frame_boundary",
    "runtime::agent_prepare_tests::depth_bound",
    "runtime::agent_prepare_tests::business_private_markers_remain_literal",
    "runtime::agent_prepare_tests::numeric_looking_strings_do_not_trip_preflight",
    "runtime::agent_prepare_tests::every_nullable_optional_field_distinction",
    "runtime::agent_prepare_tests::distinct_retained_binding_owners",
    "runtime::agent_prepare_tests::reordered_and_business_extension_fresh_retained",
]

RUST_WORKSPACE_TESTS = [
    "runtime::tests::shared_wire_vectors",
    "runtime::tests::shared_binary_vectors_with_partial_io",
    "runtime::tests::shared_parent_session_vectors",
    "runtime::tests::over_cap_payload_and_concatenated_frames",
    "runtime::tests::ordinary_json_retains_literal_private_looking_keys",
    "runtime::tests::ordinary_json_keeps_incumbent_structural_limits",
    "runtime::tests::protocol_precedence_preserves_size_and_depth_boundaries",
    "workflow::tests::running_covers_all_current_attempt_states_and_exact_columns",
    "workflow::tests::running_preserves_first_start_and_non_none_process_including_empty",
    "workflow::tests::running_rejects_absence_foreign_missing_wrong_and_completed_fences",
    "workflow::tests::missing_result_fence_precedes_every_logical_or_attempt_guard",
    "workflow::tests::tracked_result_checks_every_logical_state_before_attempt_and_outcome",
    "workflow::tests::all_ten_normalized_success_statuses_and_optional_zero_duration_are_preserved",
    "workflow::tests::result_has_no_new_attempt_status_or_phase_start_guard",
    "workflow::tests::result_rejects_exact_stale_fences_before_coordinator_policy",
    "workflow::tests::four_failure_mappings_assign_nullable_attempt_inputs_without_payload_clearing",
    "workflow::tests::coordinator_defers_running_but_cancelling_overrides_every_outcome",
    "workflow::tests::queued_cancel_has_exact_optional_active_attempt_plan",
    "workflow::tests::running_cancel_ignores_foreign_and_completed_attempts",
    "workflow::tests::cancel_checks_logical_identity_and_rejects_other_states_before_attempt",
    "workflow::tests::opaque_token_supports_clone_equality_without_secret_projection",
    "configuration_preserves_python_database_conventions",
    "invalid_configuration_fails_without_values",
    "liveness_and_database_readiness_are_distinct",
    "graceful_shutdown_stops_http_and_closes_database_pool",
    "graceful_shutdown_drains_an_inflight_readiness_request",
    "request_spans_do_not_record_caller_secrets",
]

OPERATIONS = [
    "source-guard",
    "literal-pre-pr",
    "action-disposal",
    "api-prepare",
    "ghcr-disposal",
    "toolchain-build",
    "cargo-fetch",
    "workspace-fmt",
    "workspace-clippy-all",
    "workspace-default-tests",
    "workspace-default-graph",
    "workspace-all-graph",
    "locked-inventory",
    "off-contracts-tests",
    "off-control-build",
    "off-graph",
    "off-rust-control-emit",
    "off-python-control-validate",
    "off-rust-control-validate",
    "raw-contracts-tests",
    "raw-control-build",
    "raw-graph",
    "raw-rust-control-emit",
    "raw-python-control-validate",
    "raw-rust-control-validate",
    "agent-contracts-tests",
    "agent-control-build",
    "agent-graph",
    "agent-rust-control-emit",
    "agent-python-control-validate",
    "agent-rust-control-validate",
    "off-python-control-emit",
    "raw-python-control-emit",
    "agent-python-control-emit",
    "agent-prepare-build",
    "python-control-tests",
    "python-agent-tests",
    "rust-agent-emit",
    "python-agent-validate",
    "python-agent-emit",
    "rust-agent-validate",
    "source-before",
    "source-after",
    "rust-snapshot-before",
    "rust-snapshot-after",
    "api-snapshot-before",
    "api-snapshot-after",
    "binary-readback",
    "container-custody-readback",
    "format-copy",
    "format-run",
    "format-patch",
    "owned-cleanup",
    "owned-readback",
    "safe-publication",
]

PRESERVED = {
    ".github/workflows/runtime-control.yml": "323283877ad99b509fa3fc916818d1696a6930c9669bd80182ee899ebc288319",
    "scripts/ci/runtime-control-interchange.sh": "8f4991c25a4c61ed78f9ce19d2db62704941f31816e5e0615e8af495ef111ad0",
}
FORMAT_PATHS = (
    "core-rs/crates/bifrost-contracts/src/runtime/agent_prepare.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/agent_prepare_tests.rs",
    "core-rs/crates/bifrost-contracts/examples/runtime_agent_prepare_vectors.rs",
    "core-rs/crates/bifrost-contracts/src/runtime/mod.rs",
)
CAPS = {
    "pre-pr": 360,
    "prepare": 240,
    "toolchain": 120,
    "fetch": 90,
    "checks": 400,
    "matrix": 180,
    "products": 180,
    "cleanup": 60,
}
STAGES = ("guard", *CAPS, "publication")
FORBIDDEN = {
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GHCR_TOKEN",
    "GHCR_USERNAME",
    "DOCKER_CONFIG",
    "BIFROST_ACTION_PIN_TOKEN_FILE",
    "SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY",
}
HELPER = "scripts/ci/workflow-sql-source.sh"
CONTROL_FIXTURE = "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json"
AGENT_FIXTURE = "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/agent-prepare-vectors.json"
RAW_LIMIT = 16 * 1024 * 1024
RAW_TOTAL = 64 * 1024 * 1024
EXCHANGE_LIMIT = 1024 * 1024
MODE = sys.argv[1]
ROOT = Path.cwd()
PREFIX = "bifrost-agent-prepare-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
LABEL = "bifrost.agent-prepare.owner"
EVIDENCE = Path(os.environ["AGENT_PREPARE_EVIDENCE"])
STATE_PATH = EVIDENCE / "private-state.json"
RAW_DIRECTORY = Path(os.environ["RUNNER_TEMP"]) / (PREFIX + "-private-diagnostics")


class Failure(Exception):
    def __init__(self, category, code=1):
        self.category = category
        self.code = code
        super().__init__("closed agent Prepare CI failure")


class Interrupted(BaseException):
    def __init__(self, code):
        self.code = code


def require(condition, category="schema"):
    if not condition:
        raise Failure(category)


def stop_signal(signum, _frame):
    raise Interrupted(128 + signum)


signal.signal(signal.SIGINT, stop_signal)
signal.signal(signal.SIGTERM, stop_signal)
os.umask(0o077)


def pairs(values):
    result = {}
    for key, value in values:
        require(key not in result)
        result[key] = value
    return result


def decode(raw, limit=EXCHANGE_LIMIT):
    require(len(raw) <= limit, "bound")
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(Failure("schema")),
    )


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha_file(path):
    result = hashlib.sha256()
    fd = -1
    original = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        require(stat.S_ISREG(os.fstat(fd).st_mode), "source")
        while block := os.read(fd, 65536):
            result.update(block)
    except BaseException as error:
        original = error
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    return result.hexdigest()


def identity(path):
    info = path.lstat()
    return {"dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid, "mode": stat.S_IMODE(info.st_mode)}


def checked_directory(path, expected):
    info = path.lstat()
    require(
        expected is not None
        and stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and identity(path) == expected,
        "acquisition",
    )
    return path


def atomic_json(path, value, limit=EXCHANGE_LIMIT):
    raw = canonical(value)
    require(len(raw) <= limit, "bound")
    temporary = path.with_name(path.name + ".new")
    fd = -1
    original = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        view = memoryview(raw)
        while view:
            n = os.write(fd, view)
            require(n > 0, "io")
            view = view[n:]
        os.close(fd)
        fd = -1
        temporary.replace(path)
    except BaseException as error:
        original = error
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except BaseException as error:
                if original is None:
                    original = error
        try:
            if temporary.exists():
                temporary.unlink()
        except BaseException as error:
            if original is None:
                original = error
    if original is not None:
        raise original


def empty_operation():
    return {"status": "not_started", "exit": None, "duration_s": None, "counts": None, "exchange": None}


def new_receipt(selected):
    return {
        "schema": "bifrost.test.agent-prepare-ci/v1",
        "mode": selected,
        "candidate": dict.fromkeys(
            (
                "associated_sha",
                "checkout_sha",
                "checkout_tree",
                "main_sha",
                "main_ancestor",
                "clean_before",
                "clean_after",
            )
        ),
        "source": dict.fromkeys(
            (
                "before",
                "after",
                "preserved_before",
                "preserved_after",
                "rust_snapshot",
                "api_snapshot",
                "locked_inventory",
            )
        ),
        "corpora": {"control": None, "agent": None},
        "graphs": dict.fromkeys(("workspace_default", "workspace_all_features", "off", "raw", "agent")),
        "images": {"api": None, "toolchain": None},
        "binaries": dict.fromkeys(
            ("off_tests", "raw_tests", "agent_tests", "off_control", "raw_control", "agent_control", "agent_prepare")
        ),
        "operations": {name: empty_operation() for name in OPERATIONS},
        "stages": [],
        "credentials": dict.fromkeys(("action_disposed", "ghcr_disposed", "product_environment_verified")),
        "cleanup": {
            "pre_pr_initial_empty": None,
            "pre_pr_final_empty": None,
            "owner_initial_empty": None,
            "owner_final_empty": None,
            "resources": [],
            "first_failure": None,
        },
        "format": None,
        "disposition": {
            "status": "failed",
            "primary_operation": None,
            "primary_class": None,
            "original_exit": None,
            "cleanup_failed": False,
            "publication_failed": False,
            "elapsed_s": 0,
        },
    }


state = decode(STATE_PATH.read_bytes())
require(os.environ.get("GITHUB_ACTIONS") == "true")
require(MODE in {"guard", "pre-pr", "prepare", "format", "verify", "cleanup"})
require(type(state["start"]) in (int, float) and math.isfinite(state["start"]))
require(0 <= time.monotonic() - state["start"] < 1800, "timeout")
selected = (
    "format"
    if (
        os.environ["GITHUB_EVENT_NAME"] == "push"
        and os.environ["GITHUB_REF"] == "refs/heads/rust/agent-prepare-codec"
        and state.get("format_subject", False)
    )
    else "verify"
)
if "receipt" not in state:
    state["receipt"] = new_receipt(selected)
    state["root_identity"] = identity(EVIDENCE)
    state["raw_identity"] = None
    atomic_json(STATE_PATH, state)
    initial_error = None
    try:
        RAW_DIRECTORY.mkdir(mode=0o700)
        state["raw_identity"] = identity(RAW_DIRECTORY)
    except BaseException as error:
        initial_error = error
    finally:
        try:
            atomic_json(STATE_PATH, state)
        except BaseException as error:
            if initial_error is None:
                initial_error = error
    if initial_error is not None:
        raise initial_error
checked_directory(EVIDENCE, state["root_identity"])
if not state.get("raw_removed"):
    checked_directory(RAW_DIRECTORY, state["raw_identity"])
receipt = state["receipt"]
stage_name = None
stage_start = None
active_operation = None


class DiagnosticObserver:
    # Exception identity stays lexical/private, never in JSON-persisted state.
    def __init__(self):
        self.failure = None
        self.completed = False

    def note(self, error, reason):
        if self.failure is None:
            self.failure = (error, reason)


def diagnostic_reason(label, error):
    if isinstance(error, (Interrupted, SystemExit, KeyboardInterrupt)) or not isinstance(error, Exception):
        return "control"
    if isinstance(error, Failure):
        if error.category == "timeout":
            return "deadline"
        if error.category == "bound":
            return "bound"
        categories = {
            "capture-check": {"acquisition"},
            "source-check": {"acquisition", "source"},
            "final-newline": {"schema"},
            "control-byte": {"schema"},
        }
        if error.category in categories.get(label, ()):
            return {"capture-check": "capture-identity", "source-check": "source-association"}.get(label, label)
    if label == "capture-open" and isinstance(error, FileNotFoundError):
        return "missing"
    if isinstance(error, UnicodeDecodeError) and label in {"raw-decode", "source-decode"}:
        return "utf8" if label == "raw-decode" else "source-encoding"
    if isinstance(error, OSError):
        return {
            "capture-open": "read-io",
            "capture-io": "read-io",
            "capture-check": "capture-identity",
            "source-io": "source-read",
            "source-check": "source-association",
        }.get(label, "internal")
    return "internal"


def diagnostic_operation(observer, label, actual_operation):
    require(
        label
        in {
            "capture-open",
            "capture-io",
            "capture-check",
            "source-io",
            "source-check",
            "raw-decode",
            "source-decode",
            "final-newline",
            "control-byte",
            "bound-check",
            "budget-check",
            "internal",
        },
        "schema",
    )
    try:
        return actual_operation()
    except BaseException as error:
        if observer is not None:
            # Recording cannot replace the exact pending original/control object.
            with suppress(BaseException):
                observer.note(error, diagnostic_reason(label, error))
        raise


def diagnostic_witness_initial():
    return {
        "capture": "not-checked",
        "stdout": "not-attempted",
        "stderr": "not-attempted",
        "projection": "not-attempted",
        "invalidated_by": None,
    }


def validate_diagnostic_witness(value):
    require(
        type(value) is dict and set(value) == {"capture", "stdout", "stderr", "projection", "invalidated_by"}, "schema"
    )
    require(
        type(value["capture"]) is str and value["capture"] in {"not-checked", "incomplete", "complete", "invalidated"},
        "schema",
    )
    for stream in ("stdout", "stderr"):
        require(
            type(value[stream]) is str
            and value[stream]
            in {
                "not-attempted",
                "complete",
                "missing",
                "capture-identity",
                "read-io",
                "utf8",
                "final-newline",
                "control-byte",
                "source-association",
                "source-encoding",
                "source-read",
                "bound",
                "deadline",
                "control",
                "internal",
            },
            "schema",
        )
    require(
        type(value["projection"]) is str and value["projection"] in {"not-attempted", "complete", "bound", "invalid"},
        "schema",
    )
    require(
        value["invalidated_by"] is None
        or (
            type(value["invalidated_by"]) is str
            and value["invalidated_by"]
            in {
                "capture-binding",
                "capture-bookkeeping",
                "capture-save",
                "stream-failure",
                "projection",
                "measurement-save",
                "pre-pr-capture",
            }
        ),
        "schema",
    )


def diagnostic_attach(value):
    witness = state.get("pre_pr_diagnostic_witness")
    if type(witness) is dict and type(value) is dict and type(value.get("diagnostic")) is dict:
        value["diagnostic"]["witness"] = witness.copy()


def diagnostic_note_invalidation(ledger, reason):
    if ledger["invalidated_by"] is None:
        ledger["invalidated_by"] = reason
    if ledger["capture"] == "complete":
        ledger["capture"] = "invalidated"


def invalidate_literal_capture(reason):
    require(
        reason
        in {
            "capture-binding",
            "capture-bookkeeping",
            "capture-save",
            "stream-failure",
            "projection",
            "measurement-save",
            "pre-pr-capture",
        },
        "schema",
    )
    witness = state.get("pre_pr_literal_capture")
    if type(witness) is dict:
        witness["capture_complete"] = False
    ledger = state.get("pre_pr_diagnostic_witness")
    if type(ledger) is dict:
        diagnostic_note_invalidation(ledger, reason)
    measurement = state.get("pre_pr_measurement")
    if (
        type(measurement) is dict
        and type(measurement.get("diagnostic")) is dict
        and measurement["diagnostic"].get("admission") == "observed"
    ):
        measurement["diagnostic"] = {"admission": "invalid", "records": []}
    diagnostic_attach(measurement)


def save(*, capture_invalidation_reason="measurement-save"):
    try:
        diagnostic_attach(state.get("pre_pr_measurement"))
        checked_directory(EVIDENCE, state["root_identity"])
        atomic_json(STATE_PATH, state)
    except BaseException:
        with suppress(BaseException):
            invalidate_literal_capture(capture_invalidation_reason)
        raise


def clean_env():
    result = os.environ.copy()
    for name in FORBIDDEN:
        result.pop(name, None)
    return result


def stage_record(name):
    return next((item for item in receipt["stages"] if item["name"] == name), None)


def milestone(name, phase, status, duration=None):
    key = name + ":" + phase
    emitted = state.setdefault("milestones", [])
    if key in emitted:
        return
    require(name in STAGES and phase in {"start", "complete"})
    require(status in {"running", "success", "failure", "interrupted"})
    record = {
        "schema": "bifrost.test.agent-prepare-stage/v1",
        "stage": name,
        "phase": phase,
        "elapsed_s": time.monotonic() - state["start"],
        "duration_s": duration,
        "status": status,
    }
    raw = canonical(record) + b"\n"
    require(len(emitted) < 20 and state.get("milestone_bytes", 0) + len(raw) <= 65536, "bound")
    emitted.append(key)
    state["milestone_bytes"] = state.get("milestone_bytes", 0) + len(raw)
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def end_stage(status="success", *, final=True):
    global stage_start, stage_name
    if stage_start is not None:
        row = stage_record(stage_name)
        row["duration_s"] += time.monotonic() - stage_start
        row["status"] = status if final else "running"
        if final:
            milestone(stage_name, "complete", status, row["duration_s"])
        stage_start = stage_name = None
        save()


def begin(name):
    global stage_name, stage_start
    end_stage(final=False)
    require(name in STAGES)
    stage_name, stage_start = name, time.monotonic()
    row = stage_record(name)
    if row is None:
        row = {"name": name, "cap_s": CAPS.get(name, 170), "duration_s": 0, "status": "running"}
        receipt["stages"].append(row)
    row["status"] = "running"
    milestone(name, "start", "running")
    save()


def remaining():
    require(stage_name is not None)
    now = time.monotonic()
    spent = stage_record(stage_name)["duration_s"] + now - stage_start
    cap = CAPS.get(stage_name, 170)
    if stage_name in {"guard", "publication"}:
        other = "guard" if stage_name == "publication" else "publication"
        cap -= (stage_record(other) or {}).get("duration_s", 0)
    job_reserve = 60 + 2 if stage_name not in {"cleanup", "publication"} else (2 if stage_name == "cleanup" else 0)
    left = min(cap - spent, 1800 - (now - state["start"]) - job_reserve)
    require(left > 0, "timeout")
    return left


def error_class(error):
    if isinstance(error, Failure):
        return error.category
    if not isinstance(error, Exception):
        return "control"
    return "io"


def error_code(error):
    if isinstance(error, (Failure, Interrupted)):
        return error.code
    return 130 if isinstance(error, KeyboardInterrupt) else 1


def primary(error, operation=None):
    row = receipt["disposition"]
    if row["primary_class"] is None:
        row.update(
            status="interrupted" if error_class(error) == "control" else "failed",
            primary_operation=operation,
            primary_class=error_class(error),
            original_exit=error_code(error),
        )


@contextmanager
def operation(name):
    global active_operation
    require(name in OPERATIONS)
    row = receipt["operations"][name]
    require(row["status"] == "not_started")
    prior = active_operation
    active_operation = name
    start = time.monotonic()
    try:
        yield row
        row.update(status="success", exit=0)
    except BaseException as error:
        row.update(status="interrupted" if error_class(error) == "control" else "failure")
        if isinstance(error, Failure):
            row["exit"] = error.code
        primary(error, name)
        raise
    finally:
        row["duration_s"] = time.monotonic() - start
        active_operation = prior
        # Saving is secondary to the exact pending original operation/control.
        original = sys.exc_info()[1]
        try:
            save()
        except BaseException:
            if original is None:
                raise


def capture_file_identity(info):
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_nlink == 1,
        "acquisition",
    )
    return {"dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid, "mode": 0o600, "nlink": 1}


def capture_file_final(info):
    return {
        "identity": capture_file_identity(info),
        "size": info.st_size,
        "mtime": info.st_mtime_ns,
        "ctime": info.st_ctime_ns,
    }


def literal_capture_complete(witness, row):
    # Pure admission predicate used for actual private facts and finite controls.
    streams = {"stdout", "stderr"}
    return (
        type(witness) is dict
        and witness.get("operation") == "literal-pre-pr"
        and witness.get("capture_complete") is True
        and witness.get("native_wait_completed") is True
        and witness.get("cleanup_failed") is False
        and type(witness.get("exit")) is int
        and -255 <= witness["exit"] <= 255
        and type(row) is dict
        and set(row) == {"operation", "exit", "bytes", "stdin_bytes", "reaped", "cleanup_failed"}
        and row["operation"] == "literal-pre-pr"
        and type(row["exit"]) is int
        and row["exit"] == witness["exit"]
        and row["reaped"] is True
        and row["cleanup_failed"] is False
        and type(row["stdin_bytes"]) is int
        and row["stdin_bytes"] == 0
        and type(witness.get("acquired")) is dict
        and set(witness["acquired"]) == streams
        and type(witness.get("final")) is dict
        and set(witness["final"]) == streams
        and type(witness.get("eof")) is dict
        and set(witness["eof"]) == streams
        and all(witness["eof"][stream] is True for stream in streams)
        and type(witness.get("closed")) is dict
        and set(witness["closed"]) == streams
        and all(witness["closed"][stream] is True for stream in streams)
        and type(row["bytes"]) is dict
        and set(row["bytes"]) == streams
        and witness.get("bytes") == row["bytes"]
        and all(
            type(row["bytes"][stream]) is int
            and 0 <= row["bytes"][stream] <= RAW_LIMIT
            and witness["final"][stream]["size"] == row["bytes"][stream]
            and witness["final"][stream]["identity"] == witness["acquired"][stream]
            for stream in streams
        )
        and sum(row["bytes"].values()) <= RAW_LIMIT
    )


def literal_capture_selected(operation_name, index, child_count):
    return (
        operation_name == "literal-pre-pr"
        and type(index) is int
        and type(child_count) is int
        and 0 <= index == child_count
    )


def literal_capture_binding(witness, children, index, directory_identity, counter):
    return (
        type(index) is int
        and type(children) is list
        and 0 <= index < len(children)
        and literal_capture_complete(witness, children[index])
        and type(witness.get("child_index")) is int
        and witness["child_index"] == index
        and type(directory_identity) is dict
        and directory_identity.get("mode") == 0o700
        and witness.get("directory") == directory_identity
        and type(witness.get("capture_number")) is int
        and type(counter) is int
        and 1 <= witness["capture_number"] <= counter
        and witness.get("paths")
        == {
            "stdout": str(RAW_DIRECTORY / (str(witness["capture_number"]) + ".stdout")),
            "stderr": str(RAW_DIRECTORY / (str(witness["capture_number"]) + ".stderr")),
        }
    )


def child(argv, *, input_bytes=None, env=None, limit=RAW_LIMIT, timeout=None):
    require(input_bytes is None or len(input_bytes) <= EXCHANGE_LIMIT, "bound")
    seconds = min(remaining(), timeout) if timeout is not None else remaining()
    require(seconds > 2, "timeout")
    deadline = time.monotonic() + seconds
    state["capture_counter"] = state.get("capture_counter", 0) + 1
    number = state["capture_counter"]
    stdout_path = RAW_DIRECTORY / (str(number) + ".stdout")
    stderr_path = RAW_DIRECTORY / (str(number) + ".stderr")
    process = selector = None
    output_fds = []
    pipes = []
    original = None
    cleanup_error = None
    code = None
    sizes = {"stdout": 0, "stderr": 0}
    sent = 0
    literal = None
    try:
        if literal_capture_selected(
            active_operation,
            state.get("pre_pr_literal_child_index"),
            len(state.get("children", [])),
        ):
            # The later existing Git-status child shares this operation label;
            # only its fixed first child index owns the literal capture witness.
            require(state.get("pre_pr_literal_capture") is None, "acquisition")
            checked_directory(RAW_DIRECTORY, state["raw_identity"])
            require(state["raw_identity"]["mode"] == 0o700, "acquisition")
            literal = {
                "operation": active_operation,
                "child_index": state["pre_pr_literal_child_index"],
                "capture_number": number,
                "directory": state["raw_identity"].copy(),
                "paths": {"stdout": str(stdout_path), "stderr": str(stderr_path)},
                "acquired": {},
                "final": {},
                "eof": {"stdout": False, "stderr": False},
                "closed": {"stdout": False, "stderr": False},
                "native_wait_completed": False,
                "exit": None,
                "bytes": {"stdout": 0, "stderr": 0},
                "cleanup_failed": False,
                "capture_complete": False,
            }
            state["pre_pr_literal_capture"] = literal
        # Each returned handle is retained inside the protected lifetime.
        for path in (stdout_path, stderr_path):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            output_fds.append(fd)
            if literal is not None:
                stream = "stdout" if path == stdout_path else "stderr"
                literal["acquired"][stream] = capture_file_identity(os.fstat(fd))
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=clean_env() if env is None else env,
        )
        # Popen returned all three members together. Retain every returned pipe
        # before any configuration can fail or receive a control exception.
        pipes = [pipe for pipe in (process.stdout, process.stderr, process.stdin) if pipe is not None]
        for pipe in pipes:
            os.set_blocking(pipe.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        if process.stdin is not None:
            if input_bytes:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
        while selector.get_map():
            left = deadline - time.monotonic() - 2
            require(left > 0, "timeout")
            for key, _ in selector.select(min(left, 0.25)):
                if key.data == "stdin":
                    try:
                        n = os.write(key.fd, memoryview(input_bytes)[sent : sent + 65536])
                    except BlockingIOError:
                        continue
                    require(n > 0, "io")
                    sent += n
                    if sent == len(input_bytes):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                try:
                    block = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not block:
                    if literal is not None:
                        literal["eof"][key.data] = True
                    selector.unregister(key.fileobj)
                    continue
                channel = key.data
                require(sizes[channel] + len(block) <= (limit if channel == "stdout" else RAW_LIMIT), "bound")
                require(sum(sizes.values()) + len(block) <= RAW_LIMIT, "bound")
                require(state.get("raw_bytes", 0) + sum(sizes.values()) + len(block) <= RAW_TOTAL, "bound")
                view = memoryview(block)
                while view:
                    n = os.write(output_fds[0 if channel == "stdout" else 1], view)
                    require(n > 0, "io")
                    view = view[n:]
                sizes[channel] += len(block)
        require(input_bytes is None or sent == len(input_bytes), "io")
        left = deadline - time.monotonic() - 2
        require(left > 0, "timeout")
        try:
            code = process.wait(timeout=left)
            if literal is not None:
                literal["native_wait_completed"] = True
                literal["exit"] = code
                for stream, fd in zip(("stdout", "stderr"), output_fds, strict=True):
                    finalized = capture_file_final(os.fstat(fd))
                    require(
                        finalized["identity"] == literal["acquired"][stream] and finalized["size"] == sizes[stream],
                        "acquisition",
                    )
                    literal["final"][stream] = finalized
        except subprocess.TimeoutExpired:
            raise Failure("timeout", 124) from None
    except BaseException as error:
        original = error
        if literal is not None:
            literal["capture_complete"] = False
    finally:

        def settle(callback):
            nonlocal cleanup_error
            try:
                callback()
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
                if literal is not None:
                    literal["capture_complete"] = False
                    literal["cleanup_failed"] = True

        for pipe in pipes:
            settle(pipe.close)
        if selector is not None:
            settle(selector.close)
        for stream, fd in zip(("stdout", "stderr"), output_fds, strict=False):

            def close_output(fd=fd, stream=stream):
                os.close(fd)
                if literal is not None:
                    literal["closed"][stream] = True

            settle(close_output)
        if process is not None:
            running = True
            try:
                running = process.poll() is None
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
            if original is not None or running:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error

            def reap():
                left = deadline - time.monotonic()
                require(left > 0, "timeout")
                process.wait(timeout=min(2, left))

            settle(reap)

        def retain_child():
            state["raw_bytes"] = state.get("raw_bytes", 0) + sum(sizes.values())
            row = {
                "operation": active_operation,
                "exit": code,
                "bytes": sizes.copy(),
                "stdin_bytes": sent,
                "reaped": process is not None and process.returncode is not None,
                "cleanup_failed": cleanup_error is not None,
            }
            state.setdefault("children", []).append(row)
            if literal is not None:
                literal["bytes"] = sizes.copy()
                literal["cleanup_failed"] = cleanup_error is not None
                literal["capture_complete"] = original is None and cleanup_error is None
                literal["capture_complete"] = literal_capture_complete(literal, row)

        def retain_observed_child():
            try:
                retain_child()
            except BaseException:
                if literal is not None:
                    with suppress(BaseException):
                        invalidate_literal_capture("capture-bookkeeping")
                raise

        settle(retain_observed_child)
        settle(lambda: save(capture_invalidation_reason="capture-save") if literal is not None else save())
    if original is not None:
        raise original
    if cleanup_error is not None:
        raise cleanup_error
    return code, stdout_path, stderr_path


def command(argv, **kwargs):
    code, output, errors = child(argv, **kwargs)
    if active_operation is not None:
        receipt["operations"][active_operation]["exit"] = code
    if code != 0:
        raise Failure("test", code)
    return output, errors


def capture(argv):
    output, _ = command(argv, timeout=10)
    return output.read_text().strip()


def inspect(kind, name):
    return decode(capture(["docker", kind, "inspect", name]).encode())[0]


def source_readback(which):
    result = {}
    for path in SOURCE_KEYS:
        actual = sha_file(ROOT / path)
        if path in EXPECTED_SOURCE:
            require(actual == EXPECTED_SOURCE[path], "source")
        # New two CI paths compare actual checkout bytes to its Git object, not themselves.
        require(sha_bytes(command(["git", "show", "HEAD:" + path])[0].read_bytes()) == actual, "source")
        result[path] = actual
    receipt["source"][which] = result
    preserved = {path: sha_file(ROOT / path) for path in PRESERVED}
    require(preserved == PRESERVED, "source")
    receipt["source"]["preserved_" + which] = preserved
    return result


def frozen_corpora():
    control = decode((ROOT / CONTROL_FIXTURE).read_bytes())
    agent = decode((ROOT / AGENT_FIXTURE).read_bytes())
    observed = {
        "control": {
            "fixture_sha256": sha_file(ROOT / CONTROL_FIXTURE),
            **{name: [v["name"] for v in control[name]] for name in ("wire", "binary", "sessions")},
        },
        "agent": {
            "fixture_sha256": sha_file(ROOT / AGENT_FIXTURE),
            "fixture_bytes": (ROOT / AGENT_FIXTURE).stat().st_size,
            "wire": [v["name"] for v in agent["wire"]],
            "positive": [v["name"] for v in agent["wire"] if v["expected"] == "ok"],
            "direct_test_roster_sha256": FROZEN_CORPORA["agent"]["direct_test_roster_sha256"],
        },
    }
    require(observed == FROZEN_CORPORA, "source")
    receipt["corpora"] = observed


LOCKED_IDENTITY_SHA256 = "2cd2fe8e82b1d53a52fb46008cc79315ec1c70aa173a6f42efeaabc8a6a04d74"


PYTHON_PARAMETERS = {
    "test_actual_original_reemit_and_consumption": ["prepare-parent", "raw-whitespace", "raw-float-spellings"],
    "test_existing_control_rejects": ["prepare-parent", "prepared-unexpected-observations"],
    "test_non_json": [r"\xff-InvalidJson", "-InvalidJson"],
    "test_depth": ["63", "64", "65"],
    "test_initial_read_failure_stops_without_extra_reads": ["outcome0", "outcome1", "None", "x", "xx"],
    "test_interrupted_read_continues_same_frame": ["0", "1", "4"],
    "test_binding_rejects_distinct_actual_owners": [
        "hello-message",
        "hello-session",
        "prepare-message",
        "slot",
        "prepare-hash",
        "hello-hash",
    ],
    "test_new_positive_retained_and_fresh_custody": ["retained-reordered-prepare", "fresh-business-map-extra-key"],
}
PYTHON_CONTROL_TESTS = {
    "test_shared_wire_vectors",
    "test_shared_binary_vectors_with_partial_io",
    "test_shared_parent_session_vectors",
    "test_over_cap_payload_and_concatenated_frames",
}


def python_expected():
    result = set()
    for name, count in PYTHON_AGENT_FUNCTION_COUNTS.items():
        parameters = FROZEN_CORPORA["agent"]["wire"] if name == "test_shared_corpus" else PYTHON_PARAMETERS.get(name)
        if parameters is None:
            require(count == 1, "source")
            result.add(name)
        else:
            require(len(parameters) == count and len(parameters) == len(set(parameters)), "source")
            result.update(name + "[" + value + "]" for value in parameters)
    return result


def inventory():
    packages = tomllib.loads((ROOT / "core-rs/Cargo.lock").read_text())["package"]
    values = sorted(
        [[p["name"], p["version"], p.get("source"), p.get("checksum")] for p in packages], key=lambda p: (p[0], p[1])
    )
    actual = sha_bytes(canonical(values))
    receipt["source"]["locked_inventory"] = {
        "baseline_sha256": LOCKED_IDENTITY_SHA256,
        "actual_sha256": actual,
        "count": len(values),
        "unchanged": actual == LOCKED_IDENTITY_SHA256,
    }
    require(actual == LOCKED_IDENTITY_SHA256 and len(values) == 232, "inventory")


def tracked_paths():
    paths = command(["git", "ls-tree", "-rz", "HEAD"])[0].read_bytes().split(b"\0")
    result = []
    diagnostic_git = {}
    for row in paths:
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        mode, kind, _git_hash = metadata.decode("ascii").split(" ")
        # The complete snapshots below exclude symlinks, submodules and path escapes.
        path = name.decode("utf-8")
        require(
            not path.startswith("/") and ".." not in Path(path).parts and "\n" not in path and "\r" not in path,
            "source",
        )
        result.append((path, mode, kind))
        if path.startswith(("api/src/", "api/shared/", "api/bifrost/", "api/tests/")) and path.endswith(".py"):
            diagnostic_git[path] = {"mode": mode, "kind": kind, "oid": _git_hash}
    state["pre_pr_diagnostic_git"] = diagnostic_git
    return result


def source_snapshots():
    rust = {}
    api = {}
    for path, mode, kind in tracked_paths():
        selected_api = (
            path.startswith("api/src/")
            or path.startswith("api/tests/runtime_protocol/")
            or path in {"api/tests/__init__.py", "api/pytest.ini", CONTROL_FIXTURE, AGENT_FIXTURE}
        )
        if path.startswith("core-rs/") or selected_api:
            require(kind == "blob" and mode in {"100644", "100755"}, "source")
            actual = sha_file(ROOT / path)
            if path.startswith("core-rs/"):
                rust[path] = actual
            if selected_api:
                api[path] = actual
    require(rust and api and len(rust) <= 65535 and len(api) <= 65535, "source")
    require(len(canonical(rust)) <= EXCHANGE_LIMIT and len(canonical(api)) <= EXCHANGE_LIMIT, "bound")
    state["rust_inventory"] = rust
    state["api_inventory"] = api
    for name, values in (("rust", rust), ("api", api)):
        receipt["source"][name + "_snapshot"] = {
            "candidate_sha256": sha_bytes(canonical(values)),
            "mounted_before_sha256": None,
            "mounted_after_sha256": None,
            "file_count": len(values),
        }


def absent_owned(project, prefix):
    values = []
    for _kind, argv in (
        ("container", ["docker", "ps", "-aq", "--no-trunc"]),
        ("volume", ["docker", "volume", "ls", "-q"]),
        ("network", ["docker", "network", "ls", "-q", "--no-trunc"]),
    ):
        selector = "com.docker.compose.project=" + project if project else LABEL + "=" + prefix
        values.append(capture([*argv, "--filter", "label=" + selector]))
    return not any(values)


def guard():
    begin("guard")
    with operation("source-guard"):
        require(
            os.environ["GITHUB_EVENT_NAME"] == "push"
            and os.environ["GITHUB_REF"] == "refs/heads/rust/agent-prepare-codec",
            "source",
        )
        actual = capture(["git", "rev-parse", "HEAD"])
        require(actual == os.environ["GITHUB_SHA"], "source")
        require(not capture(["git", "status", "--porcelain", "--untracked-files=all"]), "source")
        subject = capture(["git", "show", "-s", "--format=%s", "HEAD"])
        receipt["mode"] = "format" if subject == "ci: agent-prepare format-only" else "verify"
        state["format_subject"] = receipt["mode"] == "format"
        capture(["git", "-c", "credential.helper=", "-c", "http.extraheader=", "fetch", "origin", "main"])
        main = capture(["git", "rev-parse", "origin/main"])
        command(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"])
        receipt["candidate"].update(
            associated_sha=os.environ["GITHUB_SHA"],
            checkout_sha=actual,
            checkout_tree=capture(["git", "rev-parse", "HEAD^{tree}"]),
            main_sha=main,
            main_ancestor=True,
            clean_before=True,
        )
        project = capture(["bash", "-c", "source scripts/lib/test_helpers.sh; compute_project_name ."])
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", project) is not None, "source")
        state["project"] = project
        state["initial_image_ids"] = capture(
            ["docker", "image", "ls", "--no-trunc", "--format", "{{.ID}}"]
        ).splitlines()
        state["pre_pr_api_tag"] = {
            "tag": "bifrost-test-api-dev:latest",
            "previous": capture(["docker", "image", "ls", "-q", "--no-trunc", "bifrost-test-api-dev:latest"]),
            "id": None,
            "removed": False,
            "witness": None,
        }
        require(not state["pre_pr_api_tag"]["previous"], "acquisition")
        require(absent_owned(project, PREFIX), "acquisition")
        receipt["cleanup"]["pre_pr_initial_empty"] = True
        require(absent_owned(None, PREFIX), "acquisition")
        receipt["cleanup"]["owner_initial_empty"] = True
        with operation("source-before"):
            source_readback("before")
            frozen_corpora()
            source_snapshots()
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("value=" + receipt["mode"] + "\n")
    end_stage()


def directory(role, *, readable=False):
    path = (
        EVIDENCE / role
        if role not in {"ghcr-config", "private-diagnostics"}
        else Path(os.environ["RUNNER_TEMP"]) / (PREFIX + "-" + role)
    )
    require(not path.exists(), "acquisition")
    record = {"kind": "directory", "role": role, "path": str(path), "identity": None, "removed": False}
    state.setdefault("directories", []).append(record)
    save()
    # Pending name is retained even if identity observation fails after mkdir.
    path.mkdir(mode=0o755 if readable else 0o700)
    record["identity"] = identity(path)
    save()
    return path


def volume(role):
    name = PREFIX + "-" + role
    require(not capture(["docker", "volume", "ls", "-q", "--filter", "name=^" + name + "$"]), "acquisition")
    record = {"name": name, "facts": None, "removed": False}
    state.setdefault("volumes", []).append(record)
    save()
    command(["docker", "volume", "create", "--label", LABEL + "=" + PREFIX, name])
    facts = inspect("volume", name)
    require(facts["Name"] == name and facts["Labels"].get(LABEL) == PREFIX, "acquisition")
    record["facts"] = {k: facts[k] for k in ("Name", "Mountpoint", "CreatedAt")}
    save()
    return name


def container(image, argv, *, mounts=(), variables=(), network="none", python=False, input_bytes=None, limit=RAW_LIMIT):
    require(receipt["mode"] == "format" or receipt["credentials"]["action_disposed"], "credential")
    require(receipt["mode"] == "format" or receipt["credentials"]["ghcr_disposed"], "credential")
    state["container_counter"] = state.get("container_counter", 0) + 1
    name = PREFIX + "-" + str(state["container_counter"])
    require(not capture(["docker", "ps", "-aq", "--no-trunc", "--filter", "name=^/" + name + "$"]), "acquisition")
    record = {
        "name": name,
        "id": None,
        "image": image,
        "network": network,
        "mounts": list(mounts),
        "variables": list(variables),
        "python": python,
        "removed": False,
    }
    state.setdefault("containers", []).append(record)
    save()
    argv_create = ["docker", "create", "--name", name, "--label", LABEL + "=" + PREFIX, "--network", network]
    if input_bytes is not None:
        argv_create.append("-i")
    for source, target, readonly in mounts:
        argv_create += [
            "--mount",
            "type="
            + ("bind" if source.startswith("/") else "volume")
            + ",source="
            + source
            + ",target="
            + target
            + (",readonly" if readonly else ""),
        ]
    for key, value in variables:
        require(key not in FORBIDDEN, "credential")
        argv_create += ["-e", key + "=" + value]
    if python:
        argv_create += ["--user", "1000:1000", "--entrypoint", "python"]
    argv_create += [image, *argv]
    command(argv_create)
    cid = capture(["docker", "inspect", name, "--format", "{{.Id}}"])
    require(re.fullmatch(r"[0-9a-f]{64}", cid) is not None, "acquisition")
    record["id"] = cid
    save()
    verify_container(record)
    code, out, err = child(
        ["docker", "start", "-ai" if input_bytes is not None else "-a", cid], input_bytes=input_bytes, limit=limit
    )
    verify_container(record)
    facts = inspect("container", cid)
    require(not facts["State"]["Running"] and code == facts["State"]["ExitCode"], "acquisition")
    record["exit"] = code
    if code != 0:
        raise Failure("test", code)
    save()
    return record, out, err


def verify_container(record):
    facts = inspect("container", record["id"])
    require(
        facts["Id"] == record["id"]
        and facts["Name"] == "/" + record["name"]
        and facts["Image"] == record["image"]
        and facts["Config"]["Labels"].get(LABEL) == PREFIX
        and facts["HostConfig"]["NetworkMode"] == record["network"],
        "acquisition",
    )
    require(len(facts["Mounts"]) == len(record["mounts"]), "acquisition")
    for source, target, readonly in record["mounts"]:
        actual = next((m for m in facts["Mounts"] if m["Destination"] == target), None)
        require(actual is not None and actual["RW"] == (not readonly), "acquisition")
        require(actual["Source"] == source if source.startswith("/") else actual["Name"] == source, "acquisition")
    env_keys = {value.split("=", 1)[0] for value in facts["Config"]["Env"]}
    require(not env_keys.intersection(FORBIDDEN), "credential")
    if record["python"]:
        require(facts["Config"]["User"] == "1000:1000" and facts["Config"]["Entrypoint"] == ["python"], "acquisition")
    if record["network"] == "none" and receipt["mode"] == "verify":
        receipt["credentials"]["product_environment_verified"] = True
    return facts


def rust(argv, target, *, input_bytes=None, network="none", source=None, limit=RAW_LIMIT):
    mounts = [
        (str(source or ROOT / "core-rs"), "/workspace/core-rs", True),
        (state["cargo_home"], "/usr/local/cargo", network == "none"),
        (state["targets"][target], "/targets", False),
    ]
    if state.get("exchange"):
        mounts.append((state["exchange"], "/exchange", True))
    return container(
        state["toolchain_image"],
        argv,
        mounts=mounts,
        network=network,
        variables=[
            ("CARGO_TARGET_DIR", "/targets"),
            (
                "BIFROST_RUNTIME_VECTORS",
                "/workspace/core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json",
            ),
        ],
        input_bytes=input_bytes,
        limit=limit,
    )


def python(argv, *, input_bytes=None, limit=RAW_LIMIT):
    mounts = [
        (str(ROOT / "api/src"), "/app/src", True),
        (str(ROOT / "api/tests/__init__.py"), "/app/tests/__init__.py", True),
        (str(ROOT / "api/tests/runtime_protocol"), "/app/tests/runtime_protocol", True),
        (str(ROOT / "api/pytest.ini"), "/app/pytest.ini", True),
        (str(ROOT / CONTROL_FIXTURE), "/contracts/control-vectors.json", True),
        (str(ROOT / AGENT_FIXTURE), "/" + AGENT_FIXTURE, True),
    ]
    if state.get("exchange"):
        mounts.append((state["exchange"], "/exchange", True))
    return container(
        state["api_image"],
        argv,
        mounts=mounts,
        python=True,
        variables=[("BIFROST_RUNTIME_VECTORS", "/contracts/control-vectors.json"), ("PYTHONDONTWRITEBYTECODE", "1")],
        input_bytes=input_bytes,
        limit=limit,
    )


def credential_cleanup():
    env = clean_env()
    for key in ("BIFROST_ACTION_PIN_TOKEN_FILE", "SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY"):
        if os.environ.get(key):
            state[key] = os.environ[key]
    for key in ("BIFROST_ACTION_PIN_TOKEN_FILE", "SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY"):
        require(state.get(key), "credential")
        env[key] = state[key]
    command(["bash", HELPER, "credential-cleanup"], env=env)
    require(not os.path.lexists(state["BIFROST_ACTION_PIN_TOKEN_FILE"]), "credential")
    require(not Path(state["BIFROST_ACTION_PIN_TOKEN_FILE"]).parent.exists(), "credential")
    receipt["credentials"]["action_disposed"] = True
    for key in ("BIFROST_ACTION_PIN_TOKEN_FILE", "SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY"):
        os.environ.pop(key, None)
    save()


PRE_PR_STAGES = {
    "repository",
    "client",
    "stack",
    "quality",
    "generated",
    "unit",
    "e2e",
    "mcp",
    "client-unit",
    "browser",
    "image",
}


def pre_pr_file(path, limit=65536, *, diagnostic_observer=None):
    def call(label, operation):
        return diagnostic_operation(diagnostic_observer, label, operation)

    # O_NONBLOCK prevents an unexpected FIFO from blocking before fstat admission.
    fd = -1
    original = None
    result = bytearray()
    try:
        call("budget-check", remaining)
        call("source-check", lambda: require(path == path.resolve(), "acquisition"))
        fd = call("source-io", lambda: os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC))
        info = call("source-check", lambda: os.fstat(fd))
        call(
            "source-check",
            lambda: require(
                stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_size <= limit, "acquisition"
            ),
        )
        while block := call("source-io", lambda: os.read(fd, min(65536, limit + 1 - len(result)))):
            result.extend(block)
            call("bound-check", lambda: require(len(result) <= limit, "bound"))
        call("budget-check", remaining)
        after = call("source-check", lambda: os.fstat(fd))
        call(
            "source-check",
            lambda: require(
                (
                    after.st_dev,
                    after.st_ino,
                    after.st_uid,
                    after.st_mode,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                == (
                    info.st_dev,
                    info.st_ino,
                    info.st_uid,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ),
                "source",
            ),
        )
    except BaseException as error:
        original = error
    finally:
        if fd >= 0:
            try:
                call("source-io", lambda: os.close(fd))
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    return bytes(result)


def pre_pr_environment():
    # Invoke the existing trusted shell source, including its primary-checkout
    # .env.test fallback; never guess effective environment from a text parser.
    script = (
        "source ./test.sh help >/dev/null; export BIFROST_TEST_ENV_FILE; "
        "python3 -c 'import json,os; "
        "print(json.dumps({k:os.environ.get(k) for k in "
        '("COMPOSE_FILE","COMPOSE_PROJECT_NAME","BIFROST_SKIP_BUILD","BIFROST_TEST_ENV_FILE")}))' + "'"
    )
    # Match the existing stage runner's argv0: help reads $0, which must be
    # the genuine script path rather than the shell name or a synthetic label.
    value = decode(capture(["bash", "-c", script, "./test.sh"]).encode(), 65536)
    require(
        type(value) is dict
        and set(value) == {"COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "BIFROST_SKIP_BUILD", "BIFROST_TEST_ENV_FILE"}
        and value["COMPOSE_FILE"] == "docker-compose.test.yml"
        and value["COMPOSE_PROJECT_NAME"] == state["project"]
        and value["BIFROST_SKIP_BUILD"] != "1"
        and type(value["BIFROST_TEST_ENV_FILE"]) is str,
        "source",
    )
    selected = Path(value["BIFROST_TEST_ENV_FILE"])
    common = Path(capture(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]))
    primary = common.parent / ".env.test"
    expected = ROOT / ".env.test" if (ROOT / ".env.test").is_file() else primary
    if not expected.is_file():
        expected = ROOT / ".env.test"
    require(selected == expected, "source")
    value["env_digest"] = sha_bytes(pre_pr_file(selected)) if selected.is_file() else "missing"
    return value


def pre_pr_snapshot(env_file, ledger, stage="stack"):
    # The helper observes Compose under the same trusted effective environment.
    require(stage in PRE_PR_STAGES, "schema")
    raw = capture(
        [
            "bash",
            "-c",
            'source ./test.sh help >/dev/null; python3 "$@"',
            "./test.sh",
            "scripts/lib/pre_pr_stage_evidence.py",
            "snapshot",
            "--repo",
            str(ROOT),
            "--state",
            str(ledger),
            "--stage",
            stage,
            "--compose-file",
            "docker-compose.test.yml",
            "--env-file",
            env_file,
        ]
    )
    value = decode(raw.encode(), 65536)
    require(
        type(value) is dict
        and set(value)
        == {
            "head",
            "status",
            "compose_sha256",
            "compose_available",
            "compose_images",
            "env_sha256",
            "docker_version",
            "compose_version",
            "python_version",
            "node_version",
            "browser_config_sha256",
        }
        and value["head"] == receipt["candidate"]["checkout_sha"]
        and value["status"] == ""
        and value["compose_available"] is True,
        "source",
    )
    for key in value.keys() - {"compose_available", "compose_images"}:
        require(type(value[key]) is str and value[key] != "unavailable", "source")
    require(
        type(value["compose_images"]) is list
        and len(value["compose_images"]) <= 128
        and all(type(item) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in value["compose_images"]),
        "source",
    )
    return value


DIAGNOSTIC_SHADOWS = (
    "src/services/app_compiler/node_modules/",
    "src/services/app_bundler/node_modules/",
    "src/services/sdk_package/node_modules/",
    "src/services/sdk_package/sdk_src/",
)


def diagnostic_path(path):
    if type(path) is not str or not path.isascii() or not 1 <= len(path) <= 256:
        return None
    if path.startswith("/app/"):
        path = path[5:]
    if (
        not path.startswith(("src/", "shared/", "bifrost/", "tests/"))
        or path.startswith(DIAGNOSTIC_SHADOWS)
        or not path.endswith(".py")
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 or char in "\\:" for char in path)
    ):
        return None
    result = "api/" + path
    return result if len(result) <= 256 else None


def diagnostic_record(tool, severity, path, line, column, location):
    mapped = diagnostic_path(path)
    if mapped is None:
        return None
    if type(line) is not int or type(column) is not int or not 1 <= line <= 1048576 or not 1 <= column <= 1048576:
        return None
    lines = location(mapped)
    if type(lines) is not int or line > lines:
        return None
    return {
        "tool": tool,
        "code": "unclassified",
        "severity": severity,
        "source_path": mapped,
        "line": line,
        "column": column,
    }


def diagnostic_records(raw, location, *, diagnostic_observer=None):
    def call(label, operation):
        return diagnostic_operation(diagnostic_observer, label, operation)

    call("bound-check", lambda: require(type(raw) is bytes and len(raw) <= RAW_LIMIT, "bound"))
    text = call("raw-decode", lambda: raw.decode("utf-8"))
    call("final-newline", lambda: require(not text or text.endswith("\n"), "schema"))
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line[:-1] if line.endswith("\r") else line
        call(
            "control-byte",
            lambda line=line: require(
                all((ord(char) >= 32 and not 127 <= ord(char) <= 159) or char == "\t" for char in line), "schema"
            ),
        )
        cleaned.append(line)
    found = {}
    for index, line in enumerate(cleaned):
        pyright = re.fullmatch(r"  ([^:\r\n]+):([1-9][0-9]*):([1-9][0-9]*) - (error|warning|information): .*", line)
        if pyright:
            # Messages, continuations and any reported rule are never retained.
            path, number, column, severity = pyright.groups()
            if len(number) <= 7 and len(column) <= 7:
                record = diagnostic_record("pyright", severity, path, int(number), int(column), location)
                if record is not None:
                    found[diagnostic_order(record)] = record
        explicit = re.fullmatch(r"(error|warning|info)\[(?:[A-Z]+[0-9]+|invalid-syntax)\](?:\[\*\])?: .+", line)
        hidden = re.fullmatch(r"(?:[A-Z]+[0-9]+(?: \[\*\])? .+|invalid-syntax: .+)", line)
        if (explicit or hidden) and index + 1 < len(cleaned):
            arrow = re.fullmatch(r" {1,2}--> ([^:\r\n]+):([1-9][0-9]*):([1-9][0-9]*)", cleaned[index + 1])
            if arrow:
                path, number, column = arrow.groups()
                severity = explicit[1] if explicit else "unknown"
                severity = "information" if severity == "info" else severity
                if len(number) <= 7 and len(column) <= 7:
                    record = diagnostic_record("ruff", severity, path, int(number), int(column), location)
                    if record is not None:
                        found[diagnostic_order(record)] = record
        call("bound-check", lambda: require(len(found) <= 32, "bound"))
    return list(found.values())


def diagnostic_order(row):
    return (
        row["tool"],
        row["source_path"],
        row["line"],
        row["column"] is not None,
        row["column"] if row["column"] is not None else 0,
        row["code"],
        row["severity"],
    )


def diagnostic_projection(statuses, records):
    for status in ("bound", "invalid", "missing"):
        if status in statuses:
            return {"admission": status, "records": []}
    unique = {diagnostic_order(row): row for row in records}
    if len(unique) > 32:
        return {"admission": "bound", "records": []}
    result = [unique[key] for key in sorted(unique)]
    return {"admission": "observed" if result else "unrecognized", "records": result}


def validate_diagnostic(value):
    require(
        type(value) is dict
        and set(value) == {"admission", "records"}
        and value["admission"] in {"not-needed", "observed", "missing", "unrecognized", "bound", "invalid"}
        and type(value["records"]) is list
        and len(value["records"]) <= 32,
        "schema",
    )
    keys = []
    for row in value["records"]:
        require(
            type(row) is dict
            and set(row) == {"tool", "code", "severity", "source_path", "line", "column"}
            and row["tool"] in {"pyright", "ruff"}
            and row["code"] == "unclassified"
            and row["severity"] in {"error", "warning", "information", "unknown"}
            and type(row["source_path"]) is str
            and row["source_path"].startswith("api/")
            and diagnostic_path(row["source_path"][4:]) == row["source_path"]
            and type(row["line"]) is int
            and 1 <= row["line"] <= 1048576
            and (row["column"] is None or (type(row["column"]) is int and 1 <= row["column"] <= 1048576)),
            "schema",
        )
        keys.append(diagnostic_order(row))
    require(keys == sorted(set(keys)), "schema")
    require(bool(keys) == (value["admission"] == "observed"), "schema")


def literal_capture_read(witness, stream, *, diagnostic_observer=None):
    def call(label, operation):
        return diagnostic_operation(diagnostic_observer, label, operation)

    call("capture-check", lambda: checked_directory(RAW_DIRECTORY, state["raw_identity"]))
    call("capture-check", lambda: require(witness["directory"] == state["raw_identity"], "acquisition"))
    path = Path(witness["paths"][stream])
    suffix = ".stdout" if stream == "stdout" else ".stderr"
    call(
        "capture-check",
        lambda: require(path == RAW_DIRECTORY / (str(witness["capture_number"]) + suffix), "acquisition"),
    )
    finalized = witness["final"][stream]
    fd = -1
    original = None
    raw = bytearray()
    try:
        call("budget-check", remaining)
        fd = call("capture-open", lambda: os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC))
        call("capture-check", lambda: require(capture_file_final(os.fstat(fd)) == finalized, "acquisition"))
        call("capture-check", lambda: require(finalized["identity"] == witness["acquired"][stream], "acquisition"))
        call("bound-check", lambda: require(0 <= finalized["size"] <= RAW_LIMIT, "bound"))
        while block := call("capture-io", lambda: os.read(fd, min(65536, finalized["size"] + 1 - len(raw)))):
            raw.extend(block)
            call("bound-check", lambda: require(len(raw) <= finalized["size"], "bound"))
            call("budget-check", remaining)
        call("capture-check", lambda: require(len(raw) == finalized["size"] == witness["bytes"][stream], "acquisition"))
        call("capture-check", lambda: require(capture_file_final(os.fstat(fd)) == finalized, "acquisition"))
        call("capture-check", lambda: checked_directory(RAW_DIRECTORY, witness["directory"]))
        call("budget-check", remaining)
    except BaseException as error:
        original = error
    finally:
        if fd >= 0:
            try:
                call("capture-io", lambda: os.close(fd))
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    return bytes(raw)


def diagnostic_source_lines(path, *, diagnostic_observer=None):
    def call(label, operation):
        return diagnostic_operation(diagnostic_observer, label, operation)

    tracked = state.get("pre_pr_diagnostic_git", {}).get(path)
    if not diagnostic_tracked_file(tracked):
        return None
    source = ROOT / path
    call("source-check", lambda: require(source == source.resolve(), "source"))
    raw = call("internal", lambda: pre_pr_file(source, RAW_LIMIT, diagnostic_observer=diagnostic_observer))
    git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw, usedforsecurity=False).hexdigest()
    call("source-check", lambda: require(git_blob == tracked["oid"], "source"))
    text = call("source-decode", lambda: raw.decode("utf-8"))
    return len(text.split("\n")) - (1 if text.endswith("\n") else 0) if text else 0


def diagnostic_finish_stream(observer, actual_remaining):
    diagnostic_operation(observer, "budget-check", actual_remaining)
    observer.completed = True


def diagnostic_reason_controls():
    # Declarations execute only in the existing supported pre-PR control scope.
    # Synthetic failures never supply real child/parser/run observations.
    def fail(error):
        raise error

    sentinel = object()
    observer = DiagnosticObserver()
    calls = []

    def success():
        calls.append(True)
        return sentinel

    require(diagnostic_operation(observer, "internal", success) is sentinel and calls == [True], "source")
    require(observer.failure is None and observer.completed is False, "source")
    for label, error, expected in (
        ("capture-open", FileNotFoundError(), "missing"),
        ("source-io", FileNotFoundError(), "source-read"),
        ("capture-io", OSError(), "read-io"),
        ("capture-check", Failure("acquisition"), "capture-identity"),
        ("source-check", Failure("source"), "source-association"),
        ("raw-decode", UnicodeDecodeError("utf-8", b"x", 0, 1, "synthetic"), "utf8"),
        ("source-decode", UnicodeDecodeError("utf-8", b"x", 0, 1, "synthetic"), "source-encoding"),
        ("final-newline", Failure("schema"), "final-newline"),
        ("control-byte", Failure("schema"), "control-byte"),
        ("internal", RuntimeError(), "internal"),
        ("bound-check", Failure("bound"), "bound"),
        ("budget-check", Failure("timeout"), "deadline"),
        ("internal", Interrupted(17), "control"),
        ("internal", KeyboardInterrupt(), "control"),
        ("internal", SystemExit(17), "control"),
    ):
        current = DiagnosticObserver()
        count = []

        def failing(error=error, count=count):
            count.append(True)
            raise error

        try:
            diagnostic_operation(
                current,
                "internal",
                lambda current=current, label=label, failing=failing: diagnostic_operation(current, label, failing),
            )
        except BaseException as caught:
            require(caught is error and count == [True] and current.failure == (error, expected), "source")
        else:
            raise Failure("source")
        close_error = OSError()
        try:
            diagnostic_operation(current, "capture-io", lambda close_error=close_error: fail(close_error))
        except BaseException as caught:
            require(caught is close_error and current.failure == (error, expected), "source")
        else:
            raise Failure("source")
    # A successful source callback leaves no stale reason for later bound/close.
    for label, later, expected in (("bound-check", Failure("bound"), "bound"), ("capture-io", OSError(), "read-io")):
        current = DiagnosticObserver()
        diagnostic_operation(current, "source-check", lambda: None)
        try:
            diagnostic_operation(current, label, lambda later=later: fail(later))
        except BaseException as caught:
            require(caught is later and current.failure == (later, expected), "source")
        else:
            raise Failure("source")
    current = DiagnosticObserver()
    later = Failure("timeout")
    try:
        diagnostic_finish_stream(current, lambda later=later: fail(later))
    except Failure as caught:
        require(caught is later and current.completed is False and current.failure == (later, "deadline"), "source")
    else:
        raise Failure("source")
    current = DiagnosticObserver()
    completed = []
    diagnostic_finish_stream(current, lambda: completed.append(True))
    require(current.completed is True and current.failure is None and completed == [True], "source")

    class BrokenObserver:
        def note(self, _error, _reason):
            raise RuntimeError()

    original = Interrupted(19)
    try:
        diagnostic_operation(BrokenObserver(), "internal", lambda: fail(original))
    except BaseException as caught:
        require(caught is original, "source")
    else:
        raise Failure("source")
    ordinary = RuntimeError()
    current = DiagnosticObserver()
    try:
        diagnostic_operation(current, "internal", lambda: fail(ordinary))
    except RuntimeError as caught:
        require(caught is ordinary, "source")
    later_control = Interrupted(21)
    try:
        diagnostic_operation(current, "capture-io", lambda: fail(later_control))
    except BaseException as caught:
        require(caught is later_control and current.failure == (ordinary, "internal"), "source")
    else:
        raise Failure("source")
    independent = []
    for stream in ("stdout", "stderr"):
        current = DiagnosticObserver()
        error = FileNotFoundError()
        try:
            diagnostic_operation(current, "capture-open", lambda error=error: fail(error))
        except FileNotFoundError as caught:
            require(caught is error and current.failure == (error, "missing"), "source")
            independent.append(stream)
    require(independent == ["stdout", "stderr"], "source")
    count = []
    try:
        diagnostic_operation(DiagnosticObserver(), "unknown", lambda: count.append(True))
    except Failure as caught:
        require(caught.category == "schema" and not count, "source")
    else:
        raise Failure("source")
    ledger = diagnostic_witness_initial()
    ledger.update(capture="complete", stdout="complete", projection="complete")
    diagnostic_note_invalidation(ledger, "capture-save")
    diagnostic_note_invalidation(ledger, "measurement-save")
    require(
        ledger["capture"] == "invalidated"
        and ledger["invalidated_by"] == "capture-save"
        and ledger["stdout"] == ledger["projection"] == "complete",
        "source",
    )
    validate_diagnostic_witness(ledger)
    for key, bad in (("capture", True), ("stdout", "unknown"), ("projection", None), ("invalidated_by", False)):
        try:
            validate_diagnostic_witness({**ledger, key: bad})
        except Failure:
            pass
        else:
            raise Failure("source")
    for bad in ({key: value for key, value in ledger.items() if key != "stderr"}, {**ledger, "extra": None}):
        try:
            validate_diagnostic_witness(bad)
        except Failure:
            pass
        else:
            raise Failure("source")


def pre_pr_diagnostic():
    value = state["pre_pr_measurement"]
    ledger = state["pre_pr_diagnostic_witness"]
    require(value["admission"] == "admitted", "source")
    quality = next((row for row in value["stages"] if row["stage"] == "quality"), None)
    if quality is None or quality["status"] == "complete":
        value["diagnostic"] = {"admission": "not-needed", "records": []}
        diagnostic_attach(value)
        validate_pre_pr_measurement(value)
        save()
        return
    require(quality["status"] == "failed", "source")
    witness = state.get("pre_pr_literal_capture")
    index = state.get("pre_pr_literal_child_index")
    children = state.get("children", [])
    ledger["capture"] = "incomplete"
    try:
        require(
            literal_capture_binding(witness, children, index, state["raw_identity"], state.get("capture_counter"))
            and type(state.get("raw_bytes")) is int
            and 0 <= state["raw_bytes"] <= RAW_TOTAL,
            "acquisition",
        )
    except BaseException:
        with suppress(BaseException):
            invalidate_literal_capture("capture-binding")
        raise
    ledger["capture"] = "complete"
    statuses, records = [], []
    original = None
    source_lines = {}
    for stream in ("stdout", "stderr"):
        observer = None
        try:
            observer = DiagnosticObserver()

            def location(path, observer=observer):
                if path not in state.get("pre_pr_diagnostic_git", {}):
                    return None
                if path not in source_lines:
                    source_lines[path] = diagnostic_operation(
                        observer, "internal", lambda: diagnostic_source_lines(path, diagnostic_observer=observer)
                    )
                return source_lines[path]

            raw = diagnostic_operation(
                observer,
                "internal",
                lambda stream=stream, observer=observer: literal_capture_read(
                    witness, stream, diagnostic_observer=observer
                ),
            )
            records.extend(
                diagnostic_operation(
                    observer,
                    "internal",
                    lambda raw=raw, location=location, observer=observer: diagnostic_records(
                        raw, location, diagnostic_observer=observer
                    ),
                )
            )
            diagnostic_finish_stream(observer, remaining)
            ledger[stream] = "complete"
        except BaseException as error:
            with suppress(BaseException):
                ledger[stream] = (
                    observer.failure[1]
                    if observer is not None and observer.failure is not None
                    else diagnostic_reason("internal", error)
                )
            statuses.append(
                "bound"
                if isinstance(error, Failure) and error.category == "bound"
                else "missing"
                if isinstance(error, FileNotFoundError)
                else "invalid"
            )
            if original is None:
                original = error
    try:
        value["diagnostic"] = diagnostic_projection(statuses, records)
        diagnostic_attach(value)
        if not diagnostic_fit(value) and original is None:
            original = Failure("bound")
        if value["diagnostic"]["admission"] == "bound" and original is None:
            original = Failure("bound")
        if value["diagnostic"]["admission"] == "bound":
            ledger["projection"] = "bound"
            invalidate_literal_capture("projection")
        if original is not None:
            invalidate_literal_capture("stream-failure")
        diagnostic_attach(value)
        validate_pre_pr_measurement(value)
        if ledger["projection"] != "bound":
            ledger["projection"] = "complete"
        diagnostic_attach(value)
    except BaseException as error:
        with suppress(BaseException):
            ledger["projection"] = "bound" if isinstance(error, Failure) and error.category == "bound" else "invalid"
            invalidate_literal_capture("projection")
        if original is None:
            original = error
    try:
        save()
    except BaseException as error:
        if original is None:
            original = error
    if original is not None:
        raise original


def quality_build_allowed(fresh, started, skip):
    return fresh is True and started is True and type(skip) is str and skip != "1"


def diagnostic_tracked_file(tracked):
    return (
        type(tracked) is dict
        and tracked.get("mode") in {"100644", "100755"}
        and tracked.get("kind") == "blob"
        and type(tracked.get("oid")) is str
        and re.fullmatch(r"[0-9a-f]{40}", tracked["oid"]) is not None
    )


def diagnostic_fit(value):
    if len(canonical(value)) > 16384:
        witness = value["diagnostic"].get("witness")
        value["diagnostic"] = {"admission": "bound", "records": []}
        if witness is not None:
            value["diagnostic"]["witness"] = witness
        return False
    return True


def complete_build_stage(measurement, stage, name):
    return (
        type(measurement) is dict
        and measurement.get("admission") == "admitted"
        and name in {"stack", "quality"}
        and type(stage) is dict
        and stage.get("status") == "complete"
        and type(stage.get("signature")) is dict
        and type(stage["signature"].get("compose_images")) is list
        and any(row["stage"] == name and row["status"] == "complete" for row in measurement["stages"])
    )


def retained_build_witness(measurement, record, initial_ids, stage, project):
    witness = record.get("witness")
    provenance = measurement.get("image_witness")
    name = {"stack-complete": "stack", "quality-complete": "quality"}.get(provenance)
    if not (
        name is not None
        and complete_build_stage(measurement, stage, name)
        and type(record.get("id")) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", record["id"]) is not None
        and record["id"] not in initial_ids
        and record["id"] in stage["signature"]["compose_images"]
        and type(witness) is dict
        and witness.get("provenance") == provenance
        and type(witness.get("image")) is dict
        and witness["image"].get("Id") == record["id"]
        and witness.get("project") == project
        and witness.get("service") in {"init", "api", "worker", "scheduler", "scheduler-fixtures", "test-runner"}
        and (name != "quality" or witness["service"] == "test-runner")
        and type(witness["image"].get("Config")) is dict
        and type(witness["image"]["Config"].get("Labels")) is dict
    ):
        return False
    labels = witness["image"]["Config"]["Labels"]
    return (
        labels.get("com.docker.compose.project") == project
        and labels.get("com.docker.compose.service") == witness["service"]
        and witness.get("build")
        == {
            "project": project,
            "service": witness["service"],
            "image": record["tag"],
            "context": str(ROOT),
            "dockerfile": "api/Dockerfile.dev",
        }
    )


def pre_pr_diagnostic_controls():
    # Pure same-helper controls execute only in the supported pre-PR scope.
    # Their synthetic facts are never substituted for actual child/image custody.
    diagnostic_reason_controls()

    def location(path):
        return 20 if path == "api/src/probe.py" else None

    envelopes = {
        "pyright-no-rule": b"  /app/src/probe.py:1:2 - error: discarded\n",
        "pyright-unknown-rule": b"  src/probe.py:1:2 - error: discarded (arbitraryRule)\n",
        "pyright-multiline": b"  src/probe.py:1:2 - error: discarded\n  continuation (arbitraryRule)\n",
        "pyright-warning": b"  src/probe.py:1:2 - warning: discarded\n",
        "pyright-information": b"  src/probe.py:1:2 - information: discarded\n",
        "ruff-hidden": b"ZZ999 discarded\n --> src/probe.py:1:2\n",
        "ruff-fix": b"F401 [*] discarded\n --> src/probe.py:1:2\n",
        "ruff-syntax": b"invalid-syntax: discarded\n --> src/probe.py:1:2\n",
        "ruff-explicit-error": b"error[F401]: discarded\n --> src/probe.py:1:2\n",
        "ruff-explicit-warning": b"warning[F401]: discarded\n --> src/probe.py:1:2\n",
        "ruff-explicit-info": b"info[F401]: discarded\n --> src/probe.py:1:2\n",
        "ruff-explicit-fix": b"error[F401][*]: discarded\n --> src/probe.py:1:2\n",
        "ruff-two-space-arrow": b"F401 discarded\n  --> src/probe.py:1:2\n",
        "crlf": b"  src/probe.py:1:2 - error: discarded\r\n",
        "spoof-still-unauthenticated": b"F401 arbitrary fake payload\n --> src/probe.py:1:2\n",
    }
    for label, raw in envelopes.items():
        records = diagnostic_records(raw, location)
        expected_tool = "pyright" if label.startswith("pyright") or label == "crlf" else "ruff"
        expected_severity = (
            "warning"
            if label.endswith("warning")
            else "information"
            if label.endswith(("information", "info"))
            else "error"
            if expected_tool == "pyright" or label.startswith("ruff-explicit")
            else "unknown"
        )
        require(
            len(records) == 1
            and records[0]
            == {
                "tool": expected_tool,
                "code": "unclassified",
                "severity": expected_severity,
                "source_path": "api/src/probe.py",
                "line": 1,
                "column": 2,
            },
            "source",
        )
        validate_diagnostic(diagnostic_projection([], records))
    unmatched = {
        "empty": b"",
        "build-only": b"build failed\n",
        "ruff-no-arrow": b"F401 discarded\n",
        "ruff-three-space-arrow": b"F401 discarded\n   --> src/probe.py:1:2\n",
        "ruff-secondary-arrow": b"F401 discarded\n source excerpt\n --> src/probe.py:1:2\n",
        "ruff-unsupported-punctuation": b"error F401 discarded\n --> src/probe.py:1:2\n",
        "pyright-wrong-prefix": b" /app/src/probe.py:1:2 - error: discarded\n",
        "untracked": b"  src/absent.py:1:2 - error: discarded\n",
        "traversal": b"  src/../probe.py:1:2 - error: discarded\n",
        "case-mismatch": b"  src/Probe.py:1:2 - error: discarded\n",
        "host-path": b"  /home/runner/probe.py:1:2 - error: discarded\n",
        "renderer-excluded": b"  /app/doc_renderer_service/probe.py:1:2 - error: discarded\n",
        "source-line-overflow": b"  src/probe.py:21:2 - error: discarded\n",
        "zero-line": b"  src/probe.py:0:2 - error: discarded\n",
        "zero-column": b"  src/probe.py:1:0 - error: discarded\n",
        "leading-zero": b"  src/probe.py:01:2 - error: discarded\n",
        "coordinate-overflow": b"  src/probe.py:1:1048577 - error: discarded\n",
    }
    for raw in unmatched.values():
        require(not diagnostic_records(raw, location), "source")
    for prefix in DIAGNOSTIC_SHADOWS:
        require(diagnostic_path(prefix + "probe.py") is None, "source")
    for raw in (b"\xff\n", b"NUL\0\n", b"\x1b[31mtext\n", b"a\rb\n", b"no final newline", b"x" * (RAW_LIMIT + 1)):
        try:
            diagnostic_records(raw, location)
        except (Failure, UnicodeError):
            pass
        else:
            raise Failure("source")
    baseline = diagnostic_records(envelopes["pyright-no-rule"], location)
    require(diagnostic_projection([], baseline + baseline)["records"] == baseline, "source")
    for statuses, expected in (
        (["missing"], "missing"),
        (["invalid"], "invalid"),
        (["bound"], "bound"),
        (["missing", "invalid"], "invalid"),
        (["missing", "invalid", "bound"], "bound"),
    ):
        require(diagnostic_projection(statuses, baseline) == {"admission": expected, "records": []}, "source")
    overflow = [{**baseline[0], "column": number} for number in range(1, 34)]
    require(diagnostic_projection([], overflow) == {"admission": "bound", "records": []}, "source")
    nullable = {**baseline[0], "column": None}
    ordered = diagnostic_projection([], [*baseline, nullable])
    require(ordered["records"] == [nullable, baseline[0]], "source")
    validate_diagnostic(ordered)
    for field, invalid in (("line", True), ("column", False), ("line", 0), ("column", 1048577)):
        try:
            validate_diagnostic({"admission": "observed", "records": [{**baseline[0], field: invalid}]})
        except Failure:
            pass
        else:
            raise Failure("source")
    require(diagnostic_record("pyright", "error", "src/probe.py", True, 2, location) is None, "source")
    require(diagnostic_record("pyright", "error", "src/probe.py", 1, False, location) is None, "source")

    for tracked in (
        None,
        {"mode": "120000", "kind": "blob", "oid": "1" * 40},
        {"mode": "160000", "kind": "commit", "oid": "1" * 40},
    ):
        require(not diagnostic_tracked_file(tracked), "source")
    require(diagnostic_tracked_file({"mode": "100644", "kind": "blob", "oid": "1" * 40}), "source")
    too_large = {"diagnostic": {"admission": "observed", "records": baseline}, "padding": "x" * 16384}
    require(
        not diagnostic_fit(too_large) and too_large["diagnostic"] == {"admission": "bound", "records": []}, "source"
    )
    for fresh, started, skip in ((False, True, "0"), (True, False, "0"), (True, True, "1")):
        require(not quality_build_allowed(fresh, started, skip), "source")
    require(quality_build_allowed(True, True, "0"), "source")

    require(literal_capture_selected("literal-pre-pr", 0, 0), "source")
    for operation_name, index, count in (
        ("literal-pre-pr", 0, 1),
        ("literal-pre-pr", True, 1),
        ("source-before", 0, 0),
        ("literal-pre-pr", -1, -1),
    ):
        require(not literal_capture_selected(operation_name, index, count), "source")

    regular = (stat.S_IFREG | 0o600, 1, 1, 1, os.getuid(), 0, 0, 0, 0, 0)
    require(capture_file_identity(os.stat_result(regular))["mode"] == 0o600, "source")
    for offset, invalid in ((0, stat.S_IFREG | 0o644), (0, stat.S_IFDIR | 0o600), (3, 2), (4, os.getuid() + 1)):
        changed = list(regular)
        changed[offset] = invalid
        try:
            capture_file_identity(os.stat_result(changed))
        except Failure:
            pass
        else:
            raise Failure("source")

    acquired = {"dev": 1, "ino": 1, "uid": 1, "mode": 0o600, "nlink": 1}
    finalized = {"identity": acquired, "size": 0, "mtime": 0, "ctime": 0}
    witness = {
        "operation": "literal-pre-pr",
        "capture_complete": True,
        "native_wait_completed": True,
        "cleanup_failed": False,
        "exit": 1,
        "acquired": {stream: acquired for stream in ("stdout", "stderr")},
        "final": {stream: finalized for stream in ("stdout", "stderr")},
        "eof": {"stdout": True, "stderr": True},
        "closed": {"stdout": True, "stderr": True},
        "bytes": {"stdout": 0, "stderr": 0},
    }
    row = {
        "operation": "literal-pre-pr",
        "exit": 1,
        "bytes": {"stdout": 0, "stderr": 0},
        "stdin_bytes": 0,
        "reaped": True,
        "cleanup_failed": False,
    }
    for native_exit in (0, 1, -15):
        require(literal_capture_complete({**witness, "exit": native_exit}, {**row, "exit": native_exit}), "source")
    branches = [
        ("no-acquisition", {**witness, "acquired": {}}),
        ("one-acquisition", {**witness, "acquired": {"stdout": acquired}}),
        ("fstat-failed", {**witness, "final": {}}),
        ("popen-failed", {**witness, "native_wait_completed": False}),
        ("stdout-only-eof", {**witness, "eof": {"stdout": True, "stderr": False}}),
        ("stderr-only-eof", {**witness, "eof": {"stdout": False, "stderr": True}}),
        ("timeout-reaped-prefix", {**witness, "capture_complete": False}),
        ("bound-reaped-prefix", {**witness, "capture_complete": False}),
        ("partial-write", {**witness, "bytes": {"stdout": 1, "stderr": 0}}),
        (
            "identity-replacement",
            {
                **witness,
                "final": {
                    "stdout": {**finalized, "identity": {**acquired, "ino": 2}},
                    "stderr": finalized,
                },
            },
        ),
        (
            "final-size-mismatch",
            {
                **witness,
                "final": {
                    "stdout": {**finalized, "size": 1},
                    "stderr": finalized,
                },
            },
        ),
        ("stdout-close-failed", {**witness, "closed": {"stdout": False, "stderr": True}}),
        ("stderr-close-failed", {**witness, "closed": {"stdout": True, "stderr": False}}),
        ("fallback-reap-only", {**witness, "native_wait_completed": False}),
        ("bookkeeping-save-failed", {**witness, "capture_complete": False}),
        ("cleanup-failed", {**witness, "cleanup_failed": True}),
    ]
    for _label, incomplete in branches:
        require(not literal_capture_complete(incomplete, row), "source")
    for changed in ({**row, "exit": 0}, {**row, "reaped": False}, {**row, "operation": "source-before"}):
        require(not literal_capture_complete(witness, changed), "source")

    directory_identity = {"dev": 1, "ino": 1, "uid": 1, "mode": 0o700}
    bound_witness = {
        **witness,
        "child_index": 0,
        "capture_number": 7,
        "directory": directory_identity,
        "paths": {"stdout": str(RAW_DIRECTORY / "7.stdout"), "stderr": str(RAW_DIRECTORY / "7.stderr")},
    }
    require(literal_capture_binding(bound_witness, [row], 0, directory_identity, 7), "source")
    for index in (True, -1, 1):
        require(not literal_capture_binding(bound_witness, [row], index, directory_identity, 7), "source")
    for field, invalid in (
        ("child_index", 1),
        ("capture_number", 8),
        ("capture_number", True),
        ("paths", {}),
        ("directory", {}),
    ):
        require(
            not literal_capture_binding({**bound_witness, field: invalid}, [row], 0, directory_identity, 7), "source"
        )

    image_id = "sha256:" + "1" * 64
    project = "synthetic-control"
    measurement = {
        "admission": "admitted",
        "stages": [{"stage": "quality", "status": "complete"}],
        "image_witness": "quality-complete",
    }
    stage = {"status": "complete", "signature": {"compose_images": [image_id]}}
    record = {
        "id": image_id,
        "tag": "synthetic-control:only",
        "witness": {
            "provenance": "quality-complete",
            "project": project,
            "service": "test-runner",
            "image": {
                "Id": image_id,
                "Config": {
                    "Labels": {"com.docker.compose.project": project, "com.docker.compose.service": "test-runner"}
                },
            },
            "build": {
                "project": project,
                "service": "test-runner",
                "image": "synthetic-control:only",
                "context": str(ROOT),
                "dockerfile": "api/Dockerfile.dev",
            },
        },
    }
    require(retained_build_witness(measurement, record, [], stage, project), "source")
    for status in ("failed", "running"):
        require(not retained_build_witness(measurement, record, [], {**stage, "status": status}, project), "source")
    require(not retained_build_witness(measurement, record, [], None, project), "source")
    require(not retained_build_witness(measurement, record, [image_id], stage, project), "source")
    require(
        not retained_build_witness(measurement, record, [], {**stage, "signature": {"compose_images": []}}, project),
        "source",
    )
    require(not retained_build_witness(measurement, record, [], stage, "foreign"), "source")
    require(not retained_build_witness(measurement, {**record, "witness": None}, [], stage, project), "source")
    require(not retained_build_witness(measurement, {**record, "id": None}, [], stage, project), "source")
    require(
        not retained_build_witness(measurement, {**record, "id": "sha256:" + "2" * 64}, [], stage, project), "source"
    )
    for field, invalid in (("service", "api"), ("build", {}), ("provenance", "stack-complete")):
        changed = {**record, "witness": {**record["witness"], field: invalid}}
        require(not retained_build_witness(measurement, changed, [], stage, project), "source")
    later_failure = {**measurement, "stages": measurement["stages"] + [{"stage": "generated", "status": "failed"}]}
    require(retained_build_witness(later_failure, record, [], stage, project), "source")
    stack_measurement = {
        "admission": "admitted",
        "stages": [{"stage": "stack", "status": "complete"}],
        "image_witness": "stack-complete",
    }
    stack_record = {**record, "witness": {**record["witness"], "provenance": "stack-complete"}}
    require(retained_build_witness(stack_measurement, stack_record, [], stage, project), "source")


def validate_pre_pr_measurement(value):
    require(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "candidate_sha",
            "run_id",
            "run_attempt",
            "admission",
            "literal_exit",
            "stages",
            "image_witness",
            "diagnostic",
        }
        and value["schema"] == "bifrost.test.agent-prepare-pre-pr-measurement/v3"
        and value["candidate_sha"] == receipt["candidate"]["checkout_sha"]
        and type(value["candidate_sha"]) is str
        and re.fullmatch(r"[0-9a-f]{40}", value["candidate_sha"])
        and value["run_id"] == os.environ["GITHUB_RUN_ID"]
        and value["run_attempt"] == os.environ["GITHUB_RUN_ATTEMPT"]
        and all(type(value[k]) is str and re.fullmatch(r"[0-9]+", value[k]) for k in ("run_id", "run_attempt"))
        and type(value["admission"]) is str
        and value["admission"] in {"admitted", "missing", "invalid"}
        and (
            value["literal_exit"] is None
            or (type(value["literal_exit"]) is int and -255 <= value["literal_exit"] <= 255)
        )
        and type(value["stages"]) is list
        and len(value["stages"]) <= 11
        and type(value["image_witness"]) is str
        and value["image_witness"] in {"none", "stack-complete", "quality-complete"},
        "schema",
    )
    require(
        type(value["diagnostic"]) is dict and set(value["diagnostic"]) == {"admission", "records", "witness"}, "schema"
    )
    validate_diagnostic_witness(value["diagnostic"]["witness"])
    validate_diagnostic({key: value["diagnostic"][key] for key in ("admission", "records")})
    if value["diagnostic"]["admission"] == "observed":
        require(
            literal_capture_binding(
                state.get("pre_pr_literal_capture"),
                state.get("children", []),
                state.get("pre_pr_literal_child_index"),
                state["raw_identity"],
                state.get("capture_counter"),
            ),
            "acquisition",
        )
    names = []
    for row in value["stages"]:
        require(
            type(row) is dict
            and set(row) == {"stage", "status", "command_exit"}
            and type(row["stage"]) is str
            and row["stage"] in PRE_PR_STAGES
            and type(row["status"]) is str
            and row["status"] in {"running", "complete", "failed"}
            and row["command_exit"] is None,
            "schema",
        )
        names.append(row["stage"])
    require(names == sorted(set(names)), "schema")
    quality = next((row for row in value["stages"] if row["stage"] == "quality"), None)
    if value["diagnostic"]["admission"] == "observed":
        require(value["admission"] == "admitted" and quality is not None and quality["status"] == "failed", "schema")
    if value["diagnostic"]["admission"] == "not-needed":
        require(value["admission"] == "admitted" and (quality is None or quality["status"] == "complete"), "schema")
    if value["admission"] != "admitted":
        require(not names and value["image_witness"] == "none", "schema")
    if value["image_witness"] != "none":
        name = {"stack-complete": "stack", "quality-complete": "quality"}[value["image_witness"]]
        require(any(row["stage"] == name and row["status"] == "complete" for row in value["stages"]), "schema")
    require(len(canonical(value)) <= 16384, "bound")


def pre_pr_capture():
    value = state["pre_pr_measurement"]
    # Operation exit may hold a timeout/control classification rather than a
    # child return. Only the first actual literal child's retained wait result
    # supplies this separate measurement, including negative signal returns.
    index = state.get("pre_pr_literal_child_index")
    children = state.get("children", [])
    if index is not None and len(children) > index:
        child_record = children[index]
        require(child_record["operation"] == "literal-pre-pr", "source")
        value["literal_exit"] = child_record["exit"]
    else:
        # A later row-retention failure must not erase an already measured wait.
        witness = state.get("pre_pr_literal_capture")
        if (
            type(witness) is dict
            and witness.get("native_wait_completed") is True
            and witness.get("child_index") == index
        ):
            require(type(witness.get("exit")) is int and -255 <= witness["exit"] <= 255, "source")
            value["literal_exit"] = witness["exit"]
    ledger = Path(state["pre_pr_ledger"])
    plan = Path(state["pre_pr_plan"])
    if not ledger.exists() or not plan.exists():
        value["admission"] = "missing"
        raise Failure("source")
    require(state["pre_pr_fresh"] is True, "source")
    require(pre_pr_environment() == state["pre_pr_environment"], "source")
    current = pre_pr_snapshot(state["pre_pr_environment"]["BIFROST_TEST_ENV_FILE"], ledger)
    initial = state["pre_pr_snapshot"]
    require(
        {k: v for k, v in current.items() if k != "compose_images"}
        == {k: v for k, v in initial.items() if k != "compose_images"},
        "source",
    )
    raw_plan = pre_pr_file(plan)
    parsed_plan = decode(raw_plan, 65536)
    require(
        type(parsed_plan) is dict and parsed_plan.get("scope") in {"affected", "comprehensive", "docs-only"}, "source"
    )
    context = "scope=" + parsed_plan["scope"] + ";full=0;plan=" + sha_bytes(raw_plan)
    observed = decode(pre_pr_file(ledger), 65536)
    require(type(observed) is dict and set(observed) == {"stages"} and type(observed["stages"]) is dict, "schema")
    require(0 < len(observed["stages"]) <= 11 and set(observed["stages"]) <= PRE_PR_STAGES, "schema")
    for path in ("test.sh", "scripts/lib/pre_pr_stage_evidence.py", "docker-compose.test.yml"):
        require(sha_file(ROOT / path) == EXPECTED_SOURCE[path], "source")
    common_keys = {
        "head",
        "status",
        "compose_sha256",
        "env_sha256",
        "docker_version",
        "compose_version",
        "python_version",
        "node_version",
    }
    rows = []
    for name, row in sorted(observed["stages"].items()):
        require(
            type(row) is dict
            and set(row) == {"status", "context", "signature"}
            and type(row["status"]) is str
            and row["status"] in {"running", "complete", "failed"}
            and row["context"] == context
            and type(row["signature"]) is dict,
            "schema",
        )
        keys = common_keys | ({"compose_images"} if name != "repository" else set())
        if name == "browser":
            keys |= {"browser_config_sha256"}
        # start intentionally excludes mutable build-image IDs for these stages.
        if row["status"] == "running" and name in {"client", "stack", "quality", "browser", "image"}:
            keys -= {"compose_images"}
        require(set(row["signature"]) == keys, "schema")
        # The authoritative helper selects client/test profiles for these
        # stages; their genuine Compose hashes differ from default stack.
        stage_snapshot = current
        if name in {"client", "client-unit", "browser", "mcp"}:
            stage_snapshot = pre_pr_snapshot(state["pre_pr_environment"]["BIFROST_TEST_ENV_FILE"], ledger, name)
        for key in keys - {"compose_images"}:
            require(type(row["signature"][key]) is str and row["signature"][key] == stage_snapshot[key], "source")
        if "compose_images" in keys:
            ids = row["signature"]["compose_images"]
            require(type(ids) is list and len(ids) <= 128 and ids == sorted(set(ids)), "schema")
            require(all(type(item) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in ids), "schema")
        rows.append({"stage": name, "status": row["status"], "command_exit": None})
    # Commit admitted rows only after every private record was validated.
    value.update(admission="admitted", stages=rows)
    state["pre_pr_stack_record"] = observed["stages"].get("stack")
    state["pre_pr_quality_record"] = observed["stages"].get("quality")
    validate_pre_pr_measurement(value)
    save()


def pre_pr_image_witness():
    value = state["pre_pr_measurement"]
    record = state["pre_pr_api_tag"]
    stack = state.get("pre_pr_stack_record")
    quality = state.get("pre_pr_quality_record")
    if complete_build_stage(value, stack, "stack"):
        stage = stack
        provenance = "stack-complete"
        allowed = {"init", "api", "worker", "scheduler", "scheduler-fixtures", "test-runner"}
    else:
        require(complete_build_stage(value, quality, "quality"), "acquisition")
        require(
            quality_build_allowed(
                state.get("pre_pr_fresh"),
                state.get("pre_pr_started"),
                state["pre_pr_environment"]["BIFROST_SKIP_BUILD"],
            ),
            "acquisition",
        )
        stage = quality
        provenance = "quality-complete"
        allowed = {"test-runner"}
    current = capture(["docker", "image", "ls", "-q", "--no-trunc", record["tag"]])
    # Authoritative helper inspects ALL configured tags. Empty membership remains
    # HOLD even after completed quality; never narrow or synthesize the signature.
    require(
        current and current not in state["initial_image_ids"] and current in stage["signature"]["compose_images"],
        "acquisition",
    )
    facts = inspect("image", current)
    labels = facts["Config"].get("Labels") or {}
    require(
        facts["Id"] == current
        and record["tag"] in facts.get("RepoTags", [])
        and labels.get("com.docker.compose.project") == state["project"]
        and labels.get("com.docker.compose.service") in allowed,
        "acquisition",
    )
    # This completed build association does not establish stack/API readiness.
    config = decode(
        capture(
            [
                "bash",
                "-c",
                'source ./test.sh help >/dev/null; docker compose -f "$COMPOSE_FILE" --profile e2e --profile test config --format json',
                "./test.sh",
            ]
        ).encode()
    )
    require(type(config) is dict and config.get("name") == state["project"], "acquisition")
    for name in allowed:
        service = config["services"][name]
        build = service["build"]
        require(
            service["image"] == record["tag"]
            and type(build) is dict
            and Path(build["context"]) == ROOT
            and build["dockerfile"] == "api/Dockerfile.dev",
            "acquisition",
        )
    record["id"] = current
    record["witness"] = {
        "project": state["project"],
        "service": labels["com.docker.compose.service"],
        "literal_exit": value["literal_exit"],
        "provenance": provenance,
        "image": facts,
        "build": {
            "project": state["project"],
            "service": labels["com.docker.compose.service"],
            "image": record["tag"],
            "context": str(ROOT),
            "dockerfile": "api/Dockerfile.dev",
        },
    }
    value["image_witness"] = provenance
    require(retained_build_witness(value, record, state["initial_image_ids"], stage, state["project"]), "acquisition")
    validate_pre_pr_measurement(value)
    save()


def pre_pr():
    state["pre_pr_diagnostic_witness"] = diagnostic_witness_initial()
    state["pre_pr_measurement"] = {
        "schema": "bifrost.test.agent-prepare-pre-pr-measurement/v3",
        "candidate_sha": receipt["candidate"]["checkout_sha"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "admission": "invalid",
        "literal_exit": None,
        "stages": [],
        "image_witness": "none",
        "diagnostic": {"admission": "invalid", "records": []},
    }
    diagnostic_attach(state["pre_pr_measurement"])
    begin("pre-pr")
    original = None
    try:
        pre_pr_diagnostic_controls()
        for key in ("BIFROST_ACTION_PIN_TOKEN_FILE", "SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY"):
            require(os.environ.get(key), "credential")
            state[key] = os.environ[key]
        metadata = Path(capture(["git", "rev-parse", "--absolute-git-dir"]))
        directory = metadata / "bifrost-test-locks"
        ledger = Path(
            capture(
                ["git", "rev-parse", "--path-format=absolute", "--git-path", "bifrost-test-locks/pre-pr-stages.json"]
            )
        )
        plan = directory / "pre-pr-affected-plan.json"
        require(metadata == metadata.resolve() and ledger == directory / "pre-pr-stages.json", "acquisition")
        require(
            directory == directory.resolve() and not os.path.lexists(ledger) and not os.path.lexists(plan),
            "acquisition",
        )
        state.update(pre_pr_ledger=str(ledger), pre_pr_plan=str(plan), pre_pr_fresh=True)
        state["pre_pr_environment"] = pre_pr_environment()
        state["pre_pr_snapshot"] = pre_pr_snapshot(state["pre_pr_environment"]["BIFROST_TEST_ENV_FILE"], ledger)
        require(state["pre_pr_snapshot"]["env_sha256"] == state["pre_pr_environment"]["env_digest"], "source")
        for path in ("test.sh", "scripts/lib/pre_pr_stage_evidence.py", "docker-compose.test.yml"):
            require(sha_file(ROOT / path) == EXPECTED_SOURCE[path], "source")
        require(not os.path.lexists(ledger) and not os.path.lexists(plan), "acquisition")
        state["pre_pr_started"] = True
        save()
        with operation("literal-pre-pr"):
            env = clean_env()
            env["BIFROST_ACTION_PIN_TOKEN_FILE"] = state["BIFROST_ACTION_PIN_TOKEN_FILE"]
            state["pre_pr_literal_child_index"] = len(state.get("children", []))
            command(["./test.sh", "pre-pr"], env=env)
            state["pre_pr_exit_zero"] = True
            require(not capture(["git", "status", "--porcelain", "--untracked-files=all"]), "source")
    except BaseException as error:
        original = error
    finally:
        # Capture, genuine image acquisition and credential disposal are independent;
        # none can replace the first original command/control object.
        for callback in (pre_pr_capture, pre_pr_diagnostic, pre_pr_image_witness):
            try:
                callback()
            except BaseException as error:
                if callback in (pre_pr_capture, pre_pr_diagnostic):
                    with suppress(BaseException):
                        invalidate_literal_capture("pre-pr-capture" if callback is pre_pr_capture else "stream-failure")
                if original is None:
                    original = error
        try:
            with operation("action-disposal"):
                credential_cleanup()
        except BaseException as error:
            if original is None:
                original = error
        try:
            save()
        except BaseException as error:
            if original is None:
                original = error
    if original is not None:
        raise original
    require(absent_owned(state["project"], PREFIX), "cleanup")
    receipt["cleanup"]["pre_pr_final_empty"] = True
    end_stage()


def prepare():
    require(receipt["credentials"]["action_disposed"], "credential")
    begin("prepare")
    original = None
    config = None
    env = clean_env()
    state["prepare_tags"] = []
    try:
        config = directory("ghcr-config")
        env = clean_env()
        env["DOCKER_CONFIG"] = str(config)
        for key in ("GHCR_TOKEN", "GHCR_USERNAME"):
            require(os.environ.get(key), "credential")
            env[key] = os.environ[key]
        remote = os.environ["REGISTRY"] + "/" + os.environ["CI_API_TEST_IMAGE"] + ":" + os.environ["CI_TEST_IMAGE_TAG"]
        tags = ["bifrost-test-api-dev:latest", remote]
        state["prepare_tags"] = []
        for tag in tags:
            previous = capture(["docker", "image", "ls", "-q", "--no-trunc", tag])
            state["prepare_tags"].append({"tag": tag, "previous": previous, "id": None, "removed": False})
            require(
                not previous
                or (
                    tag == tags[0]
                    and previous == state["pre_pr_api_tag"]["id"]
                    and state["pre_pr_api_tag"]["witness"] is not None
                ),
                "acquisition",
            )
            if previous:
                facts = inspect("image", previous)
                require(facts == state["pre_pr_api_tag"]["witness"]["image"], "acquisition")
        save()
        with operation("api-prepare"):
            command(["bash", "api/scripts/ci/prepare-test-images.sh", "api"], env=env)
            state["api_image"] = capture(["docker", "image", "inspect", tags[0], "--format", "{{.Id}}"])
    except BaseException as error:
        original = error
    finally:
        for record in state["prepare_tags"]:
            try:
                current = capture(["docker", "image", "ls", "-q", "--no-trunc", record["tag"]])
                if current:
                    require(not record["previous"] or record["tag"] == tags[0], "acquisition")
                    record["id"] = current
                save()
            except BaseException as error:
                if original is None:
                    original = error
        try:
            with operation("ghcr-disposal"):
                require(config is not None, "credential")
                command(["docker", "logout", "ghcr.io"], env=env)
                config_file = config / "config.json"
                if config_file.exists():
                    value = decode(config_file.read_bytes())
                    require(
                        not value.get("credsStore")
                        and not value.get("credHelpers")
                        and not any(value.get("auths", {}).values()),
                        "credential",
                    )
                record = next(v for v in state["directories"] if v["role"] == "ghcr-config")
                shutil.rmtree(checked_directory(config, record["identity"]))
                require(not config.exists(), "credential")
                record["removed"] = True
                receipt["credentials"]["ghcr_disposed"] = True
        except BaseException as error:
            if original is None:
                original = error
    if original is not None:
        raise original
    _, version, _ = python(["--version"])
    text = version.read_text().strip()
    require(re.fullmatch(r"Python 3\.14\.[0-9]+", text) is not None, "image")
    _, digest, _ = python(
        ["-c", "import hashlib,sys; print(hashlib.sha256(open(sys.executable,'rb').read()).hexdigest())"]
    )
    digest_value = digest.read_text().strip()
    require(re.fullmatch(r"[0-9a-f]{64}", digest_value) is not None, "image")
    receipt["images"]["api"] = {
        "image_id": state["api_image"],
        "python_version": text,
        "python_sha256": digest_value,
        "configuration_verified": True,
    }
    end_stage()


def toolchain():
    begin("toolchain")
    with operation("toolchain-build"):
        tag = PREFIX + ":toolchain"
        require(not capture(["docker", "image", "ls", "-q", tag]), "acquisition")
        record = {"tag": tag, "id": None, "previous": "", "removed": False}
        state.setdefault("images", []).append(record)
        save()
        command(["docker", "build", "--target", "toolchain", "-t", tag, "-f", "core-rs/Dockerfile", "core-rs"])
        state["toolchain_image"] = capture(["docker", "image", "inspect", tag, "--format", "{{.Id}}"])
        record["id"] = state["toolchain_image"]
        _, rustc, _ = container(record["id"], ["rustc", "--version"])
        _, cargo, _ = container(record["id"], ["cargo", "--version"])
        rv, cv = rustc.read_text().strip(), cargo.read_text().strip()
        require(re.fullmatch(r"rustc 1\.98\.1 \([0-9a-f]+ [0-9-]+\)", rv) is not None, "image")
        require(re.fullmatch(r"cargo 1\.98\.1 \([0-9a-f]+ [0-9-]+\)", cv) is not None, "image")
        receipt["images"]["toolchain"] = {
            "image_id": record["id"],
            "rustc_version": rv,
            "cargo_version": cv,
            "configuration_verified": True,
        }
    end_stage()


def rust_test_counts(output, expected):
    text = output.read_text()
    rows = re.findall(r"^test ([A-Za-z0-9_:]+) \.\.\. (ok|FAILED|ignored)(?: .*)?$", text, re.MULTILINE)
    names = [name for name, _ in rows]
    require(Counter(names) == Counter(expected), "test")
    require(all(status == "ok" for _, status in rows), "test")
    require(not re.search(r"\b[1-9][0-9]* (?:failed|ignored|skipped)\b", text), "test")
    summaries = re.findall(r"test result: ok\. ([0-9]+) passed; ([0-9]+) failed; ([0-9]+) ignored;", text)
    require(summaries and sum(int(p) for p, _, _ in summaries) == len(rows), "test")
    require(all(int(f) == 0 and int(i) == 0 for _, f, i in summaries), "test")
    return {"passed": len(rows), "failed": 0, "skipped": 0, "ignored": 0, "roster_verified": True}


def python_test_counts(record, expected, module):
    # cp's stream is capped DURING capture; raw XML remains in the private directory.
    archive, _ = command(["docker", "cp", record["id"] + ":/tmp/bifrost/test-results.xml", "-"], limit=RAW_LIMIT)
    import io
    import tarfile

    original = None
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.read_bytes()), mode="r:") as tar:
            file = None
            try:
                members = tar.getmembers()
                require(len(members) == 1 and members[0].isfile() and 0 < members[0].size <= RAW_LIMIT, "test")
                file = tar.extractfile(members[0])
                require(file is not None, "test")
                raw = file.read(RAW_LIMIT + 1)
            except BaseException as error:
                original = error
            finally:
                if file is not None:
                    try:
                        file.close()
                    except BaseException as error:
                        if original is None:
                            original = error
    except BaseException as error:
        if original is None:
            original = error
    if original is not None:
        raise original
    require(len(raw) <= RAW_LIMIT and b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(), "test")
    tree = ET.fromstring(raw)
    require(tree.tag in {"testsuites", "testsuite"}, "test")
    rows = list(tree.iter("testcase"))
    names = []
    for row in rows:
        require(row.get("classname") == "tests.runtime_protocol." + module, "test")
        require(not any(child.tag in {"failure", "error", "skipped"} for child in row), "test")
        name = row.get("name")
        require(name in expected, "test")
        names.append(name)
    require(Counter(names) == Counter(expected), "test")
    require(
        all(
            int(suite.get("errors", "0")) == 0
            and int(suite.get("failures", "0")) == 0
            and int(suite.get("skipped", "0")) == 0
            for suite in tree.iter("testsuite")
        ),
        "test",
    )
    return {"passed": len(rows), "failed": 0, "skipped": 0, "ignored": 0, "roster_verified": True}


def selection(mode):
    result = ["--no-default-features"]
    if mode == "raw":
        result += ["--features", "serde_json/raw_value"]
    elif mode == "agent":
        result += ["--features", "agent-prepare-codec"]
    return result


def feature_graph(mode):
    workspace = mode.startswith("workspace_")
    args = [] if mode == "workspace_default" else (["--all-features"] if workspace else selection(mode))
    _, tree, _ = rust(
        ["cargo", "tree", "--locked", "--offline", "-e", "features"]
        + ([] if workspace else ["-p", "bifrost-contracts"])
        + args,
        "default" if mode == "workspace_default" else ("clippy" if workspace else mode),
    )
    require("serde_json v1.0.151" in tree.read_text(), "feature")
    manifest = (
        "/workspace/core-rs/Cargo.toml" if workspace else "/workspace/core-rs/crates/bifrost-contracts/Cargo.toml"
    )
    _, output, _ = rust(
        ["cargo", "metadata", "--locked", "--offline", "--format-version", "1", "--manifest-path", manifest, *args],
        "default" if mode == "workspace_default" else ("clippy" if workspace else mode),
    )
    metadata = decode(output.read_bytes())
    packages = {package["id"]: package for package in metadata["packages"]}
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    contracts = [p for p in packages.values() if p["name"] == "bifrost-contracts"]
    json_packages = [p for p in packages.values() if p["name"] == "serde_json"]
    require(len(contracts) == len(json_packages) == 1, "feature")
    contract = contracts[0]
    json_package = json_packages[0]
    require(json_package["version"] == "1.0.151", "feature")
    contract_node = nodes[contract["id"]]
    json_features = nodes[json_package["id"]]["features"]
    sha_dependencies = [d for d in contract_node["deps"] if d["name"] == "sha2"]
    if sha_dependencies:
        require(len(sha_dependencies) == 1 and packages[sha_dependencies[0]["pkg"]]["version"] == "0.10.9", "feature")
    graph = {
        "serde_json_version": "1.0.151",
        "agent_feature": "agent-prepare-codec" in contract_node["features"],
        "raw_value": "raw_value" in json_features,
        "sha2_direct": bool(sha_dependencies),
        "float_roundtrip": "float_roundtrip" in json_features,
        "arbitrary_precision": "arbitrary_precision" in json_features,
    }
    require(not graph["float_roundtrip"] and not graph["arbitrary_precision"], "feature")
    if not workspace:
        require(
            graph["agent_feature"] == (mode == "agent")
            and graph["sha2_direct"] == (mode == "agent")
            and graph["raw_value"] == (mode != "off"),
            "feature",
        )
    receipt["graphs"][mode] = graph


def mounted_snapshot(kind, phase):
    expected = state[kind + "_inventory"]
    if kind == "rust":
        _, output, _ = rust(
            [
                "sh",
                "-c",
                'test -z "$(find /workspace/core-rs ! -type d ! -type f -print)" && find /workspace/core-rs -type f -exec sha256sum {} +',
            ],
            "default",
        )
        actual = {}
        for line in output.read_text().splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  /workspace/(core-rs/[^\r\n]+)", line)
            require(match is not None and match[2] not in actual, "source")
            actual[match[2]] = match[1]
    else:
        # Metadata paths only enter stdin; no business payload or product import.
        translation = {
            path: (
                "/contracts/control-vectors.json"
                if path == CONTROL_FIXTURE
                else "/" + path
                if path == AGENT_FIXTURE
                else "/app/" + path.removeprefix("api/")
            )
            for path in expected
        }
        code = """import hashlib,json,os,stat,sys
paths=json.loads(sys.stdin.buffer.read(1048577)); result={}
for original,path in paths.items():
 info=os.lstat(path)
 if not stat.S_ISREG(info.st_mode): raise ValueError('source type')
 with open(path,'rb') as source:
  digest=hashlib.sha256()
  while block:=source.read(65536): digest.update(block)
 result[original]=digest.hexdigest()
for root in ('/app/src','/app/tests/runtime_protocol'):
 for directory,dirs,files in os.walk(root,followlinks=False):
  if any(os.path.islink(os.path.join(directory,p)) for p in dirs+files): raise ValueError('source type')
  for name in files:
   if os.path.join(directory,name) not in paths.values(): raise ValueError('source extra')
sys.stdout.write(json.dumps(result,sort_keys=True,separators=(',',':')))
"""
        _, output, _ = python(["-c", code], input_bytes=canonical(translation))
        actual = decode(output.read_bytes())
    require(actual == expected, "source")
    receipt["source"][kind + "_snapshot"]["mounted_" + phase + "_sha256"] = sha_bytes(canonical(actual))


def binary(mode, name, field):
    if name == "tests":
        _, output, _ = rust(
            [
                "sh",
                "-c",
                "find /targets/debug/deps -maxdepth 1 -type f -name 'bifrost_contracts-*' -perm /111 -exec sha256sum {} +",
            ],
            mode,
        )
        lines = output.read_text().splitlines()
        require(len(lines) == 1, "image")
        match = re.fullmatch(r"([0-9a-f]{64})  (/targets/debug/deps/bifrost_contracts-[0-9a-f]+)", lines[0])
        require(match is not None, "image")
        path, digest = match[2], match[1]
    else:
        path = "/targets/debug/examples/" + name
        _, output, _ = rust(["sha256sum", path], mode)
        match = re.fullmatch(r"([0-9a-f]{64})  " + re.escape(path), output.read_text().strip())
        require(match is not None, "image")
        digest = match[1]
    measured = {"sha256": digest, "image_id": state["toolchain_image"], "target": mode}
    previous = receipt["binaries"][field]
    require(previous is None or previous == measured, "image")
    receipt["binaries"][field] = measured
    return path


def check_exchange(raw, profile, members):
    require(0 < len(raw) <= EXCHANGE_LIMIT, "bound")
    value = decode(raw)
    require(
        type(value) is dict
        and set(value) == {"profile", "frames"}
        and value["profile"] == profile
        and type(value["frames"]) is list
        and len(value["frames"]) == len(members),
        "exchange",
    )
    names = []
    for row in value["frames"]:
        require(type(row) is dict and set(row) == {"name", "frame_hex"}, "exchange")
        require(row["name"] in members and type(row["frame_hex"]) is str, "exchange")
        encoded = row["frame_hex"]
        require(
            0 < len(encoded) <= 2 * EXCHANGE_LIMIT
            and len(encoded) % 2 == 0
            and re.fullmatch(r"[0-9a-f]+", encoded) is not None,
            "exchange",
        )
        frame = bytes.fromhex(encoded)
        require(len(frame) > 4 and int.from_bytes(frame[:4], "big") == len(frame) - 4, "exchange")
        names.append(row["name"])
    require(Counter(names) == Counter(members), "exchange")
    return len(names)


def exchange_receipt(row, direction, raw, members):
    row["exchange"] = {"direction": direction, "members": len(members), "bytes": len(raw), "complete_membership": True}


def products():
    begin("products")
    with operation("python-control-tests") as row:
        record, _, _ = python(
            [
                "-m",
                "pytest",
                "--confcutdir=tests/runtime_protocol",
                "tests/runtime_protocol/test_control.py",
                "-q",
                "--no-cov",
            ]
        )
        row["counts"] = python_test_counts(record, PYTHON_CONTROL_TESTS, "test_control")
    with operation("python-agent-tests") as row:
        record, _, _ = python(
            [
                "-m",
                "pytest",
                "--confcutdir=tests/runtime_protocol",
                "tests/runtime_protocol/test_agent_prepare.py",
                "-q",
                "--no-cov",
            ]
        )
        row["counts"] = python_test_counts(record, python_expected(), "test_agent_prepare")
    control = decode((ROOT / CONTROL_FIXTURE).read_bytes())
    control_members = [v["name"] for v in control["wire"] if v["expected"] == "ok"]
    exchange = directory("exchange", readable=True)
    state["exchange"] = str(exchange)
    save()
    for mode in ("off", "raw", "agent"):
        with operation(mode + "-rust-control-emit") as row:
            executable = binary(mode, "runtime_control_vectors", mode + "_control")
            _, output, _ = rust([executable, "emit"], mode, limit=EXCHANGE_LIMIT)
            raw = output.read_bytes()
            check_exchange(raw, "bifrost.runtime/v1/control_profile/v1", control_members)
            exchange_receipt(row, "rust_to_python", raw, control_members)
            path = exchange / (mode + "-rust-control.json")
            path.write_bytes(raw)
            path.chmod(0o644)
        with operation(mode + "-python-control-validate") as row:
            _, output, _ = python(["-m", "tests.runtime_protocol.interchange", "validate", "/exchange/" + path.name])
            require(
                output.read_bytes()
                == f"validated {len(control_members)} synthetic peer control encodings\n".encode("ascii"),
                "exchange",
            )
            exchange_receipt(row, "rust_to_python", raw, control_members)
        with operation(mode + "-python-control-emit") as row:
            _, output, _ = python(["-m", "tests.runtime_protocol.interchange", "emit"], limit=EXCHANGE_LIMIT)
            raw = output.read_bytes()
            check_exchange(raw, "bifrost.runtime/v1/control_profile/v1", control_members)
            exchange_receipt(row, "python_to_rust", raw, control_members)
            path = exchange / (mode + "-python-control.json")
            path.write_bytes(raw)
            path.chmod(0o644)
        with operation(mode + "-rust-control-validate") as row:
            executable = binary(mode, "runtime_control_vectors", mode + "_control")
            _, output, _ = rust([executable, "validate", "/exchange/" + path.name], mode)
            require(
                output.read_bytes()
                == f"validated {len(control_members)} synthetic peer control encodings\n".encode("ascii"),
                "exchange",
            )
            exchange_receipt(row, "python_to_rust", raw, control_members)
    members = FROZEN_CORPORA["agent"]["positive"]
    with operation("rust-agent-emit") as row:
        executable = binary("agent", "runtime_agent_prepare_vectors", "agent_prepare")
        _, output, _ = rust([executable, "emit"], "agent", limit=EXCHANGE_LIMIT)
        raw = output.read_bytes()
        check_exchange(raw, "agent_prepare_profile/v1", members)
        exchange_receipt(row, "rust_to_python", raw, members)
    with operation("python-agent-validate") as row:
        _, output, _ = python(
            ["-m", "tests.runtime_protocol.agent_prepare_interchange", "validate"], input_bytes=raw, limit=0
        )
        require(output.stat().st_size == 0, "exchange")
        exchange_receipt(row, "rust_to_python", raw, members)
    with operation("python-agent-emit") as row:
        _, output, _ = python(["-m", "tests.runtime_protocol.agent_prepare_interchange", "emit"], limit=EXCHANGE_LIMIT)
        raw = output.read_bytes()
        check_exchange(raw, "agent_prepare_profile/v1", members)
        exchange_receipt(row, "python_to_rust", raw, members)
    with operation("rust-agent-validate") as row:
        executable = binary("agent", "runtime_agent_prepare_vectors", "agent_prepare")
        _, output, _ = rust([executable, "validate"], "agent", input_bytes=raw, limit=0)
        require(output.stat().st_size == 0, "exchange")
        exchange_receipt(row, "python_to_rust", raw, members)
    end_stage()


def verify():
    require(
        receipt["mode"] == "verify"
        and receipt["credentials"]["action_disposed"]
        and receipt["credentials"]["ghcr_disposed"],
        "credential",
    )
    toolchain()
    begin("fetch")
    state["cargo_home"] = volume("cargo-home")
    state["targets"] = {name: volume("target-" + name) for name in ("default", "clippy", "off", "raw", "agent")}
    with operation("cargo-fetch"):
        _, output, _ = rust(
            [
                "sh",
                "-c",
                'test -z "$(find /usr/local/cargo -name credentials -o -name credentials.toml -o -name config -o -name config.toml -o -name .netrc -o -name .git-credentials)"',
            ],
            "default",
            network="bridge",
        )
        require(output.stat().st_size == 0, "credential")
        rust(["cargo", "fetch", "--locked"], "default", network="bridge")
    end_stage()
    begin("checks")
    with operation("locked-inventory"):
        inventory()
    with operation("rust-snapshot-before"):
        mounted_snapshot("rust", "before")
    with operation("api-snapshot-before"):
        mounted_snapshot("api", "before")
    with operation("workspace-fmt"):
        rust(["cargo", "fmt", "--all", "--", "--check"], "default")
    with operation("workspace-clippy-all"):
        rust(
            [
                "cargo",
                "clippy",
                "--workspace",
                "--all-targets",
                "--all-features",
                "--locked",
                "--offline",
                "--",
                "-D",
                "warnings",
            ],
            "clippy",
        )
    with operation("workspace-default-tests") as row:
        _, output, _ = rust(["cargo", "test", "--workspace", "--locked", "--offline"], "default")
        row["counts"] = rust_test_counts(output, RUST_WORKSPACE_TESTS)
    with operation("workspace-default-graph"):
        feature_graph("workspace_default")
    with operation("workspace-all-graph"):
        feature_graph("workspace_all_features")
    end_stage(final=False)
    begin("matrix")
    for mode in ("off", "raw", "agent"):
        args = selection(mode)
        with operation(mode + "-contracts-tests") as row:
            _, output, _ = rust(["cargo", "test", "-p", "bifrost-contracts", "--locked", "--offline", *args], mode)
            expected = RUST_CONTROL_TESTS + (RUST_AGENT_TESTS if mode == "agent" else [])
            row["counts"] = rust_test_counts(output, expected)
            binary(mode, "tests", mode + "_tests")
        with operation(mode + "-control-build"):
            rust(
                [
                    "cargo",
                    "build",
                    "-p",
                    "bifrost-contracts",
                    "--example",
                    "runtime_control_vectors",
                    "--locked",
                    "--offline",
                    *args,
                ],
                mode,
            )
        with operation(mode + "-graph"):
            feature_graph(mode)
    with operation("agent-prepare-build"):
        rust(
            [
                "cargo",
                "build",
                "-p",
                "bifrost-contracts",
                "--example",
                "runtime_agent_prepare_vectors",
                "--no-default-features",
                "--features",
                "agent-prepare-codec",
                "--locked",
                "--offline",
            ],
            "agent",
        )
    with operation("binary-readback"):
        for mode in ("off", "raw", "agent"):
            binary(mode, "tests", mode + "_tests")
            binary(mode, "runtime_control_vectors", mode + "_control")
        binary("agent", "runtime_agent_prepare_vectors", "agent_prepare")
    end_stage()
    products()
    begin("checks")
    with operation("rust-snapshot-after"):
        mounted_snapshot("rust", "after")
    with operation("api-snapshot-after"):
        mounted_snapshot("api", "after")
    with operation("container-custody-readback"):
        for record in state.get("containers", []):
            verify_container(record)
    with operation("source-after"):
        source_readback("after")
        require(receipt["source"]["before"] == receipt["source"]["after"], "source")
        require(not capture(["git", "status", "--porcelain", "--untracked-files=all"]), "source")
        require(capture(["git", "rev-parse", "HEAD"]) == receipt["candidate"]["checkout_sha"], "source")
        receipt["candidate"]["clean_after"] = True
    end_stage()


def format_only():
    require(receipt["mode"] == "format", "source")
    toolchain()
    begin("checks")
    with operation("format-copy"):
        parent = directory("source-copy")
        copy = parent / "repo"
        command(["git", "clone", "--no-hardlinks", "--no-checkout", str(ROOT), str(copy)])
        command(["git", "-C", str(copy), "checkout", "--detach", receipt["candidate"]["checkout_sha"]])
        before = {path: sha_file(copy / path) for path in FORMAT_PATHS}
        require(all(before[path] == EXPECTED_SOURCE[path] for path in FORMAT_PATHS), "source")
    with operation("format-run"):
        # Whole context is RO; ONLY four disposable file overlays can be written.
        mounts = [(str(copy / "core-rs"), "/workspace/core-rs", True)] + [
            (str(copy / path), "/workspace/" + path, False) for path in FORMAT_PATHS
        ]
        container(
            state["toolchain_image"],
            ["rustfmt", "--edition", "2024"] + ["/workspace/" + path for path in FORMAT_PATHS],
            mounts=mounts,
        )
    with operation("format-patch"):
        changed = set(capture(["git", "-C", str(copy), "diff", "--name-only"]).splitlines())
        require(changed <= set(FORMAT_PATHS), "source")
        patch, _ = command(["git", "-C", str(copy), "diff", "--binary"])
        require(patch.stat().st_size <= RAW_LIMIT, "bound")
        (EVIDENCE / "format.patch").write_bytes(patch.read_bytes())
        receipt["format"] = {
            "before": before,
            "after": {p: sha_file(copy / p) for p in FORMAT_PATHS},
            "patch_sha256": sha_file(patch),
            "source_unchanged": False,
        }
    with operation("container-custody-readback"):
        for record in state.get("containers", []):
            verify_container(record)
    with operation("source-after"):
        source_readback("after")
        require(receipt["source"]["before"] == receipt["source"]["after"], "source")
        require(not capture(["git", "status", "--porcelain", "--untracked-files=all"]), "source")
        receipt["candidate"]["clean_after"] = True
        receipt["format"]["source_unchanged"] = True
    end_stage()


def cleanup():
    # Every attempt is independent, including observations and private-state writes.
    # A failed/control attempt cannot skip another resource or replace the first error.
    first = None
    resources = []

    def attempt(callback):
        nonlocal first
        try:
            callback()
        except BaseException as error:
            receipt["disposition"]["cleanup_failed"] = True
            if first is None:
                first = error
                receipt["cleanup"]["first_failure"] = error_class(error)
            # Only a secondary save is suppressed; the first error remains retained.
            with suppress(BaseException):
                save()

    def resource(kind, identifier, callback):
        row = {
            "kind": kind,
            "identity": identifier,
            "acquired": False,
            "verified": False,
            "disposed": None,
            "inspection_status": "failed",
        }
        resources.append(row)
        callback(row)

    def project(row):
        require(receipt["cleanup"]["pre_pr_initial_empty"] is True, "acquisition")
        row["acquired"] = bool(state.get("pre_pr_started"))
        row["verified"] = True
        if state.get("pre_pr_started"):
            # Existing source helper chooses exactly this initially absent project.
            # Inspect its genuine labels before invoking its normal teardown.
            for kind, argv in (
                ("container", ["docker", "ps", "-aq", "--no-trunc"]),
                ("volume", ["docker", "volume", "ls", "-q"]),
                ("network", ["docker", "network", "ls", "-q", "--no-trunc"]),
            ):
                for name in capture(
                    [*argv, "--filter", "label=com.docker.compose.project=" + state["project"]]
                ).splitlines():
                    facts = inspect(kind, name)
                    labels = facts["Config"]["Labels"] if kind == "container" else facts["Labels"]
                    require(labels.get("com.docker.compose.project") == state["project"], "acquisition")
            command(["./test.sh", "stack", "down"])
        require(absent_owned(state["project"], PREFIX), "cleanup")
        receipt["cleanup"]["pre_pr_final_empty"] = True
        row.update(disposed=True, inspection_status="absent")

    def remove_container(record, row):
        existing = capture(["docker", "ps", "-aq", "--no-trunc", "--filter", "name=^/" + record["name"] + "$"])
        if not existing:
            require(record["removed"] or record["id"] is None, "acquisition")
            row.update(verified=True, disposed=True, inspection_status="absent")
            return
        require(record["id"] is None or existing == record["id"], "acquisition")
        record["id"] = existing
        verify_container(record)
        row.update(acquired=True, verified=True, inspection_status="observed")
        command(["docker", "rm", "-f", existing])
        require(not capture(["docker", "ps", "-aq", "--no-trunc", "--filter", "id=" + existing]), "cleanup")
        record["removed"] = True
        row.update(disposed=True, inspection_status="absent")

    def remove_volume(record, row):
        existing = capture(["docker", "volume", "ls", "-q", "--filter", "name=^" + record["name"] + "$"])
        if not existing:
            require(record["removed"] or record["facts"] is None, "acquisition")
            row.update(verified=True, disposed=True, inspection_status="absent")
            return
        require(existing == record["name"], "acquisition")
        facts = inspect("volume", existing)
        require(facts["Labels"].get(LABEL) == PREFIX, "acquisition")
        selected = {k: facts[k] for k in ("Name", "Mountpoint", "CreatedAt")}
        require(record["facts"] is None or selected == record["facts"], "acquisition")
        record["facts"] = selected
        row.update(acquired=True, verified=True, inspection_status="observed")
        command(["docker", "volume", "rm", existing])
        require(not capture(["docker", "volume", "ls", "-q", "--filter", "name=^" + existing + "$"]), "cleanup")
        record["removed"] = True
        row.update(disposed=True, inspection_status="absent")

    def remove_image(record, row):
        if record is state.get("pre_pr_api_tag") and state.get("pre_pr_started"):
            # A genuine retained complete-stage witness is required BEFORE absence.
            measurement = state.get("pre_pr_measurement")
            provenance = measurement.get("image_witness") if type(measurement) is dict else None
            name = {"stack-complete": "stack", "quality-complete": "quality"}.get(provenance)
            stage = state.get("pre_pr_" + name + "_record") if name is not None else None
            require(
                type(measurement) is dict
                and retained_build_witness(measurement, record, state["initial_image_ids"], stage, state["project"])
                and (
                    name != "quality"
                    or quality_build_allowed(
                        state.get("pre_pr_fresh"),
                        state.get("pre_pr_started"),
                        state["pre_pr_environment"]["BIFROST_SKIP_BUILD"],
                    )
                ),
                "acquisition",
            )
        existing = capture(["docker", "image", "ls", "-q", "--no-trunc", record["tag"]])
        if not existing:
            require(
                record["removed"]
                or record["id"] is None
                or any(
                    r is not record and r["id"] == record["id"] and r["removed"]
                    for r in state.get("prepare_tags", [])
                    + ([state["pre_pr_api_tag"]] if state.get("pre_pr_api_tag") else [])
                ),
                "acquisition",
            )
            record["removed"] = True
            row.update(acquired=record["id"] is not None, verified=True, disposed=True, inspection_status="absent")
            return
        # Never infer ownership solely from an image appearing during elapsed time.
        require(record["id"] is not None and record["id"] not in state["initial_image_ids"], "acquisition")
        if existing != record["id"]:
            # Prepare can replace our prePR alias; remove only the retained old ID.
            require(
                record is state.get("pre_pr_api_tag")
                and record["witness"] is not None
                and any(r["tag"] == record["tag"] and r["id"] == existing for r in state.get("prepare_tags", [])),
                "acquisition",
            )
            facts = inspect("image", record["id"])
            require(
                facts["Id"] == record["id"]
                and not facts.get("RepoTags")
                and facts["Config"] == record["witness"]["image"]["Config"],
                "acquisition",
            )
            row.update(acquired=True, verified=True, inspection_status="observed")
            command(["docker", "image", "rm", record["id"]])
            require(
                record["id"]
                not in capture(["docker", "image", "ls", "--no-trunc", "--format", "{{.ID}}"]).splitlines(),
                "cleanup",
            )
            record["removed"] = True
            row.update(disposed=True, inspection_status="absent")
            return
        require(not record["previous"] or record["previous"] == state["pre_pr_api_tag"]["id"], "acquisition")
        facts = inspect("image", existing)
        require(facts["Id"] == existing and record["tag"] in facts.get("RepoTags", []), "acquisition")
        if record is state.get("pre_pr_api_tag"):
            require(
                record["witness"] is not None and facts["Config"] == record["witness"]["image"]["Config"], "acquisition"
            )
        row.update(acquired=True, verified=True, inspection_status="observed")
        command(["docker", "image", "rm", record["tag"]])
        require(not capture(["docker", "image", "ls", "-q", record["tag"]]), "cleanup")
        record["removed"] = True
        row.update(disposed=True, inspection_status="absent")

    def remove_directory(record, row):
        path = Path(record["path"])
        if not os.path.lexists(path):
            require(record["removed"] or record["identity"] is None, "acquisition")
            row.update(verified=True, disposed=True, inspection_status="absent")
            return
        require(record["identity"] is not None, "acquisition")
        checked_directory(path, record["identity"])
        row.update(acquired=True, verified=True, inspection_status="observed")
        if record["role"] == "ghcr-config":
            env = clean_env()
            env["DOCKER_CONFIG"] = str(path)
            command(["docker", "logout", "ghcr.io"], env=env)
            if (path / "config.json").exists():
                value = decode((path / "config.json").read_bytes())
                require(
                    not value.get("credsStore")
                    and not value.get("credHelpers")
                    and not any(value.get("auths", {}).values()),
                    "credential",
                )
        shutil.rmtree(path)
        require(not os.path.lexists(path), "cleanup")
        record["removed"] = True
        row.update(disposed=True, inspection_status="absent")

    attempt(lambda: begin("cleanup"))
    if state.get("project"):
        attempt(lambda: resource("project", state["project"], project))
    if state.get("BIFROST_ACTION_PIN_TOKEN_FILE"):

        def action(row):
            credential_cleanup()
            row.update(acquired=True, verified=True, disposed=True, inspection_status="absent")

        attempt(lambda: resource("directory", "action-credential", action))
    for record in state.get("containers", []):
        attempt(
            lambda record=record: resource(
                "container", record["id"] or record["name"], lambda row: remove_container(record, row)
            )
        )
    for record in state.get("volumes", []):
        attempt(lambda record=record: resource("volume", record["name"], lambda row: remove_volume(record, row)))
    for record in (
        ([state["pre_pr_api_tag"]] if state.get("pre_pr_api_tag") else [])
        + state.get("images", [])
        + state.get("prepare_tags", [])
    ):
        attempt(
            lambda record=record: resource(
                "image", record["id"] or PREFIX + "-pending-image", lambda row: remove_image(record, row)
            )
        )
    for record in state.get("directories", []):
        attempt(lambda record=record: resource("directory", record["role"], lambda row: remove_directory(record, row)))
    attempt(lambda: require(len(resources) <= 128, "bound"))
    receipt["cleanup"]["resources"] = resources
    # Inspect each family independently; no incomplete/error observation is EMPTY.
    empty = []
    for _kind, argv in (
        ("container", ["docker", "ps", "-aq", "--no-trunc"]),
        ("volume", ["docker", "volume", "ls", "-q"]),
        ("network", ["docker", "network", "ls", "-q", "--no-trunc"]),
    ):

        def observe(argv=argv):
            result = capture([*argv, "--filter", "label=" + LABEL + "=" + PREFIX])
            require(not result, "cleanup")
            empty.append(True)

        attempt(observe)

    def diagnostics(row):
        checked_directory(RAW_DIRECTORY, state["raw_identity"])
        row.update(acquired=True, verified=True, inspection_status="observed")
        shutil.rmtree(RAW_DIRECTORY)
        require(not os.path.lexists(RAW_DIRECTORY), "cleanup")
        state["raw_removed"] = True
        row.update(disposed=True, inspection_status="absent")

    attempt(lambda: resource("directory", "private-diagnostics", diagnostics))
    if len(empty) == 3 and all(row["disposed"] is True and row["verified"] for row in resources):
        receipt["cleanup"]["owner_final_empty"] = True
    else:
        receipt["cleanup"]["owner_final_empty"] = False
    attempt(save)
    attempt(lambda: end_stage("failure" if first is not None else "success"))
    if first is not None:
        raise first


def validate_receipt():
    # Closed, value-type admission of the publication projection, never diagnostics.
    def record(value, keys):
        require(type(value) is dict and set(value) == set(keys))

    def boolean(value):
        require(type(value) is bool)

    def integer(value, low=0, high=65535):
        require(type(value) is int and low <= value <= high)

    def time_value(value):
        require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86400)

    def digest(value):
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)

    def git(value):
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) is not None)

    def image(value):
        require(type(value) is str and value.startswith("sha256:"))
        digest(value[7:])

    def nullable(value, validator):
        if value is not None:
            validator(value)

    def hash_map(value, keys):
        record(value, keys)
        for item in value.values():
            digest(item)

    def version(value):
        require(
            type(value) is str
            and 0 < len(value) <= 128
            and value.isascii()
            and re.fullmatch(r"[A-Za-z0-9 .()+_-]+", value) is not None
        )

    def snapshot(value):
        record(value, ("candidate_sha256", "mounted_before_sha256", "mounted_after_sha256", "file_count"))
        digest(value["candidate_sha256"])
        nullable(value["mounted_before_sha256"], digest)
        nullable(value["mounted_after_sha256"], digest)
        integer(value["file_count"])

    def inventory_record(value):
        record(value, ("baseline_sha256", "actual_sha256", "count", "unchanged"))
        digest(value["baseline_sha256"])
        digest(value["actual_sha256"])
        integer(value["count"])
        boolean(value["unchanged"])

    record(
        receipt,
        (
            "schema",
            "mode",
            "candidate",
            "source",
            "corpora",
            "graphs",
            "images",
            "binaries",
            "operations",
            "stages",
            "credentials",
            "cleanup",
            "format",
            "disposition",
        ),
    )
    require(receipt["schema"] == "bifrost.test.agent-prepare-ci/v1" and receipt["mode"] in {"format", "verify"})
    candidate = receipt["candidate"]
    record(
        candidate,
        ("associated_sha", "checkout_sha", "checkout_tree", "main_sha", "main_ancestor", "clean_before", "clean_after"),
    )
    for key in ("associated_sha", "checkout_sha", "checkout_tree", "main_sha"):
        nullable(candidate[key], git)
    for key in ("main_ancestor", "clean_before", "clean_after"):
        nullable(candidate[key], boolean)
    source = receipt["source"]
    record(
        source,
        ("before", "after", "preserved_before", "preserved_after", "rust_snapshot", "api_snapshot", "locked_inventory"),
    )
    for key in ("before", "after"):
        nullable(source[key], lambda v: hash_map(v, SOURCE_KEYS))
    for key in ("preserved_before", "preserved_after"):
        nullable(source[key], lambda v: hash_map(v, PRESERVED))
    for key in ("rust_snapshot", "api_snapshot"):
        nullable(source[key], snapshot)
    nullable(source["locked_inventory"], inventory_record)
    record(receipt["corpora"], ("control", "agent"))
    for key, expected in FROZEN_CORPORA.items():
        if receipt["corpora"][key] is not None:
            require(receipt["corpora"][key] == expected)
    record(receipt["graphs"], ("workspace_default", "workspace_all_features", "off", "raw", "agent"))
    for value in receipt["graphs"].values():
        if value is not None:
            record(
                value,
                (
                    "serde_json_version",
                    "agent_feature",
                    "raw_value",
                    "sha2_direct",
                    "float_roundtrip",
                    "arbitrary_precision",
                ),
            )
            version(value["serde_json_version"])
            for key in set(value) - {"serde_json_version"}:
                boolean(value[key])
    record(receipt["images"], ("api", "toolchain"))
    for key, value in receipt["images"].items():
        if value is not None:
            record(
                value,
                ("image_id", "configuration_verified", "python_version", "python_sha256")
                if key == "api"
                else ("image_id", "configuration_verified", "rustc_version", "cargo_version"),
            )
            image(value["image_id"])
            boolean(value["configuration_verified"])
            if key == "api":
                version(value["python_version"])
                digest(value["python_sha256"])
            else:
                version(value["rustc_version"])
                version(value["cargo_version"])
    record(
        receipt["binaries"],
        ("off_tests", "raw_tests", "agent_tests", "off_control", "raw_control", "agent_control", "agent_prepare"),
    )
    for value in receipt["binaries"].values():
        if value is not None:
            record(value, ("sha256", "image_id", "target"))
            digest(value["sha256"])
            image(value["image_id"])
            require(value["target"] in {"off", "raw", "agent"})
    record(receipt["operations"], OPERATIONS)
    for value in receipt["operations"].values():
        record(value, ("status", "exit", "duration_s", "counts", "exchange"))
        require(value["status"] in {"not_started", "success", "failure", "interrupted"})
        nullable(value["exit"], lambda v: integer(v, -255, 255))
        nullable(value["duration_s"], time_value)
        if value["status"] == "not_started":
            require(all(value[k] is None for k in ("exit", "duration_s", "counts", "exchange")))
        if value["status"] == "success":
            require(value["exit"] == 0 and value["duration_s"] is not None)
        if value["counts"] is not None:
            counts = value["counts"]
            record(counts, ("passed", "failed", "skipped", "ignored", "roster_verified"))
            for key in ("passed", "failed", "skipped", "ignored"):
                integer(counts[key])
            boolean(counts["roster_verified"])
        if value["exchange"] is not None:
            exchange = value["exchange"]
            record(exchange, ("direction", "members", "bytes", "complete_membership"))
            require(exchange["direction"] in {"rust_to_python", "python_to_rust"})
            integer(exchange["members"], 1, 256)
            integer(exchange["bytes"], 1, EXCHANGE_LIMIT)
            boolean(exchange["complete_membership"])
    require(type(receipt["stages"]) is list and len(receipt["stages"]) <= 10)
    seen = set()
    for value in receipt["stages"]:
        record(value, ("name", "cap_s", "duration_s", "status"))
        require(value["name"] in STAGES and value["name"] not in seen)
        seen.add(value["name"])
        integer(value["cap_s"], 0, 1800)
        require(value["cap_s"] == CAPS.get(value["name"], 170))
        nullable(value["duration_s"], time_value)
        require(value["status"] in {"not_started", "running", "success", "failure", "interrupted"})
    record(receipt["credentials"], ("action_disposed", "ghcr_disposed", "product_environment_verified"))
    for value in receipt["credentials"].values():
        nullable(value, boolean)
    cleanup_value = receipt["cleanup"]
    record(
        cleanup_value,
        (
            "pre_pr_initial_empty",
            "pre_pr_final_empty",
            "owner_initial_empty",
            "owner_final_empty",
            "resources",
            "first_failure",
        ),
    )
    for key in ("pre_pr_initial_empty", "pre_pr_final_empty", "owner_initial_empty", "owner_final_empty"):
        nullable(cleanup_value[key], boolean)
    classes = {
        "source",
        "credential",
        "image",
        "feature",
        "inventory",
        "test",
        "exchange",
        "timeout",
        "bound",
        "acquisition",
        "io",
        "control",
        "cleanup",
        "publication",
        "schema",
    }
    require(cleanup_value["first_failure"] is None or cleanup_value["first_failure"] in classes)
    require(type(cleanup_value["resources"]) is list and len(cleanup_value["resources"]) <= 128)
    for value in cleanup_value["resources"]:
        record(value, ("kind", "identity", "acquired", "verified", "disposed", "inspection_status"))
        require(value["kind"] in {"project", "container", "volume", "network", "image", "directory"})
        label = value["identity"]
        require(
            type(label) is str
            and 1 <= len(label) <= 128
            and label.isascii()
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", label) is not None
        )
        if value["kind"] == "directory":
            require(label in {"action-credential", "ghcr-config", "source-copy", "exchange", "private-diagnostics"})
        for key in ("acquired", "verified"):
            boolean(value[key])
        nullable(value["disposed"], boolean)
        require(value["inspection_status"] in {"observed", "absent", "failed"})
    if receipt["format"] is not None:
        value = receipt["format"]
        record(value, ("before", "after", "patch_sha256", "source_unchanged"))
        hash_map(value["before"], FORMAT_PATHS)
        hash_map(value["after"], FORMAT_PATHS)
        digest(value["patch_sha256"])
        boolean(value["source_unchanged"])
    value = receipt["disposition"]
    record(
        value,
        (
            "status",
            "primary_operation",
            "primary_class",
            "original_exit",
            "cleanup_failed",
            "publication_failed",
            "elapsed_s",
        ),
    )
    require(value["status"] in {"passed", "failed", "interrupted"})
    require(value["primary_operation"] is None or value["primary_operation"] in OPERATIONS)
    require(value["primary_class"] is None or value["primary_class"] in classes)
    nullable(value["original_exit"], lambda v: integer(v, -255, 255))
    boolean(value["cleanup_failed"])
    boolean(value["publication_failed"])
    time_value(value["elapsed_s"])


def admit_success():
    required = set(OPERATIONS) - {"format-copy", "format-run", "format-patch"}
    if receipt["mode"] == "format":
        required = {
            "source-guard",
            "source-before",
            "source-after",
            "toolchain-build",
            "container-custody-readback",
            "format-copy",
            "format-run",
            "format-patch",
            "owned-cleanup",
            "owned-readback",
            "safe-publication",
        }
    # Publication is still in progress here and succeeds only after final validation/write.
    require(
        all(receipt["operations"][name]["status"] == "success" for name in required - {"safe-publication"}), "schema"
    )
    require(receipt["candidate"]["clean_before"] is True and receipt["candidate"]["clean_after"] is True, "source")
    require(receipt["source"]["before"] == receipt["source"]["after"], "source")
    require(receipt["cleanup"]["owner_final_empty"] is True, "cleanup")
    require(not receipt["disposition"]["cleanup_failed"], "cleanup")
    now = time.monotonic()
    elapsed = now - state["start"]
    require(elapsed <= 1800, "timeout")
    durations = {row["name"]: row["duration_s"] for row in receipt["stages"]}
    if stage_start is not None:
        durations[stage_name] += now - stage_start
    # All elapsed time outside the eight fixed stages consumes shared170,
    # including live publication, checkout/helper work and stage bookkeeping.
    orchestration = elapsed - sum(value for name, value in durations.items() if name in CAPS)
    require(
        orchestration <= 170 and all(durations[row["name"]] <= row["cap_s"] for row in receipt["stages"]), "timeout"
    )
    if receipt["mode"] == "verify":
        validate_pre_pr_measurement(state.get("pre_pr_measurement"))
        require(state["pre_pr_measurement"]["admission"] == "admitted", "source")
        require(state["pre_pr_measurement"]["literal_exit"] == 0, "test")
        require(all(row["status"] == "complete" for row in state["pre_pr_measurement"]["stages"]), "test")
        require(all(value is True for value in receipt["credentials"].values()), "credential")
        require(receipt["cleanup"]["pre_pr_final_empty"] is True, "cleanup")
        require(all(value is not None for value in receipt["graphs"].values()), "feature")
        for name in {
            "workspace-default-tests",
            "python-control-tests",
            "python-agent-tests",
            "off-contracts-tests",
            "raw-contracts-tests",
            "agent-contracts-tests",
        }:
            counts = receipt["operations"][name]["counts"]
            require(
                counts is not None
                and counts["roster_verified"] is True
                and counts["passed"] > 0
                and not any(counts[k] for k in ("failed", "skipped", "ignored")),
                "test",
            )
        require(all(value is not None for value in receipt["binaries"].values()), "source")
        for kind in ("rust", "api"):
            snap = receipt["source"][kind + "_snapshot"]
            require(
                snap is not None
                and snap["candidate_sha256"] == snap["mounted_before_sha256"] == snap["mounted_after_sha256"],
                "source",
            )
    else:
        require(receipt["format"] is not None and receipt["format"]["source_unchanged"] is True, "source")


def publication(original):
    pending = original
    publication_start = None
    publication_base = 0

    def completed_bound():
        now = time.monotonic()
        elapsed = now - state["start"]
        require(publication_start is not None and elapsed <= 1800, "timeout")
        durations = {row["name"]: row["duration_s"] for row in receipt["stages"]}
        # end_stage cleared its live timer before the final files were closed;
        # this actual start/base retains the entire publication lifetime.
        durations["publication"] = publication_base + now - publication_start
        orchestration = elapsed - sum(value for name, value in durations.items() if name in CAPS)
        require(
            orchestration <= 170 and all(durations[row["name"]] <= row["cap_s"] for row in receipt["stages"]), "timeout"
        )
        return durations["publication"]

    try:
        begin("publication")
        publication_start = stage_start
        publication_base = stage_record("publication")["duration_s"]
        with operation("safe-publication"):
            if pending is None:
                try:
                    admit_success()
                except BaseException as error:
                    pending = error
                    primary(error, "safe-publication")
                else:
                    receipt["disposition"].update(status="passed", original_exit=0)
            receipt["disposition"]["elapsed_s"] = time.monotonic() - state["start"]
            validate_receipt()
            owned = {"schema": "bifrost.test.agent-prepare-owned/v1", **receipt["cleanup"]}
            atomic_json(EVIDENCE / "owned-inventory.json", owned, 262144)
            if state.get("pre_pr_measurement") is not None:
                validate_pre_pr_measurement(state["pre_pr_measurement"])
                atomic_json(EVIDENCE / "pre-pr-measurement.json", state["pre_pr_measurement"], 16384)
            if receipt["format"] is not None:
                atomic_json(
                    EVIDENCE / "format-metadata.json",
                    {"schema": "bifrost.test.agent-prepare-format/v1", **receipt["format"]},
                    16384,
                )
        end_stage(final=False)
        stage_record("publication")["status"] = "success"
        # These receipt duration/timestamp samples precede final file completion.
        # A passed field alone cannot establish post-write or upload acceptance.
        stage_record("publication")["duration_s"] = completed_bound()
        receipt["disposition"]["elapsed_s"] = time.monotonic() - state["start"]
        validate_receipt()
        atomic_json(EVIDENCE / "receipt.json", receipt, 262144)
        save()
        measured = completed_bound()
        milestone("publication", "complete", "success", measured)
        save()
        # Last observable local success boundary: includes final file closure,
        # private save and milestone flush. No later successful-path IO follows.
        completed_bound()
    except BaseException as error:
        if pending is None:
            pending = error
        try:
            primary(pending, "safe-publication")
            receipt["disposition"]["publication_failed"] = True
            row = receipt["operations"]["safe-publication"]
            row["status"] = "interrupted" if error_class(pending) == "control" else "failure"
            row["exit"] = error_code(pending)
        finally:
            # One best-effort invalidation, not write-until-green or a new reserve.
            # It runs even if secondary diagnostic bookkeeping also fails.
            try:
                checked_directory(EVIDENCE, state["root_identity"])
                (EVIDENCE / "receipt.json").unlink(missing_ok=True)
            finally:
                # Original identity/exit wins even if invalidation fails. Never
                # accept a passed field without zero producer/job/upload exits.
                raise pending
    return pending


original = None
if MODE == "cleanup" and receipt["disposition"]["primary_class"] is not None:
    # Separate workflow processes retain the first measured class/exit, not a
    # fictional same Python exception object across process boundaries.
    original = Failure(receipt["disposition"]["primary_class"], receipt["disposition"]["original_exit"] or 1)
try:
    if MODE == "guard":
        guard()
    elif MODE == "pre-pr":
        pre_pr()
    elif MODE == "prepare":
        prepare()
    elif MODE in {"format", "verify"}:
        require(receipt["mode"] == MODE, "source")
        format_only() if MODE == "format" else verify()
    elif MODE == "cleanup":
        with operation("owned-cleanup"):
            cleanup()
        with operation("owned-readback"):
            require(receipt["cleanup"]["owner_final_empty"] is True, "cleanup")
            require(receipt["cleanup"]["pre_pr_final_empty"] is True, "cleanup")
except BaseException as error:
    if original is None:
        original = error
    primary(error, active_operation)
    with suppress(BaseException):
        end_stage("interrupted" if error_class(error) == "control" else "failure")
finally:
    if MODE == "cleanup":
        try:
            original = publication(original)
        except BaseException as error:
            receipt["disposition"]["publication_failed"] = True
            if original is None:
                original = error
                primary(error, "safe-publication")
            with suppress(BaseException):
                save()
    else:
        try:
            save()
        except BaseException as error:
            if original is None:
                original = error
if original is not None:
    # Redacted static classification; original object priority is preserved internally.
    raise SystemExit(error_code(original)) from None
PY
