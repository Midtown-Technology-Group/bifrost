# Single-server MCP manifest contract — v1 design

Status: **Thomas accepted the three design choices below on 2026-10-15;
maintainer review remains pending**. This document defines the intended contract,
not an implemented API.
Scope: [Bifrost #674](https://github.com/Midtown-Technology-Group/bifrost/issues/674),
Multica `01a0e069-904b-7c2b-b84a-7850b519a9af` (MIDT-172).

## Accepted design choices

Thomas selected `accept-recommended` with no additional conditions in hosted
inbox answer `inbox-answer:c0d2c78b-9ff0-46b8-a260-ddad45801e62`, persisted
revision `ledger-18bed29651b792bd36c9cf6b`, for the existing decision packet
`iris-midt172-contract-v1-be1aefc-decisions-20261014-r1`. The authoritative answer
was delivered to this owner thread on 2026-10-15. It accepts all three choices:

1. **Create-only v1.** Return a conflict for an existing server name; do not
   replace, attach, rename automatically, or upsert. This supersedes the optional
   replace path in the October 6 proposal and avoids changing templates already
   serving other organizations.
2. **Inactive imports and gated policy.** Validate required mappings and
   credentials before writing anything. Create the imported server inactive;
   keep connection visibility flags false until setup and tool discovery are
   complete and an administrator explicitly applies the requested policy.
   Activation remains a separate reviewed action.
3. **Sanitized inline OAuth configuration in template exports.** Export only
   the allowlisted configuration below, allowing destination provider creation
   or explicit mapping with destination validation, without carrying source
   provider IDs or credentials.

Acceptance settles these design choices only. It does not authorize implementation,
merge, deployment, existing-server overwrite, credential export, or an access
grant. Implementation issues MIDT-168 through MIDT-171 may reference the accepted
choices but require their own applicable authorization and repository gates.
No runtime, API, credential, or export behavior is changed by this document.

### Alternatives considered and tradeoffs

- Create-only keeps existing multi-org templates intact and avoids replace
  rollback/audit complexity. Explicit replacement is the alternative; v1 would
  need target selection, preservation of unrelated bindings, and separate review.
  Create-only means repeated imports conflict instead of updating in place.
- Inactive imports and deferred policy avoid granting execution rights from a
  file. The alternative is immediate policy/activation after setup; that removes
  an administrator step but risks importing unintended shared-token permissions.
  Rejecting incomplete connection setup preserves today's credential-required
  model; credential-free drafts would require a separately designed lifecycle.
- Inline OAuth configuration makes the template reusable without source foreign
  keys. Requiring an existing destination provider is the alternative; it reduces
  exported configuration but requires manual setup on every destination. Endpoints,
  scopes, audience, public client IDs, and regional overrides can still identify
  an environment: allowlisting excludes credentials, not all sensitive context.
  Template-only default and explicit connection opt-in limit that exposure.

## Document and scopes

Use one UTF-8 YAML document, media type
`application/vnd.bifrost.mcp-server+yaml`, suffix `.mcp-server.yaml`.
Required root fields are `kind: MCPServer`, integer `version: 1`, `scope`, and
`server`. This is independent of `.bifrost/mcp-servers.yaml`: no workspace
wrapper, implicit conversion, or reuse of its ORM codec.

`scope: template` is the default export. It includes only `server.name`,
`server.server_url`, `server.organization_scope`, and `server.oauth`.
`connections` must be absent. `organization_scope` is `global` or
`target-organization`; the latter requires a destination organization selected
outside the document. Global scope is explicit and requires platform-admin rights.

`server.oauth` is null for an unauthenticated server, otherwise an allowlisted
block with `flow`, `authorization_url`, `token_url`, `scopes`, and `audience`.
`flow` is `authorization_code` or `client_credentials`; `token_url` is required,
and `authorization_url` is required for authorization-code flow. Match existing
DTO field limits; the document's `flow` maps to DTO `oauth_flow_type`.

`scope: template-with-connections` is explicit opt-in. It requires a
`connections` array; each entry has a unique nonempty document-local `ref`,
`client_id`, optional `server_url_override`, and `policy`. Each ref requires an
explicit authorized destination organization mapping outside the document.
Reject mappings that would create multiple connections for the same server/org.
`policy` holds `available_in_chat`, `available_to_autonomous`, and `tools`;
each tool entry has a unique nonempty `name` and boolean `enabled`.
Omitted visibility flags default false and omitted tools default to an empty list.

Example extended document (a template document omits `connections`):

```yaml
kind: MCPServer
version: 1
scope: template-with-connections
server:
  name: example
  server_url: https://example.com/mcp
  organization_scope: global
  oauth:
    flow: authorization_code
    authorization_url: https://example.com/authorize
    token_url: https://example.com/token
    scopes: []
    audience: null
connections:
  - ref: connection-1
    client_id: public-client-id
    server_url_override: null
    policy:
      available_in_chat: false
      available_to_autonomous: false
      tools:
        - name: read_example
          enabled: false
```

## Portability and credential boundary

- **Omitted identities:** source server, connection, tool, organization, provider,
  user, and token UUIDs. Generate destination entity IDs; document refs only
  correlate connection mappings, never identify database rows. Source UUID
  fields are invalid, even when they happen to match a destination entity.
- **Remapped bindings:** organization scope and connection refs resolve through
  explicit import selections. OAuth configuration creates a local provider or
  maps to an explicitly selected compatible local provider; it never silently
  updates an existing provider. Mapping cannot bypass tenant visibility rules.
- **Portable content:** server name and endpoint, and allowlisted OAuth
  configuration. Export by constructing the allowlist, not dumping public DTOs.
  Credential-bearing URLs must fail export/validation with a safe field error;
  they must not be copied or echoed. Validate endpoint configuration under the
  platform's existing network/security rules before any discovery request.
- **Preserved connection configuration:** client IDs and regional
  URL overrides occur only in the extended scope. Client IDs are public
  identifiers but environment/vendor specific; show this caveat during preview
  and allow an explicit destination override outside the document.
- **Refreshed destination data:** discovery metadata and tool catalogs/schemas
  are omitted. Refresh locally after setup. Recompute the deployment callback
  URL; never export the source `redirect_url`. Omit timestamps and free-text
  disabled reasons.
- **Preserved requested policy:** visibility flags and tool enabled values are
  intent, not immediate authorization. Match policy by exact observed tool name
  after `tools/list`; unobserved names remain unresolved and cannot authorize
  tools. Newly discovered tools have no implied permission from this document.
- **Omitted activation:** source `is_active` is excluded. Imported servers start
  inactive regardless of source state.
- **Forbidden credential material:** client secrets (plaintext or encrypted),
  access/refresh/service tokens and token IDs, per-user credential rows, consent
  data, provider credentials/placeholders/status, and encrypted blobs. Reject
  supplied fields rather than dropping them. Never put credentials in the file,
  preview response, audit payload, or error message.

## Import lifecycle and collisions

Export/import require platform-admin authorization, matching current server
mutation authority. Validate/preview is read-only: parse, validate, check scope,
resolve selected mappings, and report conflicts and missing setup. Secret values
are supplied only through the authenticated destination commit request, outside
the manifest, and encrypted using existing storage mechanisms.

Commit revalidates permissions, mappings, provider compatibility, and name
uniqueness in one database transaction. Missing required mappings/client setup
returns `incomplete_setup` and writes nothing. No placeholder secret or
credential-free connection draft is created. Template-only import needs no
connection credentials because it creates no connections. Extended import must
meet the destination connection model's required client ID and nonempty secret.

Generate fresh destination UUIDs. An existing name returns `409 name_conflict`,
including on repeated import; callers must deliberately choose a distinct name
before resubmitting. Use the database's existing name uniqueness semantics;
handle a concurrent name collision as the same safe conflict. There is no target
UUID/replace option in v1. Rejected source UUID fields are schema errors, not
collision keys. Preserve all existing servers, connections, and policy.

Successful commit creates an inactive server and any fully configured connections
with visibility flags false. Requested policy must remain reviewable without
being applied automatically. Discovery and OAuth network calls are separate from
the atomic database creation step; their failures leave the imported server
inactive. Before applying policy/activation, refresh tools and resolve unknown
names, credentials, and authentication failures. Implementation must specify how
pending policy is retained and presented; this document adds no parallel job system.

## Validation, compatibility, and UI/API errors

Reject empty/multiple-document YAML, malformed UTF-8, duplicate keys, unsafe YAML
tags, invalid root kind/scope, missing/invalid fields, credential fields, and
unknown fields at every level. Do not coerce a string version into an integer.
Use safe parsing with bounded input and expansion; the implementation must
declare the upload size limit and enforce it before parsing.

Reject unsupported versions with `unsupported_version`; never guess, downgrade,
or parse a workspace manifest as v1. Later format versions must retain explicit
v1 support or publish a deliberate compatibility migration. This new opt-in
format does not alter the existing workspace manifest contract. Implementation
must separately make the repository's CLI/API contract-version decision.

Responses include a stable `code`, JSON-pointer `path`, and safe message, without
echoing submitted values. Multiple field errors/unresolved items may be returned.
Use `400 invalid_document` for unreadable YAML/encoding, `415 unsupported_media_type`,
`413 document_too_large`, `422 invalid_manifest` for schema violations,
`422 unsupported_version`, `422 incomplete_setup`, `409 name_conflict`, and
`403 forbidden` for insufficient authority. Preserve existing unauthenticated
and tenant resource-concealment semantics. Commit succeeds with `201` and fresh
destination IDs plus inactive/setup state, never credential values.

The UI shows field errors, destination mappings, missing credentials, requested
policy, and name conflicts before enabling commit. An invalid or incomplete
submission creates no partial resources. Do not offer replace in v1. After
creation, show inactive status and the remaining discovery/policy/activation step;
failed setup must not be presented as an active usable connection.

## Evidence and implementation verification

Read-only revalidation on 2026-10-12, host `codex-remote-02`, worktree
`/home/thomas/.t3/worktrees/bifrost-platform/handoff-multica-mcp-contract-20261012`,
branch `handoff/multica-mcp-contract-20261012`. Source reviewed and live
`origin/main` resolved to `08255ad355a3ab63f0c04a214c3c230432552dfe`.
Loaded engineering-flow `2026-10-06.1` from skill package `2026-10-09.1`.

Historical Multica CLI readback on 2026-10-12: issue revision 14, in progress,
last decision request 2026-10-06 13:11 UTC; no later approval, active run, or linked PR
at that time. Original
proposal comment `01a11154-173a-7717-aaa5-a53396871707`; final recommendation
`01a11156-d808-7616-a123-c7906fb1f81d`. GitHub #674 is open with no comments.
No Multica record changed. This readback is evidence at the recorded date, not a
future status guarantee.

Relevant current source:

- [External MCP DTOs](../../../api/src/models/contracts/external_mcp.py): inline
  provider configuration is already supported; public responses include fields
  unsuitable for this portable format.
- [Server ORM](../../../api/src/models/orm/external_mcp.py): unique server name,
  generated IDs, organization scope, and `is_active`.
- [Server router](../../../api/src/routers/mcp_servers.py): platform-admin create,
  inline provider creation, per-connection credential placeholders on providers.
- [Connection router](../../../api/src/routers/mcp_connections.py): required
  nonempty plaintext secret at API create, encrypted at rest; no draft API.
- [Workspace manifest codec](../../../api/bifrost/manifest.py): includes source
  IDs, cached schemas/discovery, and service token FKs; cannot be reused unchanged.

No runtime behavior changed or runtime proof claimed. Thomas's three design
choices are accepted as recorded above; maintainer review remains outstanding.
Recording acceptance does not claim that this PR is merged or MIDT-172 is closed.
A live stack, type regeneration, and runtime tests are inapplicable to this diff;
the publishing candidate must still pass the repository's `./test.sh pre-pr` gate.
Any separately authorized implementation must prove both export scopes, recursive
credential exclusion, malformed/unknown/version rejection, no-write incomplete
setup and collisions (including races), explicit tenant/provider mappings,
inactive imports, and observed-tool policy gating. Follow live worktree API/UI
proof, scoped Docker tests, type generation, parity/version checks, and pre-PR/CI
gates from repository guidance before delivery.
