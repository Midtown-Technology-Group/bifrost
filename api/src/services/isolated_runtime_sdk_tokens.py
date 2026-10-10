"""Finite CRED-P1 signing candidate; no route, grant writer or renewal hook.

The trusted issuer must independently authenticate the committed Rust owner,
Start, grant, source admission and live session before invoking this helper.
Matching supplied models/digests is consistency evidence, never that authority.
Signing keys remain in the platform issuer, outside builders and workloads.
"""

from datetime import datetime, timedelta

from pydantic import Field

from src.core.runtime_sdk_credentials import (
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    GrantReference,
    GrantSnapshot,
    RuntimeSDKDenied,
    SelectedSDKPolicy,
    _PrivateRecord,
    caller_digest,
    epoch_us,
    grant_digest,
    operations_digest,
    sign_runtime_sdk_token,
    source_digest,
)


class FiniteAccessCredential(_PrivateRecord):
    access_token: str = Field(repr=False)
    expires_at: datetime


def sign_finite_runtime_sdk_access(
    snapshot: GrantSnapshot,
    caller: AuthorizedCallerSnapshot,
    source: AcceptedManifestIdentity,
    policy: SelectedSDKPolicy,
    reference: GrantReference,
    *,
    now: datetime,
) -> FiniteAccessCredential:
    """Check the unchanged CRED-P1 preimages and expose only finite access.

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
        signed = sign_runtime_sdk_token(
            reference,
            issued_at=snapshot.issued_at,
            expires_at=snapshot.initial_access_expires_at,
        )
        return FiniteAccessCredential(
            access_token=signed.access_token, expires_at=signed.expires_at
        )
    except (ValueError, TypeError, OverflowError):
        raise RuntimeSDKDenied("finite runtime SDK signing denied") from None
