"""CLI compatibility contract tripwire.

Two jobs:

1. **Runtime gate sync** — the CLI and server copies of the contract version
   must agree while ``GET /api/version`` exposes it for compatibility checks.

2. **Tripwire** — a fingerprint over the contract surface the CLI actually
   depends on (the request/response DTOs it sends + the routes it calls). Any
   change to that surface flips the fingerprint, failing this test until the
   author makes an explicit decision: bump ``CONTRACT_VERSION`` for a breaking
   change, or just refresh the fingerprint for a compatible additive/cosmetic
   change. This makes a missed compatibility decision a red test instead of a
   production incident.

The fingerprint is computed live, in-process, and only ever compared to a
constant committed in THIS file — never shipped or compared across machines —
so cross-machine schema-serialization differences are irrelevant.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

# Standalone bifrost SDK package import (mirrors test_contracts_parity.py).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.contract_version import CONTRACT_VERSION as CLI_CONTRACT_VERSION  # noqa: E402
from shared.contract_version import CONTRACT_VERSION as SERVER_CONTRACT_VERSION  # noqa: E402

# DTOs the CLI sends/receives. Server-canonical classes (the wire truth).
from src.models.contracts.agent_runs import (  # noqa: E402
    AgentRunCreateRequest,
    AgentRunEnqueueRequest,
    AgentRunEnqueueResponse,
)
from src.models.contracts.agents import AgentCreate, AgentUpdate  # noqa: E402
from src.models.contracts.applications import (  # noqa: E402
    ApplicationCreate,
    ApplicationUpdate,
)
from src.models.contracts.platform_jobs import (  # noqa: E402
    PlatformJobAccepted,
    PlatformJobPublic,
)
from src.models.contracts.access_checks import (  # noqa: E402
    AccessExplanation,
    AccessTrace,
)
from src.models.contracts.audit import AuditLogListResponse  # noqa: E402
from src.models.contracts.audit_retention import AuditRetentionStatus  # noqa: E402
from src.models.contracts.bridge_grants import (  # noqa: E402
    RoleBridgeGrantsResponse,
    RoleBridgeGrantsUpdate,
)
from src.models.contracts.identities import IdentityCreate, IdentityPublic  # noqa: E402
from src.models.contracts.permissions import PermissionCatalogEntry  # noqa: E402
from src.models.contracts.role_assignments import (  # noqa: E402
    UserRoleAssignmentsResponse,
)
from src.models.contracts.run_retention import RunRetentionStatus  # noqa: E402
from src.models.contracts.user_access import UserAccessMap  # noqa: E402
from src.models.contracts.claims import CustomClaimCreate, CustomClaimUpdate  # noqa: E402
from src.models.contracts.config import ConfigCreate, ConfigUpdate  # noqa: E402
from src.models.contracts.events import (  # noqa: E402
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
)
from src.models.contracts.executions import (  # noqa: E402
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
)
from src.models.contracts.forms import FormCreate, FormUpdate  # noqa: E402
from src.models.contracts.integrations import (  # noqa: E402
    IntegrationCreate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    IntegrationUpdate,
)
from src.models.contracts.organizations import (  # noqa: E402
    OrganizationCreate,
    OrganizationUpdate,
)
from src.models.contracts.solutions import (  # noqa: E402
    SolutionCandidateDeployEnqueued,
    SolutionDeployEnqueued,
    SolutionDeployJobStatus,
)
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate  # noqa: E402
from src.models.contracts.services import ServicePolicyUpdate  # noqa: E402
from src.models.contracts.tables import (  # noqa: E402
    ConditionalDocumentUpdate,
    DocumentBatchCreate,
    DocumentBatchCreateResponse,
    DocumentBatchUpsertResponse,
    DocumentUpdate,
    TableCreate,
    TableUpdate,
)
from src.models.contracts.recommended_access import RecommendedAccess  # noqa: E402
from src.models.contracts.users import RoleCreate, RoleUpdate  # noqa: E402
from src.models.contracts.workflows import WorkflowUpdateRequest  # noqa: E402
from src.models.contracts.workspace_promotions import (  # noqa: E402
    WorkspacePromotionCanaryAccepted,
    WorkspacePromotionCanaryRequest,
    WorkspacePromotionArtifactResponse,
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
    WorkspaceReleaseActivateRequest,
    WorkspaceReleasePrepareRequest,
    WorkspaceReleaseStatusResponse,
    WorkspaceLiveStatusResponse,
)

import inspect  # noqa: E402

from pydantic import BaseModel  # noqa: E402

from src.models.contracts import cli as _cli_contracts  # noqa: E402

# ---------------------------------------------------------------------------
# The contract surface the CLI depends on.
# ---------------------------------------------------------------------------

#: The explicit CRUD + execute DTOs the `bifrost <entity>` command surface uses.
_COMMAND_DTOS: list[type] = [
    OrganizationCreate,
    OrganizationUpdate,
    RoleCreate,
    RoleUpdate,
    # Manual bridge-grant CLI flags and SDK calls consume these REST DTOs;
    # they are outside the auto-discovered /api/sdk/* contract module.
    RoleBridgeGrantsUpdate,
    RoleBridgeGrantsResponse,
    WorkflowUpdateRequest,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    FormCreate,
    FormUpdate,
    AgentCreate,
    AgentUpdate,
    AgentRunCreateRequest,
    AgentRunEnqueueRequest,
    AgentRunEnqueueResponse,
    ApplicationCreate,
    ApplicationUpdate,
    PlatformJobAccepted,
    PlatformJobPublic,
    IntegrationCreate,
    IntegrationUpdate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    ConfigCreate,
    ConfigUpdate,
    CustomClaimCreate,
    CustomClaimUpdate,
    TableCreate,
    TableUpdate,
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
    SolutionDeployEnqueued,
    SolutionCandidateDeployEnqueued,
    SolutionDeployJobStatus,
    PolicyRuleCreate,
    PolicyRuleUpdate,
    ServicePolicyUpdate,
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
    WorkspacePromotionArtifactResponse,
    WorkspacePromotionCanaryRequest,
    WorkspacePromotionCanaryAccepted,
    WorkspaceReleasePrepareRequest,
    WorkspaceReleaseActivateRequest,
    WorkspaceReleaseStatusResponse,
    WorkspaceLiveStatusResponse,
    UserAccessMap,
    PermissionCatalogEntry,
    UserRoleAssignmentsResponse,
    AccessExplanation,
    AccessTrace,
    AuditLogListResponse,
    RunRetentionStatus,
    AuditRetentionStatus,
    IdentityCreate,
    IdentityPublic,
    RecommendedAccess,
]

#: Every request/response DTO the in-workflow SDK sends/parses against
#: ``/api/sdk/*`` lives in ``src.models.contracts.cli``. We pull them in
#: programmatically so a NEW SDK DTO is automatically fingerprinted — no manual
#: list to forget to update. A retype on any of these silently breaks running
#: workflows on a stale CLI, so they belong in the contract.
_SDK_DTOS: list[type] = [
    obj
    for _name, obj in inspect.getmembers(_cli_contracts, inspect.isclass)
    if issubclass(obj, BaseModel)
    and obj is not BaseModel
    and obj.__module__ == _cli_contracts.__name__
]

#: Request/response DTOs the CLI/SDK sends or parses. Type-aware via JSON Schema.
#: This is the real wire contract: the command DTOs (`bifrost <entity>` /
#: `workflows execute`) plus EVERY SDK DTO (pulled in programmatically, so new
#: ones are auto-covered). A field removed/renamed/retyped here is exactly what
#: silently corrupts a stale CLI, and it is caught completely and automatically.
CONTRACT_FINGERPRINT_MODELS: list[type] = _COMMAND_DTOS + _SDK_DTOS + [
    DocumentUpdate,
    ConditionalDocumentUpdate,
    # Table SDK calls use the REST batch endpoint, outside /api/sdk/*.
    DocumentBatchCreate,
    DocumentBatchCreateResponse,
    DocumentBatchUpsertResponse,
]

#: We deliberately do NOT fingerprint the full route list. Route strings are a
#: weak, noisy proxy: hand-listing ~100 `/api/*` paths is perpetually incomplete
#: (every omission is a false-negative), and a route rename produces a clean 404
#: — not the silent corruption a response-shape change causes, which the DTOs
#: above already catch. We keep only `/api/version` itself, since the gate's own
#: handshake depends on that literal path.
CLI_ROUTES: tuple[str, ...] = ("/api/version",)

#: Committed fingerprint of the contract surface above. If a code change flips
#: the live fingerprint, this test fails — update this value, and bump both
#: contract-version mirrors if the change breaks older CLIs. See module docstring.
EXPECTED_CONTRACT_FINGERPRINT = (
    # Canonical table batch modes/count-only responses require contract 12.
    # Older servers ignore write_mode and would insert instead of replace.
    # Upstream integration (2026-09-08): optional python_type/json_schema metadata
    # and AgentRun cursor are additive. Existing supported query spellings remain
    # accepted; malformed/unknown filters now fail explicitly. No CLI version bump.
    # Nullable parameters without defaults are required in runtime/AST inference;
    # WorkflowParameter required markers and client validation reflect that rule.
    # Additive conditional document update DTOs; dedicated route fails closed on old servers.
    # ApplicationCreate.app_model default flipped inline_v1 → standalone_v2
    # (2026-06-13). CONTRACT_VERSION bumped to 3: an old CLI would default a new
    # `apps create` to v1 against a v2-default server, so old clients are gated.
    #
    # SDKIntegrationsGetRequest gained optional `solution` (2026-06-14,
    # RequiredConnectionUnset escalation). ADDITIVE — an old CLI simply omits the
    # field and keeps silent-None behavior, so no CONTRACT_VERSION bump; fingerprint
    # refreshed only.
    #
    # Solution deploy now returns 202 + deploy_job_id and the CLI polls
    # SolutionDeployJobStatus for the prior summary shape (2026-06-17).
    # CONTRACT_VERSION bumped to 5.
    #
    # Solution deploy now uploads a workspace zip as multipart/form-data instead
    # of the legacy JSON bundle request body (2026-06-21).
    # CONTRACT_VERSION bumped to 6.
    #
    # TablePolicies policies union widened to list[Policy | PolicyRuleRef] (2026-06-23).
    # ADDITIVE: old CLIs send plain inline rules; PolicyRuleRef is a new optional
    # variant — no old client is broken. Fingerprint refreshed only.
    #
    # PolicyRuleCreate + PolicyRuleUpdate added to CLI contract surface (2026-06-23).
    # ADDITIVE: new entity group (policy-rule), no existing DTOs changed.
    # Fingerprint refreshed only.
    #
    # Solution install (zip + from-repo) is now async: POST /install and
    # /install/from-repo return 202 + deploy_job_id (was 200/201 + Solution) and
    # the CLI/UI poll SolutionDeployJobStatus for the solution_id. That job status'
    # install_id widened to nullable (a zip install resolves its target inside the
    # job) — a response-shape change the CLI parses (2026-07-02).
    # CONTRACT_VERSION bumped to 7.
    #
    # Application publish now returns 202 + a standardized platform job instead
    # of ApplicationPublic, and the CLI polls PlatformJobPublic for durable
    # progress/result/error (2026-07-28). CONTRACT_VERSION bumped to 8.
    #
    # Forms gained optional confirmation_markdown (2026-08-04). ADDITIVE: old
    # clients omit it and continue receiving the prior default confirmation.
    #
    # RoleCreate/RoleUpdate gained additive scopes fields (2026-07-30).
    # Old CLIs omit them and retain empty-scope custom-role behavior.
    # Forms also gained optional confirmation_markdown (2026-08-04). All are
    # additive; fingerprint refreshed after merging the contract surfaces.
    #
    # CLI/SDK file write and delete requests gained optional guarded-mutation
    # fields (2026-08-08). ADDITIVE: old clients omit them and retain the prior
    # unconditional behavior; new clients can reject stale writes and deletes.
    #
    # PlatformJobStatus gained `waiting` for durable parent jobs (2026-08-07).
    # CONTRACT_VERSION bumped to 9 because an older CLI cannot parse the new
    # enum value while polling PlatformJobPublic.
    # Fingerprint refreshed against the combined MTG fork and upstream v9
    # contract surfaces; the additional fork fields were already shipped.
    #
    # Solution deploy enqueue now requires candidate_id (2026-08-12), binding
    # an accepted asynchronous job to the exact reviewed bundle. CONTRACT_VERSION
    # bumped to 10 because a stale CLI cannot verify that invariant.
    #
    # Workspace promotion preview moved to immutable artifact v2 (2026-08-19):
    # reviewed production source is named by protected Git commit/tree, closure
    # bytes are server-fetched, and the response binds content, candidate,
    # release, effective file, and registration identities. CONTRACT_VERSION
    # bumped to 11 because v1 clients cannot safely interpret or activate v2.
    # The same unreleased v11 contract includes reviewed-artifact-only canary
    # request/acceptance DTOs, immutable preparation proof, and tagged
    # canary/risk-acknowledgement activation authorization; local draft uploads
    # are not executable.
    # Workspace release history status gained optional deadline, overdue, and
    # runtime/history verification fields (2026-08-24). ADDITIVE: v11 clients
    # ignore them and retain their existing pending/locked state handling.
    # Agent create/update replaced llm_model with llm_profile_id and the SDK AI
    # surface adopted reusable model profiles (2026-08-24). BREAKING: the
    # compatible CLI ships behind the unreleased 1.2.3 minimum-version floor.
    # Event subscriptions replaced the inert filter_expression string with the
    # structured criteria v1 contract (2026-08-26). BREAKING: the matching CLI
    # implementation ships in that same unreleased 1.2.3-gated release.
    # Additive attachment, artifact, model-profile, logo, usage-cache, and
    # platform-job memory fields are included in the same fingerprint refresh.
    # Reviewed promotion gained an optional declared multi-root cohort and
    # additive source-release evidence (2026-08-24). Single-root v11 clients
    # retain their existing request and response behavior.
    # Promotion responses gained optional risk_paths (2026-08-29). ADDITIVE:
    # old clients ignore it and retain their existing risk-class handling.
    # Promotion responses gained optional diagnostic delta/decision evidence
    # (2026-08-31). ADDITIVE: old clients ignore the extra response fields.
    # Workflow updates gained an optional execution retry policy (2026-09-03).
    # ADDITIVE: old clients omit it and workflows remain non-retryable by default.
    # Redesign integration adds optional logo/description and role metadata.
    # Existing CLI request and response fields remain compatible.
    # Workspace Live release retirement (2026-09-18). ADDITIVE: release and live
    # status responses gained optional retired_at/retirement_reason, the live
    # status gained an optional state enum, and the new retire request/response
    # DTOs are not CLI-fingerprinted. Existing v11 clients ignore the extra
    # fields and retain their prior pending/locked/not_queued handling.
    # Solution access flags and caller identity gained additive, dual-name
    # compatible fields (2026-09-16). Existing v11 clients retain their scope.
    # PlatformJobPublic gained execution_backend (2026-09-17). ADDITIVE:
    # existing clients ignore the placement detail.
    # ServicePolicyUpdate is a new CLI contract for supervised services.
    # PlatformJobStatus.requires_action breaks older CLI job pollers; contract 13.
    # LLM configuration provider enums gained `google` (2026-08-14). ADDITIVE:
    # old clients can keep selecting OpenAI-compatible or Anthropic providers.
    #
    # SDK AI completion requests gained optional file inputs (2026-08-15).
    # ADDITIVE: old SDK clients omit the field and keep text-only behavior.
    #
    # PlatformJobPublic gained optional memory_required_bytes (2026-08-23).
    # ADDITIVE: old clients ignore the scheduler admission detail.
    #
    # Agent create/update replaced the raw llm_model field with llm_profile_id
    # (2026-08-22). BREAKING: older CLIs cannot express reusable model profiles,
    # and SDK AI model info no longer reports the removed profile max_tokens
    # value. Both ship behind the same unreleased 1.2.3 minimum CLI boundary.
    #
    # SDK AI completion requests gained optional `profile` (2026-08-23).
    # ADDITIVE: older SDKs omit it and continue using the Primary assignment.
    #
    # EventSubscriptionCreate's filter_expression description now documents
    # its enforced JMESPath-compatible behavior (2026-08-26). COSMETIC: the
    # field shape is unchanged; fingerprint refreshed only.
    #
    # WorkflowParameter gained optional python_type and json_schema fields
    # (2026-09-04). ADDITIVE: older clients ignore the richer tool-contract
    # metadata, so the fingerprint is refreshed without raising MIN_CLI_VERSION.
    #
    # IntegrationCreate/IntegrationUpdate gained optional description
    # (2026-09-09). ADDITIVE: older clients omit it and keep existing behavior.
    #
    # Solution access flags renamed with dual-name compat (2026-09-16, SPIKE).
    # allow_outbound_access is canonical; global_repo_access is still accepted
    # on input and emitted (deprecated) on output. allow_inbound_access added
    # (default true). WorkflowExecutionRequest/EmitEventRequest gained optional
    # caller_solution(_id). ADDITIVE: old clients use the old key / omit the
    # new fields and keep prior behavior; fingerprint refreshed only.
    #
    # PlatformJobPublic gained execution_backend (2026-09-17). ADDITIVE: old
    # clients ignore the placement detail and keep polling status as before.
    #
    # MTG preserves its local runner and session_id contracts. requires_action
    # already raised the fork contract to v13. Upstream's unpublished 1.4.2
    # minimum is not a compatible replacement for the fork's contract gate.
    #
    # TableUpdate gained optional organization_id (2026-09-27, RBAC R1b batch
    # 4): rescoping a table is now supported via PATCH. ADDITIVE: old CLIs
    # simply never send it and retain the prior no-rescope behavior.
    # Fingerprint refreshed only.
    #
    # RBAC bridge merge (2026-10-18): RoleCreate/RoleUpdate/RolePublic remove
    # legacy permissions and adopt base roles / typed role_permissions.
    # BREAKING: both fork contract versions advance to v14; v13 clients must
    # upgrade. The legacy minimum stays frozen at the published bridge build.
    # The new Platform Admin-only bridge editor's request and response are
    # explicitly fingerprinted: its manual CLI flags are not auto-discovered.
    # The endpoints are additive, within the same v14 RBAC contract decision.
    #
    # WebhookSourceConfig.config description (2026-10-01): documents that a
    # "secret" key is write-only (stored encrypted, never returned; omit to
    # keep, null/"" to clear). COSMETIC: the schema shape is unchanged and old
    # CLIs send the same body. Fingerprint refreshed only.
    #
    # Access and retention response shapes newly fingerprinted (2026-10-06):
    # `bifrost users access/roles`, `permissions catalog`, `audit list/explain`
    # and `retention` parse UserAccessMap, PermissionCatalogEntry,
    # UserRoleAssignmentsResponse, AccessExplanation/AccessTrace,
    # AuditLogListResponse, RunRetentionStatus and AuditRetentionStatus. The
    # commands ship with these DTOs, so no released CLI parses an older shape
    # (greenfield coverage). Fingerprint refreshed only.
    #
    # PermissionCatalogEntry gained `names` and Title Case `area` values
    # (2026-10-06, Graph-style permission display names). Same unreleased
    # commands as above, so no released CLI parses the older shape.
    # Fingerprint refreshed only.
    #
    # Identity shapes newly fingerprinted (2026-10-06): `bifrost users create
    # --identity` sends IdentityCreate and parses IdentityPublic, and
    # `bifrost workflows requirements/grant` parse WorkflowRequirements. The
    # commands ship with these DTOs (greenfield coverage). Fingerprint
    # refreshed only.
    #
    # WorkflowRequirement gained `organization_id` (2026-10-06): the
    # organization a reach item targets or a policy role's grant is placed at.
    # ADDITIVE: the same unreleased commands, and the CLI reads items by key.
    # Fingerprint refreshed only.
    #
    # Requirements became Recommended Access (2026-10-06): WorkflowRequirements
    # is now RecommendedAccess (items RecommendedAccessItem, grant
    # RecommendedGrant), served at /recommended-access and parsed by `bifrost
    # workflows recommendations/grant`. Same shape, renamed; the route and the
    # commands are unreleased. Fingerprint refreshed only.
    #
    # AccessExplanation.then became nullable and NowUnavailable gained
    # `inputs_not_stored` (2026-10-07): older and worker-written access checks
    # stored no trace, or no inputs either. `bifrost audit explain` is
    # unreleased and ships handling both. Fingerprint refreshed only.
    #
    # AuditLogEntry gained `workflow_name`, AuditLogActor `home_organization_id`
    # and `home_organization_name`, and RoleUserSummary `identity_kind`
    # (2026-10-07): optional additive fields the CLI ignores. Fingerprint
    # refreshed only.
    #
    # NowUnavailable gained `run_as_user_missing` (2026-10-09): a stored
    # `run_as` check whose user no longer exists. `bifrost audit explain` is
    # still unreleased and ships handling it. Fingerprint refreshed only.
    #
    # WorkflowExecutionRequest.run_as description now names Impersonate Users
    # (2026-10-09). COSMETIC: description text only. Fingerprint refreshed only.
    #
    # Agent run launch shapes newly fingerprinted, with optional `run_as`
    # (2026-10-10): the SDK sends AgentRunEnqueueRequest and parses
    # AgentRunEnqueueResponse (which echoes `run_as_user_id`), and
    # AgentRunCreateRequest is the /execute body. ADDITIVE: old clients omit
    # `run_as` and ignore the echo. Fingerprint refreshed only.
    "8af658bc3908620526f16736393dff529c5d12e4cf36a5a113303809a178395e"
)


def _fingerprint(models: list[type], routes: tuple[str, ...]) -> str:
    """Deterministic sha256 over model JSON schemas + the route list."""
    h = hashlib.sha256()
    for model in sorted(models, key=lambda m: m.__name__):
        schema = model.model_json_schema()
        h.update(model.__name__.encode())
        h.update(json.dumps(schema, sort_keys=True).encode())
    h.update(json.dumps(sorted(routes)).encode())
    return h.hexdigest()


def test_cli_and_server_contract_version_agree() -> None:
    """The server and standalone CLI enforce the same breaking-change gate."""
    assert CLI_CONTRACT_VERSION == SERVER_CONTRACT_VERSION, (
        f"CONTRACT_VERSION drift: CLI={CLI_CONTRACT_VERSION} "
        f"(api/bifrost/contract_version.py) vs "
        f"server={SERVER_CONTRACT_VERSION} (api/shared/contract_version.py). "
        "The runtime compatibility gate must remain synchronized."
    )


def test_contract_fingerprint_tripwire() -> None:
    """A change to the CLI-consumed contract surface forces a decision."""
    current = _fingerprint(CONTRACT_FINGERPRINT_MODELS, CLI_ROUTES)
    assert current == EXPECTED_CONTRACT_FINGERPRINT, (
        "A CLI-consumed contract (DTO schema) changed.\n"
        f"  current fingerprint: {current}\n"
        "  - CLI-IMPACTING/BREAKING change (field removed/renamed/retyped, "
        "response shape the CLI parses changed): bump CONTRACT_VERSION in "
        "api/shared/contract_version.py and api/bifrost/contract_version.py, "
        "then update EXPECTED_CONTRACT_FINGERPRINT below.\n"
        "  - COSMETIC/ADDITIVE (description tweak, new optional field the CLI "
        "ignores): just update EXPECTED_CONTRACT_FINGERPRINT; leave "
        "CONTRACT_VERSION unchanged.\n"
        "See test_contract_version.py module docstring."
    )
