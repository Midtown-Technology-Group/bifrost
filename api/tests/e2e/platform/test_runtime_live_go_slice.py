"""Actual accepted-byte candidate / original Rust owner / authenticated API slice.

Only the dedicated hosted lane supplies source-bound producer files. No fake
artifact, lifecycle writer, auth override or production dispatch lives here.
Physical cleanup is checked; durable source settlement is a separate required
acceptance gate and is not inferred from these observations.
"""

import asyncio
import hashlib
import ipaddress
import json
import os
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import text

from src.core.constants import PROVIDER_ORG_ID
from src.core.runtime_sdk_credentials import (
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    GrantSnapshot,
    SDKOperation,
    SelectedSDKPolicy,
    caller_digest,
    grant_digest,
    operations_digest,
    source_digest,
)
from src.models.orm.config import Config
from src.models.orm.integrations import Integration
from src.models.orm.runtime_execution import RuntimeDeploymentArtifact
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.isolated_runtime_sdk_bridge import _start_ticks
from src.services.isolated_runtime_sdk_http import build_isolated_sdk_app
from src.services.isolated_runtime_sdk_issuer import FiniteIssuerServer
from src.services.isolated_runtime_sdk_server import IsolatedSDKServer
from src.services.isolated_runtime_sdk_snapshot import load_committed_finite_snapshot

from tests.e2e.platform.test_runtime_writer_guards import (
    installed_guards as installed_guards,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]
WORKFLOW_SHA = "160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def publish(path, value):
    temporary = path.with_suffix(".new")
    with temporary.open("xb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.rename(path)


async def observation(path):
    # Parent orchestration has a fixed outer deadline; this is observation of one
    # original handle/file, never retrying a transaction, start or credential.
    async with asyncio.timeout(45):
        while not path.is_file():
            await asyncio.sleep(0.02)
        raw = path.read_bytes()
        assert 0 < len(raw) <= 65536
        return json.loads(raw)


def certificates(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "isolated SDK")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert, private = directory / "certificate.pem", directory / "key.pem"
    with cert.open("xb") as stream:
        stream.write(certificate.public_bytes(serialization.Encoding.PEM))
    with private.open("xb") as stream:
        stream.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    private.chmod(0o600)
    return cert, private


async def test_original_rust_owner_runs_unchanged_go_twice(
    db_session, async_session_factory, platform_admin, e2e_client
):
    root = Path(os.environ["BIFROST_LIVE_SLICE_ROOT"])
    assert os.geteuid() == 1000 and root.is_absolute() and root.resolve() == root
    producer = root / "native"
    descriptor = json.loads((producer / "native-bundle-descriptor.json").read_bytes())
    build = json.loads((producer / "descriptor.json").read_bytes())
    assert descriptor["source_commit"] == os.environ["BIFROST_LIVE_SOURCE_SHA"]
    assert descriptor["producer_run_id"] == os.environ["BIFROST_LIVE_PRODUCER_RUN"]
    assert build["source_commit"] == descriptor["source_commit"]
    assert build["source_sha256"] == descriptor["source_sha256"]
    assert (
        digest((producer / "native-bundle.tar").read_bytes())
        == descriptor["artifact"]["artifact_id"][7:]
    )
    assert digest((producer / "workflow").read_bytes()) == WORKFLOW_SHA
    assert descriptor["artifact"]["executable_sha256"] == WORKFLOW_SHA
    with tarfile.open(producer / "native-bundle.tar", "r:") as archive:
        entries = {
            item.name: archive.extractfile(item).read()
            for item in archive.getmembers()
            if item.isfile()
        }
    assert list(entries) == [row["path"] for row in descriptor["entries"]]
    for entry in descriptor["entries"]:
        assert len(entries[entry["path"]]) == entry["size_bytes"]
        assert digest(entries[entry["path"]]) == entry["sha256"]
    input_raw, output_raw = entries["input-schema.json"], entries["output-schema.json"]
    assert digest(input_raw) == descriptor["input_schema_sha256"]
    assert digest(output_raw) == descriptor["output_schema_sha256"]
    assert (
        digest((producer / "descriptor.json").read_bytes())
        == descriptor["artifact"]["build_evidence_sha256"]
    )
    input_schema, output_schema = json.loads(input_raw), json.loads(output_raw)
    solution, workflow, deployment, integration = (uuid4() for _ in range(4))
    # Explicit language-neutral registration, not a Python function wrapper or
    # an assertion that a native archive was stored in Python's source store.
    manifest = {
        "version": "isolated-native-workflow-registration/v1",
        "workflow": {
            "id": str(workflow),
            "name": "Integration readiness",
            "runtime": "go-native/v1",
            "artifact": descriptor["artifact"],
            "input_schema": input_schema,
            "output_schema": output_schema,
        },
    }
    resolution = {
        "version": "isolated-native-resolution/v1",
        "workflow_id": str(workflow),
        "artifact_id": descriptor["artifact"]["artifact_id"],
        "input_schema_sha256": digest(input_raw),
        "output_schema_sha256": digest(output_raw),
    }
    manifest_hash, resolution_hash = (
        "sha256:" + digest(canonical(manifest)),
        "sha256:" + digest(canonical(resolution)),
    )
    db_session.add(
        Solution(
            id=solution,
            slug=f"live-native-{solution}",
            name="Isolated native readiness",
            organization_id=PROVIDER_ORG_ID,
            execution_runtime_mode="repo-v1",
        )
    )
    await db_session.flush()
    db_session.add(
        Workflow(
            id=workflow,
            name=f"live-native-{workflow}",
            function_name="Run",
            path="cmd/workflow",
            type="workflow",
            solution_id=solution,
            organization_id=PROVIDER_ORG_ID,
            is_active=True,
            endpoint_enabled=False,
            public_endpoint=False,
            api_key_enabled=False,
        )
    )
    db_session.add(
        SolutionDeployment(
            id=deployment,
            solution_id=solution,
            organization_id=PROVIDER_ORG_ID,
            state="draft",
            bundle_hash=descriptor["artifact"]["artifact_id"],
            compiled_manifest=manifest,
            compiled_manifest_hash=manifest_hash,
            resolution_map=resolution,
            resolution_map_hash=resolution_hash,
            source_artifact_key=f"isolated-native/{descriptor['artifact']['artifact_id']}",
            runtime_storage_prefix=f"isolated-native/{deployment}",
            created_by=platform_admin.user_id,
        )
    )
    db_session.add(Integration(id=integration, name="Fixture"))
    await db_session.flush()
    db_session.add(
        Config(
            key="region",
            value={"value": "isolated"},
            integration_id=integration,
            organization_id=None,
            updated_by="isolated-native-fixture",
        )
    )
    await db_session.flush()
    db_session.add(
        RuntimeDeploymentArtifact(
            deployment_id=deployment,
            workflow_id=workflow,
            solution_id=solution,
            artifact_id=descriptor["artifact"]["artifact_id"],
            runtime="go-native/v1",
            runtime_protocol="bifrost.runtime/v1",
            artifact=descriptor["artifact"],
            input_schema=input_schema,
            output_schema=output_schema,
            build_evidence_sha256=descriptor["artifact"]["build_evidence_sha256"],
            source_sha256=descriptor["source_sha256"],
            reviewed_by=platform_admin.user_id,
        )
    )
    previous = "draft"
    for state in ("building", "validated", "ready", "activating", "active"):
        assert (
            await db_session.scalar(
                text(
                    "UPDATE solution_deployments SET state=:state WHERE id=:id AND state=:previous RETURNING state"
                ),
                {"id": deployment, "state": state, "previous": previous},
            )
            == state
        )
        previous = state
    await db_session.execute(
        text(
            "UPDATE solutions SET active_deployment_id=:deployment,execution_runtime_mode='deployment-v1' WHERE id=:solution"
        ),
        {"deployment": deployment, "solution": solution},
    )
    await db_session.commit()
    caller_row = (
        (
            await db_session.execute(
                text(
                    "SELECT u.id,u.organization_id,u.email,COALESCE(u.name,'') AS name,u.is_superuser,org.is_provider,u.is_external FROM users u JOIN organizations org ON org.id=u.organization_id WHERE u.id=:id AND u.is_active"
                ),
                {"id": platform_admin.user_id},
            )
        )
        .mappings()
        .one()
    )
    role_names = (
        (
            await db_session.execute(
                text(
                    'SELECT DISTINCT r.name FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=:id ORDER BY r.name COLLATE "C"'
                ),
                {"id": platform_admin.user_id},
            )
        )
        .scalars()
        .all()
    )
    caller = AuthorizedCallerSnapshot(
        caller_user_id=caller_row["id"],
        caller_organization_id=caller_row["organization_id"],
        effective_organization_id=caller_row["organization_id"],
        caller_email=caller_row["email"],
        caller_name=caller_row["name"],
        caller_admin=caller_row["is_superuser"],
        caller_provider=caller_row["is_provider"],
        caller_external=caller_row["is_external"]
        and not (caller_row["is_superuser"] or caller_row["is_provider"]),
        roles=tuple(role_names),
    )
    source = AcceptedManifestIdentity(
        source_id=deployment,
        solution_install_id=solution,
        source_manifest_digest=manifest_hash,
        source_resolution_digest=resolution_hash,
        source_global_permission=False,
    )
    policy = SelectedSDKPolicy(
        operations=(
            SDKOperation(
                operation="integration-get",
                integration_name="Fixture",
                scope_kind="organization",
                scope_organization_id=PROVIDER_ORG_ID,
                resolved_organization_id=PROVIDER_ORG_ID,
                solution_install_id=solution,
            ),
        )
    )
    proofs = []
    for number in range(2):
        case = root / str(number)
        case.mkdir(mode=0o700)
        runtime_root = case / "execution"
        runtime_root.mkdir(mode=0o700)
        expected_result = {
            "ready": number == 0,
            "missing_keys": [] if number == 0 else ["timezone"],
        }
        required_keys = ["region", " region ", ""] + (
            ["timezone"] if number == 1 else []
        )
        execution, attempt, session, supervisor, runtime, grant = (
            uuid4() for _ in range(6)
        )
        binding = {
            "kind": "execution-binding/v1",
            "execution_kind": "workflow",
            "execution_id": str(execution),
            "attempt_id": str(attempt),
            "attempt_number": 1,
            "solution_id": str(solution),
            "deployment_id": str(deployment),
            "artifact_id": descriptor["artifact"]["artifact_id"],
            "session_id": str(session),
            "supervisor_incarnation_id": str(supervisor),
            "runtime_incarnation_id": str(runtime),
            "original_caller": {
                "caller_id": str(platform_admin.user_id),
                "organization_id": str(PROVIDER_ORG_ID),
            },
            "effective_scope": {
                "kind": "organization",
                "organization_id": str(PROVIDER_ORG_ID),
            },
        }
        context = {
            k: binding[k]
            for k in (
                "execution_kind",
                "execution_id",
                "attempt_id",
                "attempt_number",
                "solution_id",
                "deployment_id",
                "artifact_id",
                "effective_scope",
            )
        }
        context.update(kind="tenant-context/v1", caller_id=str(platform_admin.user_id))
        prepare = {
            "protocol": "bifrost.runtime/v1",
            "type": "Prepare",
            "session_id": str(session),
            "message_id": str(uuid4()),
            "sequence": 2,
            "correlation_id": str(uuid4()),
            "body": {
                "binding": binding,
                "artifact": descriptor["artifact"],
                "context": context,
                "workload": {
                    "input": {"integration_name": "Fixture", "required_keys": []},
                    "input_schema_digest": "sha256:" + digest(input_raw),
                    "output_schema_digest": "sha256:" + digest(output_raw),
                    "deadline_utc": (datetime.now(UTC) + timedelta(seconds=30))
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                },
            },
        }
        config = {
            "root": str(runtime_root),
            "descriptor": str(producer / "native-bundle-descriptor.json"),
            "archive": str(producer / "native-bundle.tar"),
            "image_id": build["guardian_carrier"]["image_id"],
            "nonce": str(uuid4()),
            "prepare": prepare,
            "workflow_id": str(workflow),
            "caller_id": str(platform_admin.user_id),
        }
        for key in (
            "owner_id",
            "claim_token",
            "worker_id",
            "select_message_id",
            "start_id",
            "start_message_id",
            "decision_id",
            "receipt_message_id",
        ):
            config[key] = str(uuid4())
        publish(case / "owner-config.json", config)
        publish(case / "launch-ready.json", {"event": "launch_ready"})
        ingress = await observation(case / "owner-ingress.json")
        assert (
            ingress["event"] == "ingress_required"
            and ingress["owner_uid"] == os.geteuid()
        )
        ticks = _start_ticks(ingress["owner_pid"])
        identity = dict(
            grant_id=grant,
            execution_id=execution,
            session_id=session,
            owner_incarnation_id=UUID(config["owner_id"]),
            attempt_id=attempt,
        )

        async def committed():
            return await load_committed_finite_snapshot(
                async_session_factory, **identity
            )

        keys = case / "keys"
        keys.mkdir(mode=0o700)
        cert, private = certificates(keys)
        app = build_isolated_sdk_app(
            grant_id=grant,
            load_snapshot=committed,
            caller=caller,
            source=source,
            policy=policy,
            gate_path=Path(ingress["gate_path"]),
            owner_pid=ingress["owner_pid"],
            owner_uid=ingress["owner_uid"],
            owner_start_ticks=ticks,
            session_factory=async_session_factory,
            fixture_integration_id=integration,
        )
        sdk_statuses = []

        @app.middleware("http")
        async def count_response(request, call_next):
            response = await call_next(request)
            sdk_statuses.append(response.status_code)
            return response

        server = IsolatedSDKServer(
            app,
            directory=Path(ingress["sdk_directory"]),
            socket_uid=os.geteuid(),
            certificate=cert,
            private_key=private,
        )
        issuer = None
        try:
            await server.start()
            publish(case / "ingress-ready.json", {"event": "ingress_ready"})
            start = await observation(case / "owner-start.json")
            assert start["event"] == "start_committed" and start["session_id"] == str(
                session
            )
            row = (
                (
                    await db_session.execute(
                        text(
                            "SELECT s.claim_token_digest,s.worker_incarnation_id,s.supervisor_incarnation_id,st.started_at,st.deadline_utc,clock_timestamp() AS issued,o.caller_snapshot FROM runtime_sessions s JOIN runtime_starts st ON st.session_id=s.id JOIN runtime_execution_owners o ON o.execution_id=s.execution_id WHERE s.id=:session AND s.execution_id=:execution AND s.owner_incarnation_id=:owner"
                        ),
                        {
                            "session": session,
                            "execution": execution,
                            "owner": UUID(config["owner_id"]),
                        },
                    )
                )
                .mappings()
                .one()
            )
            retained_caller = AuthorizedCallerSnapshot.model_validate_json(
                json.dumps(row["caller_snapshot"])
            )
            assert retained_caller == caller
            snapshot = GrantSnapshot(
                id=grant,
                schema_version="cred-p1/v1",
                workflow_attempt_id=attempt,
                execution_id=execution,
                attempt_number=1,
                claim_token_digest=row["claim_token_digest"],
                worker_incarnation_id=row["worker_incarnation_id"],
                supervisor_incarnation_id=row["supervisor_incarnation_id"],
                runtime_session_id=session,
                started_at=row["started_at"],
                issued_at=row["issued"],
                timeout_seconds=30,
                credential_deadline=row["deadline_utc"],
                initial_access_expires_at=row["deadline_utc"],
                caller_user_id=caller.caller_user_id,
                caller_organization_id=caller.caller_organization_id,
                effective_organization_id=caller.effective_organization_id,
                caller_email=caller.caller_email,
                caller_name=caller.caller_name,
                caller_admin=int(caller.caller_admin),
                caller_provider=int(caller.caller_provider),
                caller_external=int(caller.caller_external),
                caller_snapshot_digest=caller_digest(caller),
                workflow_id=workflow,
                solution_install_id=solution,
                source_kind="solution-deployment",
                source_id=deployment,
                source_manifest_digest=manifest_hash,
                source_resolution_digest=resolution_hash,
                source_global_permission=0,
                source_digest=source_digest(source),
                operations_digest=operations_digest(policy),
            )
            issuer_dir = case / "issuer"
            issuer_dir.mkdir(mode=0o700)
            issuer = FiniteIssuerServer(
                directory=issuer_dir,
                owner_pid=ingress["owner_pid"],
                owner_uid=ingress["owner_uid"],
                owner_start_ticks=ticks,
                grant_id=grant,
                load_snapshot=committed,
                caller=caller,
                source=source,
                policy=policy,
                ca_pem=cert.read_text(),
            )
            await issuer.start()
            provision = {
                "event": "provision_ready",
                "snapshot": snapshot.model_dump(mode="json"),
                "grant_digest": grant_digest(snapshot),
                "frontier_sha256": digest(
                    canonical(
                        {
                            "binding_sha256": start["binding_sha256"],
                            "channel_custody_sha256": start["channel_custody_sha256"],
                            "grant_digest": grant_digest(snapshot),
                            "committed_start_id": start["start"]["body"][
                                "committed_start_id"
                            ],
                        }
                    )
                ),
                "issuer": {
                    "uid": os.geteuid(),
                    "pid": os.getpid(),
                    "ticks": _start_ticks(os.getpid()),
                    "path": str(issuer.path),
                    "ca": cert.read_text(),
                },
            }
            for key in (
                "provision_id",
                "delivery_id",
                "release_id",
                "provision_message_id",
            ):
                provision[key] = str(uuid4())
            publish(case / "provision-ready.json", provision)
            result = await observation(case / "owner-result.json")
            assert result["event"] == "slice_result"
            assert (
                result["component"]["sdk_admissions"] == 1
                and result["component"]["sdk_denials"] == 0
            )
            assert result["component"]["log_batches"] == 1 and sdk_statuses == [200]
            assert (
                result["physical_drain"]["container_removed"]
                and result["physical_drain"]["namespace_drained"]
            )
            assert (
                result["source_staging_removed"]
                and not (runtime_root / "bundle").exists()
            )
            exited = await observation(case / "owner-exited.json")
            assert exited == {
                "event": "owner_exited",
                "pid": ingress["owner_pid"],
                "exit_code": 0,
            }
            response = await e2e_client.get(
                f"/api/executions/{execution}", headers=platform_admin.headers
            )
            assert response.status_code == 200
            public = response.json()
            assert public["status"] == "Success" and public["result"] == expected_result
            receipt = (
                (
                    await db_session.execute(
                        text(
                            "SELECT result_message_id,result_sha256,raw_result_payload,decision_id,disposition,winner FROM runtime_report_receipts WHERE session_id=:session"
                        ),
                        {"session": session},
                    )
                )
                .mappings()
                .one()
            )
            assert digest(receipt["raw_result_payload"]) == receipt["result_sha256"]
            retained_receipt = {
                key: str(receipt[key])
                for key in (
                    "result_message_id",
                    "result_sha256",
                    "decision_id",
                    "disposition",
                    "winner",
                )
            }
            assert (
                retained_receipt == result["component"]["receipt"]
                and receipt["disposition"] == "accepted"
            )
            proofs.append(
                {
                    "execution_id": str(execution),
                    "session_id": str(session),
                    "workflow_sha256": WORKFLOW_SHA,
                    "artifact_id": descriptor["artifact"]["artifact_id"],
                    "sdk_statuses": sdk_statuses,
                    "receipt": retained_receipt,
                    "result_sha256": receipt["result_sha256"],
                    "public_status": public["status"],
                    "public_result": public["result"],
                    "physical_drain": result["physical_drain"],
                    "elapsed_ms": result["elapsed_ms"],
                }
            )
        finally:
            if issuer is not None:
                await issuer.stop()
                assert not list(issuer_dir.iterdir())
                issuer_dir.rmdir()
            await server.stop()
            Path(ingress["sdk_directory"]).rmdir()
            cert.unlink()
            private.unlink()
            keys.rmdir()
        publish(case / "fixture-finished.json", {"event": "fixture_finished"})
    assert len(proofs) == 2 and proofs[0]["artifact_id"] == proofs[1]["artifact_id"]
    publish(
        root / "live-proof.json",
        {
            "source": descriptor["source_commit"],
            "producer_run": descriptor["producer_run_id"],
            "sdk_version": "0.0.0-spike.2",
            "executions": proofs,
            "runtime_acceptance": False,
            "durable_source_settlement": False,
            "production_dispatch": False,
        },
    )
