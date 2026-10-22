# Files

- [CI Selection, Quality Gates, and Release Evidence](ci-and-release-evidence.md) - Explains affected-test planning, the CI acceptance graph, staged Sonar evidence, enforcement-chain ownership, and release-package checks without conflating plans, test execution, or deployment.
- [Deployment and Infrastructure Topology](deployment-and-infrastructure.md) - Explains Bifrost's Compose and Kubernetes service topologies, dependency ordering, storage configuration, and deployment ownership boundaries.
- [Development, Test, and Verification Lanes](development-and-verification.md) - Describes isolated development and test stacks, supported test entry points, contract generation, and verification evidence limits.
- [Extension Points, Plugins, and Generated Documentation](extension-points.md) - Maps the operation catalog, plugin skill distribution, SDK generation template, and screenshot documentation pipeline.
- [Kubernetes Manifests and Isolated Local Kind](kubernetes-and-local-cluster.md) - Maps the repository's Kubernetes base, application-build overlay, and worktree-scoped Kind harness, including credentials, RBAC, autoscaling, and evidence boundaries.
- [Observability, Diagnostics, and Operational Controls](observability-and-operations.md) - Documents health probes, optional OpenTelemetry export, scheduler diagnostics, metrics aggregation, and safe log handling.
- [Versioning, Packaging, and Release Process](release-process.md) - Explains MTG fork version calculation, reviewed release preparation, immutable package publication, and the separate deployment authority.
- [Security, Audit, and Sensitive Data Boundaries](security-and-audit.md) - Summarizes request limits, secret sanitization, privileged audit access, and repository vulnerability handling policy.
