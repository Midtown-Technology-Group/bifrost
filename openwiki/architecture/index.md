# Files

- [CLI, Python SDK, and MCP Surfaces](cli-and-mcp.md) - Explains the installed Bifrost CLI and Python API client, their compatibility and request boundaries, and how CLI and selected MCP mutation tools stay aligned with server DTOs.
- [Data Model, API Contracts, and Schema Evolution](data-and-contracts.md) - How Bifrost separates request/response contracts from persistent models, scopes data access, evolves PostgreSQL schema, and keeps browser types aligned with the API.
- [Identity, Tenant Isolation, and Authorization](identity-and-authorization.md) - How Bifrost validates typed credentials, builds principals, resolves organization scope, and applies role-aware access to platform resources.
- [Platform Architecture and Runtime Boundaries](platform.md) - The main Bifrost service boundaries, request and startup wiring, browser client composition, and the source paths that connect them.
- [Language-Neutral Runtime Contract and Conformance](runtime-protocol-conformance.md) - Describes the proposed static runtime document schemas and offline checks, and separates that evidence from negotiated protocol, runtime implementation, authorization, and behavioral conformance.
