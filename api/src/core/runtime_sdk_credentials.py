"""Private CRED-P1 contracts; never a general JWT or context serializer.

Only trusted issuers use this module. No route or runtime hook accepts these
credentials yet. Source permissions are deliberately empty in this packet.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RUNTIME_SDK_AUDIENCE = "bifrost-workflow-runtime-sdk"
RUNTIME_SDK_PURPOSE = "workflow-runtime-sdk/v1"
GRANT_SCHEMA_VERSION = "cred-p1/v1"
MAX_TIMESTAMP_US = 253402300799999999
MAX_TIMESTAMP_SECONDS = MAX_TIMESTAMP_US // 1_000_000
RENEWAL_SECONDS = 600
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
EpochUS = Annotated[int, Field(strict=True, ge=0, le=MAX_TIMESTAMP_US)]
Flag = Annotated[int, Field(strict=True, ge=0, le=1)]
Timeout = Annotated[int, Field(strict=True, ge=0, le=2147483647)]
Digest = Annotated[
    str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64, strict=True)
]
SourceHash = Annotated[
    str,
    Field(pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71, strict=True),
]


class RuntimeSDKDenied(ValueError):
    """A closed credential contract failed; messages never include inputs."""


def bounded_text(value: str, limit: int) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("invalid bounded text")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid bounded text") from None
    if len(raw) > limit:
        raise ValueError("bounded text exceeds byte limit")
    return value


def epoch_us(value: datetime) -> int:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    delta = value - _EPOCH
    result = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if not 0 <= result <= MAX_TIMESTAMP_US:
        raise ValueError("timestamp is outside contract")
    return result


def utc_from_us(value: int) -> datetime:
    if type(value) is not int or not 0 <= value <= MAX_TIMESTAMP_US:
        raise ValueError("timestamp is outside contract")
    return _EPOCH + timedelta(microseconds=value)


class _PrivateRecord(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class CommittedWorkflowStart(_PrivateRecord):
    execution_id: UUID
    workflow_attempt_id: UUID
    attempt_number: Annotated[int, Field(ge=1, le=2147483647)]
    private_claim_token: UUID = Field(repr=False)
    worker_incarnation_id: UUID
    supervisor_incarnation_id: UUID
    runtime_session_id: UUID
    committed_started_at_us: EpochUS
    timeout_seconds: Timeout
    predeclared_credential_deadline_us: EpochUS | None
    predeclared_initial_access_expires_at_us: EpochUS

    @model_validator(mode="after")
    def deadline_shape(self) -> "CommittedWorkflowStart":
        if self.timeout_seconds == 0:
            if self.predeclared_credential_deadline_us is not None:
                raise ValueError("no-timeout work has no hard deadline")
        elif (
            self.predeclared_credential_deadline_us is None
            or self.predeclared_credential_deadline_us
            != self.predeclared_initial_access_expires_at_us
        ):
            raise ValueError(
                "finite initial expiry must preserve the declared deadline"
            )
        return self


class AuthorizedCallerSnapshot(_PrivateRecord):
    caller_user_id: UUID
    caller_organization_id: UUID | None
    effective_organization_id: UUID | None
    caller_email: str
    caller_name: str
    caller_admin: bool
    caller_provider: bool
    caller_external: bool
    roles: tuple[str, ...]

    @field_validator("caller_email")
    @classmethod
    def email_bound(cls, value: str) -> str:
        return bounded_text(value, 320)

    @field_validator("caller_name")
    @classmethod
    def name_bound(cls, value: str) -> str:
        return bounded_text(value, 255)

    @field_validator("roles")
    @classmethod
    def role_bounds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 256 or value != tuple(sorted(set(value))):
            raise ValueError("roles must be bounded, unique and sorted")
        for role in value:
            bounded_text(role, 255)
        return value


class SDKOperation(_PrivateRecord):
    operation: Literal["integration-get", "mapping-get"]
    integration_name: str
    scope_kind: Literal["default", "global", "organization"]
    scope_organization_id: UUID | None
    resolved_organization_id: UUID | None
    solution_install_id: UUID | None

    @field_validator("integration_name")
    @classmethod
    def name_bound(cls, value: str) -> str:
        bounded_text(value, 255)
        if not value or value != value.strip():
            raise ValueError("integration name must be nonempty and trimmed")
        return value

    @model_validator(mode="after")
    def scope_shape(self) -> "SDKOperation":
        if (self.scope_kind == "organization") != (
            self.scope_organization_id is not None
        ):
            raise ValueError("invalid operation scope")
        if self.scope_kind == "global" and self.resolved_organization_id is not None:
            raise ValueError("global scope must resolve to global")
        if (
            self.scope_kind == "organization"
            and self.resolved_organization_id != self.scope_organization_id
        ):
            raise ValueError("organization scope must resolve exactly")
        if self.operation == "mapping-get" and self.solution_install_id is not None:
            raise ValueError("mapping request has no solution selector")
        return self


class SelectedSDKPolicy(_PrivateRecord):
    operations: tuple[SDKOperation, ...]

    @field_validator("operations")
    @classmethod
    def operation_bounds(
        cls, value: tuple[SDKOperation, ...]
    ) -> tuple[SDKOperation, ...]:
        names = tuple(item.operation for item in value)
        if len(value) > 2 or names != tuple(sorted(set(names))):
            raise ValueError("operations must be bounded, unique and sorted")
        return value


class AcceptedManifestIdentity(_PrivateRecord):
    source_id: UUID
    solution_install_id: UUID
    source_manifest_digest: SourceHash
    source_resolution_digest: SourceHash
    source_global_permission: bool


class GrantReference(_PrivateRecord):
    grant_id: UUID
    grant_digest: Digest


class CredentialBundle(_PrivateRecord):
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    expires_at: datetime


class GrantSnapshot(_PrivateRecord):
    """Every immutable grant column, in an explicitly frozen private order."""

    id: UUID
    schema_version: Literal["cred-p1/v1"]
    workflow_attempt_id: UUID
    execution_id: UUID
    attempt_number: Annotated[int, Field(ge=1, le=2147483647)]
    claim_token_digest: Digest
    worker_incarnation_id: UUID
    supervisor_incarnation_id: UUID
    runtime_session_id: UUID
    started_at: datetime
    issued_at: datetime
    timeout_seconds: Timeout
    credential_deadline: datetime | None
    initial_access_expires_at: datetime
    caller_user_id: UUID
    caller_organization_id: UUID | None
    effective_organization_id: UUID | None
    caller_email: str
    caller_name: str
    caller_admin: Flag
    caller_provider: Flag
    caller_external: Flag
    caller_snapshot_digest: Digest
    workflow_id: UUID
    solution_install_id: UUID
    source_kind: Literal["solution-deployment"]
    source_id: UUID
    source_manifest_digest: SourceHash
    source_resolution_digest: SourceHash
    source_global_permission: Flag
    source_digest: Digest
    operations_digest: Digest

    @field_validator(
        "started_at", "issued_at", "credential_deadline", "initial_access_expires_at"
    )
    @classmethod
    def time_bound(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            epoch_us(value)
        return value

    @field_validator("caller_email")
    @classmethod
    def email_bound(cls, value: str) -> str:
        return bounded_text(value, 320)

    @field_validator("caller_name")
    @classmethod
    def name_bound(cls, value: str) -> str:
        return bounded_text(value, 255)

    @model_validator(mode="after")
    def deadline_shape(self) -> "GrantSnapshot":
        if self.timeout_seconds == 0:
            if self.credential_deadline is not None:
                raise ValueError("no-timeout work has no hard deadline")
        elif self.credential_deadline != self.initial_access_expires_at:
            raise ValueError("finite initial expiry must preserve its deadline")
        return self


# Order and optional tags are part of CRED-P1; never derive this from model_dump.
GRANT_FIELDS = (
    "id",
    "schema_version",
    "workflow_attempt_id",
    "execution_id",
    "attempt_number",
    "claim_token_digest",
    "worker_incarnation_id",
    "supervisor_incarnation_id",
    "runtime_session_id",
    "started_at",
    "issued_at",
    "timeout_seconds",
    "credential_deadline",
    "initial_access_expires_at",
    "caller_user_id",
    "caller_organization_id",
    "effective_organization_id",
    "caller_email",
    "caller_name",
    "caller_admin",
    "caller_provider",
    "caller_external",
    "caller_snapshot_digest",
    "workflow_id",
    "solution_install_id",
    "source_kind",
    "source_id",
    "source_manifest_digest",
    "source_resolution_digest",
    "source_global_permission",
    "source_digest",
    "operations_digest",
)
_OPTIONAL_GRANT_FIELDS = {
    "credential_deadline",
    "caller_organization_id",
    "effective_organization_id",
}


def _field(value: str | int | UUID | datetime) -> bytes:
    if isinstance(value, datetime):
        value = epoch_us(value)
    if isinstance(value, UUID):
        value = str(value)
    if type(value) is int:
        if value < 0:
            raise ValueError("negative codec integer")
        value = str(value)
    if not isinstance(value, str):
        raise TypeError("unsupported private codec field")
    raw = value.encode("utf-8")
    return _frame(raw)


def _frame(raw: bytes) -> bytes:
    return str(len(raw)).encode("ascii") + b":" + raw + b","


def _optional(value: UUID | datetime | None) -> bytes:
    return _field(0) if value is None else _field(1) + _field(value)


def claim_digest(claim: UUID) -> str:
    if not isinstance(claim, UUID):
        raise TypeError("invalid private claim")
    return hashlib.sha256(_field("cred-p1/claim/v1") + _field(claim)).hexdigest()


def caller_digest(caller: AuthorizedCallerSnapshot) -> str:
    caller = AuthorizedCallerSnapshot.model_validate(caller)
    raw = _field("cred-p1/caller/v1") + _field(caller.caller_user_id)
    raw += _optional(caller.caller_organization_id)
    raw += _field(caller.caller_email) + _field(caller.caller_name)
    raw += b"".join(
        _field(int(flag))
        for flag in (
            caller.caller_admin,
            caller.caller_provider,
            caller.caller_external,
        )
    )
    raw += _field(len(caller.roles)) + b"".join(_field(role) for role in caller.roles)
    return hashlib.sha256(raw).hexdigest()


def source_digest(source: AcceptedManifestIdentity) -> str:
    source = AcceptedManifestIdentity.model_validate(source)
    # Zero lookup records: this packet grants no source HTTP operation.
    raw = b"".join(
        _field(value)
        for value in (
            "cred-p1/source/v1",
            "solution-deployment",
            source.source_id,
            source.source_manifest_digest,
            source.source_resolution_digest,
            source.solution_install_id,
            int(source.source_global_permission),
            0,
        )
    )
    return hashlib.sha256(raw).hexdigest()


def operations_digest(policy: SelectedSDKPolicy) -> str:
    policy = SelectedSDKPolicy.model_validate(policy)
    raw = _field("cred-p1/operations/v1") + _field(len(policy.operations))
    for ordinal, operation in enumerate(policy.operations):
        record = (
            _field(ordinal)
            + _field(operation.operation)
            + _field(operation.integration_name)
        )
        record += _field(operation.scope_kind) + _optional(
            operation.scope_organization_id
        )
        record += _optional(operation.resolved_organization_id) + _optional(
            operation.solution_install_id
        )
        raw += _frame(record)
    return hashlib.sha256(raw).hexdigest()


def grant_digest(snapshot: GrantSnapshot) -> str:
    snapshot = GrantSnapshot.model_validate(snapshot)
    raw = _field("cred-p1/grant/v1")
    for name in GRANT_FIELDS:
        value = getattr(snapshot, name)
        raw += _optional(value) if name in _OPTIONAL_GRANT_FIELDS else _field(value)
    return hashlib.sha256(raw).hexdigest()


class RuntimeSDKTokenClaims(_PrivateRecord):
    iss: str
    aud: Literal["bifrost-workflow-runtime-sdk"]
    sub: str
    type: Literal["access"]
    purpose: Literal["workflow-runtime-sdk/v1"]
    grant_digest: Digest
    iat: Annotated[int, Field(ge=0, le=MAX_TIMESTAMP_SECONDS)]
    exp: Annotated[int, Field(ge=0, le=MAX_TIMESTAMP_SECONDS)]
    jti: str

    @field_validator("iss")
    @classmethod
    def issuer_bound(cls, value: str) -> str:
        return bounded_text(value, 255)

    @field_validator("sub", "jti")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("token UUID must be canonical")
        return value


def sign_runtime_sdk_token(
    reference: GrantReference, *, issued_at: datetime, expires_at: datetime
) -> CredentialBundle:
    try:
        return _sign_runtime_sdk_token(
            reference, issued_at=issued_at, expires_at=expires_at
        )
    except (jwt.PyJWTError, ValueError, TypeError, OverflowError):
        raise RuntimeSDKDenied("runtime SDK token issuance denied") from None


def _sign_runtime_sdk_token(
    reference: GrantReference, *, issued_at: datetime, expires_at: datetime
) -> CredentialBundle:
    from src.config import get_settings

    settings = get_settings()
    reference = GrantReference.model_validate(reference)
    if settings.jwt_audience == RUNTIME_SDK_AUDIENCE:
        raise RuntimeSDKDenied("dedicated audience collides with application audience")
    claims = RuntimeSDKTokenClaims(
        iss=settings.jwt_issuer,
        aud=RUNTIME_SDK_AUDIENCE,
        sub=str(reference.grant_id),
        type="access",
        purpose=RUNTIME_SDK_PURPOSE,
        grant_digest=reference.grant_digest,
        iat=epoch_us(issued_at) // 1_000_000,
        exp=epoch_us(expires_at) // 1_000_000,
        jti=str(uuid4()),
    )
    if claims.exp <= max(claims.iat, epoch_us(datetime.now(UTC)) // 1_000_000):
        raise RuntimeSDKDenied("credential deadline is not usable")
    token = jwt.encode(
        claims.model_dump(),
        settings.secret_key,
        algorithm=settings.algorithm,
        headers={"typ": "JWT"},
    )
    if len(token.encode("utf-8")) > 4096:
        raise RuntimeSDKDenied("token exceeds contract")
    return CredentialBundle(
        access_token=token,
        refresh_token=token,
        expires_at=utc_from_us(claims.exp * 1_000_000),
    )


def decode_runtime_sdk_access(
    token: str, *, allow_expired_for_renewal: bool = False
) -> RuntimeSDKTokenClaims:
    from src.config import get_settings

    settings = get_settings()
    try:
        if type(allow_expired_for_renewal) is not bool:
            raise ValueError("invalid renewal mode")
        bounded_text(token, 4096)
        if settings.jwt_audience == RUNTIME_SDK_AUDIENCE:
            raise ValueError("audience collision")
        header = jwt.get_unverified_header(token)
        if (
            set(header) != {"alg", "typ"}
            or header["typ"] != "JWT"
            or header["alg"] != settings.algorithm
        ):
            raise ValueError("invalid token header")
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            issuer=settings.jwt_issuer,
            audience=RUNTIME_SDK_AUDIENCE,
            options={
                "verify_signature": True,
                "verify_exp": not allow_expired_for_renewal,
                "strict_aud": True,
                "require": list(RuntimeSDKTokenClaims.model_fields),
            },
        )
        return RuntimeSDKTokenClaims.model_validate(payload)
    except (jwt.PyJWTError, ValueError, TypeError):
        raise RuntimeSDKDenied("invalid runtime SDK token") from None
