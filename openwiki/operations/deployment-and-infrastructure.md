---
type: operations-architecture
title: Deployment and Infrastructure Topology
description: Explains Bifrost's Compose and Kubernetes service topologies, dependency ordering, storage configuration, and deployment ownership boundaries.
tags: [deployment, infrastructure, docker, kubernetes, operations]
sources:
  - id: openwiki-source-164e2da859b5277df81c7d94
    resource: repo://.github/workflows/ci.yml
  - id: openwiki-source-c3e6ae5d4315785a63bd2231
    resource: repo://docker-compose.debug.yml
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-82b568a56fde27324ed7f028
    resource: repo://docs/dev/agent-platform-rules.md
  - id: openwiki-source-86f244ea2e22dcba998209ef
    resource: repo://k8s/local/build-jobs.yaml
  - id: openwiki-source-73d414649903befd981485fc
    resource: repo://k8s/local/keda.yaml
  - id: openwiki-source-a9f75f8713f57dc30266397c
    resource: repo://k8s/local/README.md
  - id: openwiki-source-abe04d56b39d4dffc90d2db5
    resource: repo://k8s/README.md
  - id: openwiki-source-ae3e812414fd7902fe28055f
    resource: repo://scripts/kubernetes/local-kind.sh
generated: { by: "bifrost-atlas", at: "2026-10-18T02:34:56.337Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Deployment and Infrastructure Topology

Bifrost separates request handling, browser delivery, queued execution, scheduling, persistence, and object storage into cooperating services. The repository provides a Compose stack, Kubernetes manifests, and a separate local Kind harness; these are distinct deployment surfaces with different ownership and exposure assumptions. See [development and verification](development-and-verification.md), [release process](release-process.md), and [devices and workers](../integrations/devices-and-workers.md).

## Compose service graph

The root `docker-compose.yml` is described as the production-oriented Compose stack, although its defaults include local SeaweedFS for S3-compatible object storage. PostgreSQL with pgvector is the database; PgBouncer provides transaction-mode connection pooling to application services. RabbitMQ transports queued work, Redis provides cache/session services, and SeaweedFS persists object data in a named volume. These supporting services have health checks, and their state volumes are separate from the containers (`docker-compose.yml`).

An `init` service runs database migrations and module-cache warming after PgBouncer and Redis become healthy. The API waits for successful init completion and healthy database pool, broker, and cache before serving. The client container serves the browser application and proxies HTTP and WebSocket traffic to the API; the API port is not published directly in this stack. The scheduler waits for the API and its backing services, hosts scheduler triggers and a small platform-job pool, and uses a PostgreSQL lease to elect the trigger leader. Workflow and agent execution remains in worker containers, which consume RabbitMQ and can be horizontally scaled (`docker-compose.yml`).

Workers and scheduler also receive database, RabbitMQ, Redis, API URL, and object-storage configuration. In this layout workers can fetch workspace content from shared object storage, so replicas do not depend on a shared local filesystem. The default service image references are release images; build directives are commented examples. Environment values control replica counts and service configuration, while required secrets must be supplied by the operator; this page intentionally does not reproduce secret values or credential commands (`docker-compose.yml`).

## Kubernetes deployment surface

`k8s/` contains production-oriented Kubernetes manifests for namespace/configuration, API, client, workers, scheduler, and RabbitMQ. The documented prerequisites include managed PostgreSQL and object storage. The API, client, worker, scheduler, and broker are represented as separate workloads/services; the manifests do not define a dedicated coding-agent deployment. Interactive agent requests run in the API deployment, while queued autonomous runs are handled by workers (`k8s/README.md`).

Object storage supports S3-compatible providers and the native Azure Blob provider. Azure Blob does not use S3 endpoint settings. The generic manifests do not configure Azure Workload Identity, so selecting default-credential authentication alone does not establish the required identity for API, init, and worker workloads. The repository's documentation recommends applying the set with Kustomize, while leaving ingress and public exposure to the operator (`k8s/README.md`).


## Development Compose isolation

`docker-compose.debug.yml`, invoked through `debug.sh`, is a per-worktree development environment rather than the root stack. The project name and persistent volumes are derived per worktree to allow parallel isolated stacks. Profiles choose between a host-published client port and mesh access without host ports; development images and mounts provide hot reload. The repository's debug lifecycle is the supported way to manage that environment (`docker-compose.debug.yml`).

## Local Kubernetes spike

`k8s/local/` is a separate Kind harness, not an overlay that proves the production-oriented `k8s/` manifests are live. The wrapper scopes its cluster name, state directory, kubeconfig, rendered manifests, and `kubectl` context to a hash of the checkout path; operations use the generated kubeconfig and explicit `kind-<cluster>` context rather than the user's current context ([local script scope](../../scripts/kubernetes/local-kind.sh#L5-L16), [explicit kubectl context](../../scripts/kubernetes/local-kind.sh#L35-L37), [local harness contract](../../k8s/local/README.md#L1-L25)). Generated credentials are local to that context and are not checked into manifests; startup preserves a complete existing local secret and refuses to rotate a partial one ([secret handling](../../k8s/local/README.md#L27-L37)).

`up` creates or reuses the Kind cluster, builds and loads the API image unless explicitly told to load an existing image, installs KEDA, applies local stateful dependencies, waits for them, runs the init job, then applies API, scheduler, worker, and queue-based scaling resources. `status`, `collect`, and `down` report the selected cluster, collect resource/log diagnostics, and delete that Kind cluster and kubeconfig; they are evidence about the local harness only ([startup sequence](../../scripts/kubernetes/local-kind.sh#L166-L185), [dependency and workload application](../../scripts/kubernetes/local-kind.sh#L206-L245), [diagnostic collection and teardown](../../scripts/kubernetes/local-kind.sh#L266-L309)).

Kubernetes App build Jobs are an opt-in local experiment via `BIFROST_KIND_BUILD_JOBS=1`. That applies a separate manifest with a scoped controller Role and a runner ServiceAccount; API and worker pods retain disabled service-account token automount while only the scheduler receives controller access. Worker scale-in is deliberately disabled in the local KEDA ScaledObject until queue-drain behavior has been exercised; KEDA owns replica count while that object exists ([build-job opt-in](../../scripts/kubernetes/local-kind.sh#L158-L164), [local build RBAC and configuration](../../k8s/local/build-jobs.yaml#L1-L80), [KEDA scale-in settings](../../k8s/local/keda.yaml#L12-L40), [operator ownership guidance](../../k8s/local/README.md#L50-L68)). See [Kubernetes and local cluster](kubernetes-and-local-cluster.md) for the harness lifecycle and its experiment boundaries.

## Repository deployment boundary

The CI workflow retains an explicit upstream-only guard on its DigitalOcean development rollout: it runs only for pushes to `main` in `gobifrost/bifrost`. The platform fork's repository guidance assigns MTG deployment to `bifrost-infra` and Azure, and says to preserve that upstream guard. This checkout therefore documents deployment configuration and ownership boundaries; it does not prove a current deployment or live service health (`.github/workflows/ci.yml`, `docs/dev/agent-platform-rules.md`).

## Operational evidence limits

Manifests, Compose configuration, and workflow definitions show intended topology and repository policy. They do not establish which image or configuration is currently deployed, cluster health, operator-managed ingress, external identity setup, or cloud resource state. Verify those facts through the authorized deployment environment when an operational task requires them.
