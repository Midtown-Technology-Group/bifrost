# Retained knowledge sharing and private-agent promotion

The MTG RBAC sync retains `knowledge_namespace_roles` with its existing IDs,
rows, foreign keys, global (`organization_id IS NULL`) assignments and
`NULLS NOT DISTINCT` uniqueness. Namespace CRUD, role consumer counts and the
Platform Admin role-detail Knowledge tab continue to use that junction.
No namespace row is converted into a `knowledge.read` permission.

The agent namespace selector reloads current role assignments and boundaries
from the database. It includes the caller's custom or builtin base role at
their non-null home organization, plus additional roles effective at that
home organization. An explicit home-organization boundary qualifies; a managed
organizations boundary qualifies only for a customer home organization. A
foreign-only, absent or platform boundary does not qualify an ordinary caller.
The namespace assignment must independently be home-scoped or global. The
stored Platform Admin wildcard retains the existing cross-scope exception.
An ordinary null-home caller has no selector reach. Sharing roles with zero
permission rows remain valid; an External caller's explicit sharing roles
remain usable by the selector.

This selector is selection/sharing metadata, not a universal content-read ACL.
Existing direct-content, SDK, engine and agent-mediated knowledge policies
remain responsible for content access. In particular, the existing direct
knowledge API denies External users; this change does not add a namespace ACL
to every content read or grant any knowledge permission implicitly.

Private-agent promotion reloads current authority and requires
`agents.promote` at the agent's organization through the shared bridge policy.
An ordinary caller must own the private agent, have a non-null current home
organization equal to the agent's organization, and hold the explicit bridge
grant at that home organization. `agents.readwrite`, `agents.readwrite.all`,
foreign or managed-organization grants and stale token admin flags do not
satisfy that requirement. Platform Admin wildcard holders bypass owner,
home-organization and explicit-grant checks. The existing Solution-managed
entity guard applies to every caller, including Platform Admins. Promotion
does not introduce a Solution mutation bypass.
