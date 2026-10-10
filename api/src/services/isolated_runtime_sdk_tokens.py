"""Finite CRED-P1 signing candidate; no route, grant writer or renewal hook.

The trusted issuer must independently authenticate the committed Rust owner,
Start, grant, source admission and live session before invoking this helper.
Matching supplied models/digests is consistency evidence, never that authority.
Signing keys remain in the platform issuer, outside builders and workloads.
"""

import json
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import Field

from src.core.runtime_sdk_credentials import (
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    Digest,
    GrantReference,
    GrantSnapshot,
    RuntimeSDKDenied,
    SelectedSDKPolicy,
    _PrivateRecord,
    caller_digest,
    decode_runtime_sdk_access,
    epoch_us,
    grant_digest,
    operations_digest,
    sign_runtime_sdk_token,
    source_digest,
)


class FiniteAccessCredential(_PrivateRecord):
    access_token: str = Field(repr=False)
    expires_at: datetime


def validate_finite_runtime_sdk_preimages(
    snapshot: GrantSnapshot,
    caller: AuthorizedCallerSnapshot,
    source: AcceptedManifestIdentity,
    policy: SelectedSDKPolicy,
    reference: GrantReference,
    *,
    now: datetime,
) -> tuple[
    GrantSnapshot,
    AuthorizedCallerSnapshot,
    AcceptedManifestIdentity,
    SelectedSDKPolicy,
    GrantReference,
]:
    """Check and return validated unchanged CRED-P1 preimages.

    No row is created, renewed, reopened or finalized. A failed/uncertain issuer
    admission must never reach this helper, and an issued token is not release
    permission. No-timeout, global/provider expansion and mapping-get are outside
    this isolated readiness slice.
    """
    try:
        snapshot = GrantSnapshot.model_validate(snapshot)
        caller = AuthorizedCallerSnapshot.model_validate(caller)
        source = AcceptedManifestIdentity.model_validate(source)
        policy = SelectedSDKPolicy.model_validate(policy)
        reference = GrantReference.model_validate(reference)
        epoch_us(now)
        if (
            snapshot.timeout_seconds <= 0
            or snapshot.credential_deadline is None
            or snapshot.credential_deadline != snapshot.initial_access_expires_at
            or not snapshot.started_at <= snapshot.issued_at <= now
            or snapshot.initial_access_expires_at <= now
            or snapshot.initial_access_expires_at
            > snapshot.started_at + timedelta(seconds=snapshot.timeout_seconds)
            or reference.grant_id != snapshot.id
            or reference.grant_digest != grant_digest(snapshot)
            or snapshot.caller_snapshot_digest != caller_digest(caller)
            or snapshot.source_digest != source_digest(source)
            or snapshot.operations_digest != operations_digest(policy)
            or snapshot.source_global_permission != 0
            or source.source_global_permission
            or snapshot.caller_user_id != caller.caller_user_id
            or snapshot.caller_organization_id != caller.caller_organization_id
            or snapshot.effective_organization_id != caller.effective_organization_id
            or snapshot.caller_email != caller.caller_email
            or snapshot.caller_name != caller.caller_name
            or snapshot.caller_admin != int(caller.caller_admin)
            or snapshot.caller_provider != int(caller.caller_provider)
            or snapshot.caller_external != int(caller.caller_external)
            or snapshot.caller_organization_id is None
            or snapshot.effective_organization_id != snapshot.caller_organization_id
            or snapshot.source_id != source.source_id
            or snapshot.solution_install_id != source.solution_install_id
            or snapshot.source_manifest_digest != source.source_manifest_digest
            or snapshot.source_resolution_digest != source.source_resolution_digest
            or len(policy.operations) != 1
        ):
            raise RuntimeSDKDenied("finite runtime SDK signing denied")
        operation = policy.operations[0]
        if (
            operation.operation != "integration-get"
            or operation.scope_kind != "organization"
            or operation.scope_organization_id != snapshot.effective_organization_id
            or operation.resolved_organization_id != snapshot.effective_organization_id
            or operation.solution_install_id != snapshot.solution_install_id
        ):
            raise RuntimeSDKDenied("finite runtime SDK signing denied")
        return snapshot, caller, source, policy, reference
    except (ValueError, TypeError, OverflowError):
        raise RuntimeSDKDenied("finite runtime SDK signing denied") from None


def sign_finite_runtime_sdk_access(
    snapshot: GrantSnapshot,
    caller: AuthorizedCallerSnapshot,
    source: AcceptedManifestIdentity,
    policy: SelectedSDKPolicy,
    reference: GrantReference,
    *,
    now: datetime,
) -> FiniteAccessCredential:
    """Expose only finite access after checking all immutable preimages."""
    snapshot, caller, source, policy, reference = validate_finite_runtime_sdk_preimages(
        snapshot, caller, source, policy, reference, now=now
    )
    signed = sign_runtime_sdk_token(
        reference,
        issued_at=snapshot.issued_at,
        expires_at=snapshot.initial_access_expires_at,
    )
    return FiniteAccessCredential(
        access_token=signed.access_token, expires_at=signed.expires_at
    )


class FiniteIntegrationGetIntent(_PrivateRecord):
    """Verified request consistency, NOT owner admission or live custody.

    The trusted parent must still derive the session fence from retained rows,
    establish current source/caller entitlement and live guardian custody, then
    obtain Rust SDK admission before invoking the stable capability. This object
    contains no bearer and cannot authorize a capability by itself.
    """

    grant_id: UUID
    grant_digest: Digest
    integration_name: str
    organization_id: UUID
    solution_id: UUID


def verify_finite_integration_get(
    access_token: str,
    raw_body: bytes,
    snapshot: GrantSnapshot,
    caller: AuthorizedCallerSnapshot,
    source: AcceptedManifestIdentity,
    policy: SelectedSDKPolicy,
    *,
    now: datetime,
) -> FiniteIntegrationGetIntent:
    """Dedicated SDK ingress; no general auth, DB write, renewal or fetch.

    Accept the existing SDK JSON shape, with exact admitted organization and
    installation. OAuth scope overrides may cause vendor mutation and are denied
    in this isolated slice. All authority preimages are parent-loaded inputs,
    never request-supplied context, claims or fence values.
    """
    try:
        claims = decode_runtime_sdk_access(access_token)
        reference = GrantReference(
            grant_id=UUID(claims.sub), grant_digest=claims.grant_digest
        )
        snapshot, caller, source, policy, reference = (
            validate_finite_runtime_sdk_preimages(
                snapshot, caller, source, policy, reference, now=now
            )
        )
        if (
            claims.iat != epoch_us(snapshot.issued_at) // 1_000_000
            or claims.exp != epoch_us(snapshot.initial_access_expires_at) // 1_000_000
            or claims.exp <= epoch_us(now) // 1_000_000
            or type(raw_body) is not bytes
            or not 0 < len(raw_body) <= 8192
        ):
            raise ValueError("invalid finite ingress")

        def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate request field")
                result[key] = value
            return result

        request = json.loads(raw_body.decode("utf-8"), object_pairs_hook=closed_object)
        operation = policy.operations[0]
        if (
            not isinstance(request, dict)
            or not {"name", "scope", "solution"}.issubset(request)
            or not set(request).issubset({"name", "scope", "solution", "oauth_scope"})
            or request["name"] != operation.integration_name
            or request["scope"] != str(operation.scope_organization_id)
            or request["solution"] != str(operation.solution_install_id)
            or request.get("oauth_scope") is not None
        ):
            raise ValueError("request is outside admitted SDK policy")
        # Preimage validation above proves these nullable contract fields present.
        if snapshot.effective_organization_id is None:
            raise ValueError("organization required")
        return FiniteIntegrationGetIntent(
            grant_id=snapshot.id,
            grant_digest=reference.grant_digest,
            integration_name=operation.integration_name,
            organization_id=snapshot.effective_organization_id,
            solution_id=snapshot.solution_install_id,
        )
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise RuntimeSDKDenied("finite runtime SDK request denied") from None
