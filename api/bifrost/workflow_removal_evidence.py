"""Signed historical workflow-retirement observations; never deployment authority.

The trusted producer owns authoritative scanning. This export seam signs only
complete, post-reconciliation observations, including disabled objects. Reviewers
supply target identity and pinned public keys independently of the receipt.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVIDENCE_CONTRACT = "bifrost.workflow-removal-evidence/v1"
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Identity = Annotated[str, Field(min_length=1, max_length=255)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RemovalInventory(StrictModel):
    """One exhausted inventory; unresolved/dynamic references are not absence."""
    snapshot_revision: Identity

    @field_validator("complete", "pagination_exhausted", "includes_disabled", mode="before")
    @classmethod
    def strict_boolean(cls, value: Any) -> bool:
        if value is not True:
            raise ValueError("Inventory completeness requires a literal true")
        return value

    complete: Literal[True]
    pagination_exhausted: Literal[True]
    includes_disabled: Literal[True]
    remaining_references: list[Identity] = Field(max_length=0)
    unresolved_references: list[Identity] = Field(max_length=0)


class RemovalObservation(StrictModel):
    workflow_id: str = Field(min_length=36, max_length=36)
    phase: Literal["post-reconciliation"]
    observed_at: str = Field(max_length=64)
    snapshot_revision: Identity
    callers_dependencies: RemovalInventory
    event_sources: RemovalInventory
    subscriptions: RemovalInventory
    schedules: RemovalInventory

    @field_validator("workflow_id")
    @classmethod
    def uuid_string(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Workflow identity must be a canonical UUID")
        return value


class RemovalBinding(StrictModel):
    solution_id: str = Field(min_length=36, max_length=36)
    instance_origin: str = Field(max_length=1000)
    recipe_path: str = Field(max_length=1000)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_recipe_digest: Digest
    candidate_recipe_digest: Digest
    base_sources_digest: Digest
    candidate_sources_digest: Digest
    base_resources_digest: Digest
    candidate_resources_digest: Digest

    @field_validator("solution_id")
    @classmethod
    def uuid_string(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Solution identity must be a canonical UUID")
        return value

    @field_validator("instance_origin")
    @classmethod
    def origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment
                or value != f"https://{parsed.netloc.lower()}" or parsed.port == 443):
            raise ValueError("Expected a canonical HTTPS instance origin")
        return value

    @field_validator("recipe_path")
    @classmethod
    def path(cls, value: str) -> str:
        from bifrost.solution_delivery_review import delivery_path
        return delivery_path(value)


class RemovalProducer(StrictModel):
    contract: Literal["bifrost.workflow-removal-observer/v1"]
    issuer: Identity
    key_id: Identity
    build_digest: Digest
    run_id: Identity


class WorkflowRemovalEvidence(StrictModel):
    schema_version: Literal["bifrost.workflow-removal-evidence/v1"]
    binding: RemovalBinding
    producer: RemovalProducer
    issued_at: str = Field(max_length=64)
    expires_at: str = Field(max_length=64)
    observations: list[RemovalObservation] = Field(min_length=1, max_length=256)
    signature: str = Field(pattern=r"^[A-Za-z0-9+/]{86}==$", max_length=88)


class TrustedRemovalProducer(StrictModel):
    """Pinned by review policy, never imported from the evidence itself."""
    issuer: Identity
    key_id: Identity
    build_digest: Digest
    public_key: str = Field(pattern=r"^[A-Za-z0-9+/]{43}=$", max_length=44)


class WorkflowRemovalReviewContext(StrictModel):
    solution_id: str = Field(min_length=36, max_length=36)
    instance_origin: str = Field(max_length=1000)
    recipe_path: str
    base_sha: str
    trusted_producers: list[TrustedRemovalProducer] = Field(min_length=1, max_length=16)
    max_age_seconds: int = Field(default=900, gt=0, le=3600)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def removal_binding(*, solution_id: str, instance_origin: str, recipe_path: str,
                    base_sha: str, base_recipe: dict, candidate_recipe: dict,
                    base_files: dict[str, bytes], candidate_files: dict[str, bytes],
                    base_resources: dict[str, bytes], candidate_resources: dict[str, bytes]) -> RemovalBinding:
    """Bind normalized recipe models and every exact carried content byte."""
    from bifrost.solution_delivery_review import ReviewedWorkflowRecipe

    def recipe_digest(value: dict) -> str:
        return digest(ReviewedWorkflowRecipe.model_validate(value).model_dump(mode="json"))

    def content_digest(files: dict[str, bytes]) -> str:
        return digest({path: "sha256:" + hashlib.sha256(raw).hexdigest()
                       for path, raw in files.items()})

    return RemovalBinding(solution_id=solution_id, instance_origin=instance_origin,
        recipe_path=recipe_path, base_sha=base_sha,
        base_recipe_digest=recipe_digest(base_recipe), candidate_recipe_digest=recipe_digest(candidate_recipe),
        base_sources_digest=content_digest(base_files), candidate_sources_digest=content_digest(candidate_files),
        base_resources_digest=content_digest(base_resources), candidate_resources_digest=content_digest(candidate_resources))


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Evidence timestamps require a timezone")
    return parsed


def _require_observations(receipt: WorkflowRemovalEvidence, removed_ids: set[str],
                          now: datetime, max_age_seconds: int) -> None:
    identities = [item.workflow_id for item in receipt.observations]
    if len(set(identities)) != len(identities) or set(identities) != removed_ids:
        raise ValueError("Evidence must cover exactly the removed workflow IDs")
    issued, expires = _timestamp(receipt.issued_at), _timestamp(receipt.expires_at)
    if not issued <= now < expires or not 0 < (expires - issued).total_seconds() <= max_age_seconds:
        raise ValueError("Evidence is expired, future-dated or exceeds review freshness policy")
    for observation in receipt.observations:
        observed = _timestamp(observation.observed_at)
        if not 0 <= (now - observed).total_seconds() <= max_age_seconds or observed > issued:
            raise ValueError("Evidence observation is stale or future-dated")
        inventories = (observation.callers_dependencies, observation.event_sources,
                       observation.subscriptions, observation.schedules)
        if any(item.snapshot_revision != observation.snapshot_revision for item in inventories):
            raise ValueError("Evidence inventories require one coherent authoritative snapshot")


def export_workflow_removal_evidence(payload: dict[str, Any], *, private_key: bytes) -> dict[str, Any]:
    """Producer seam: export already collected authoritative observations.

    Only a trusted read-only observer service should hold this Ed25519 key. This
    function does not scan live state, reconcile references, or confer trust on
    operator-authored inventories. No key is shipped or stored by the SDK.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if "signature" in payload:
        raise ValueError("Export requires unsigned observation payload")
    receipt = WorkflowRemovalEvidence.model_validate({**payload, "signature": "A" * 86 + "=="})
    _require_observations(receipt, {item.workflow_id for item in receipt.observations},
                          datetime.now(timezone.utc), 3600)
    unsigned = receipt.model_dump(exclude={"signature"}, mode="json")
    signature = Ed25519PrivateKey.from_private_bytes(private_key).sign(canonical_bytes(unsigned))
    return {**unsigned, "signature": base64.b64encode(signature).decode("ascii")}


def verify_workflow_removal_evidence(evidence: dict[str, Any] | WorkflowRemovalEvidence, *,
                                    context: WorkflowRemovalReviewContext,
                                    expected_binding: RemovalBinding, removed_ids: set[str]) -> str:
    """Authenticate a bounded historical observation against external policy."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    # Round-trip model inputs too: model_construct/model_copy must not bypass validation.
    receipt = WorkflowRemovalEvidence.model_validate(
        evidence.model_dump(mode="json") if isinstance(evidence, WorkflowRemovalEvidence) else evidence)
    if receipt.binding != expected_binding:
        raise ValueError("Evidence target, baseline or content digests mismatch")
    _require_observations(receipt, removed_ids, datetime.now(timezone.utc), context.max_age_seconds)
    trusted = [item for item in context.trusted_producers
               if (item.issuer, item.key_id, item.build_digest)
               == (receipt.producer.issuer, receipt.producer.key_id, receipt.producer.build_digest)]
    if len(trusted) != 1:
        raise ValueError("Evidence producer is not uniquely trusted")
    try:
        signature = base64.b64decode(receipt.signature, validate=True)
        if base64.b64encode(signature).decode("ascii") != receipt.signature:
            raise ValueError("Noncanonical evidence signature encoding")
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(trusted[0].public_key, validate=True))
        key.verify(signature,
                   canonical_bytes(receipt.model_dump(exclude={"signature"}, mode="json")))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Evidence signature verification failed") from exc
    return digest(receipt.model_dump(mode="json"))
