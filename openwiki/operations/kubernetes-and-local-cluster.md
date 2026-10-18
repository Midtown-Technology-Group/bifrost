---
type: operations-guide
title: Kubernetes Manifests and Isolated Local Kind
description: Maps the repository's Kubernetes base, application-build overlay, and worktree-scoped Kind harness, including credentials, RBAC, autoscaling, and evidence boundaries.
tags: [kubernetes, kind, deployment, build-jobs, operations]
sources:
  - id: openwiki-source-76b7996cc1e4c01b8a590987
    resource: repo://deploy/kubernetes/builds/build-config.yaml
  - id: openwiki-source-7e664aded5bd8161f153ae30
    resource: repo://deploy/kubernetes/builds/kustomization.yaml
  - id: openwiki-source-9684ac63703b1726a0eb112a
    resource: repo://deploy/kubernetes/builds/rbac.yaml
  - id: openwiki-source-b3c615761753b2512e9f4f29
    resource: repo://deploy/kubernetes/builds/scheduler-controller-patch.yaml
  - id: openwiki-source-555b32ee785edf2f4513a9fd
    resource: repo://deploy/kubernetes/builds/serviceaccounts.yaml
  - id: openwiki-source-6ea291085813aa0a58dea645
    resource: repo://k8s/api/deployment.yaml
  - id: openwiki-source-7cfccf624773c01f1d98b870
    resource: repo://k8s/api/service.yaml
  - id: openwiki-source-9faeb487eace841025ab74b9
    resource: repo://k8s/client/deployment.yaml
  - id: openwiki-source-41e19db8b60a1c2629c34b15
    resource: repo://k8s/client/service.yaml
  - id: openwiki-source-b4f4eff22149e1e840f70797
    resource: repo://k8s/kustomization.yml
  - id: openwiki-source-73d414649903befd981485fc
    resource: repo://k8s/local/keda.yaml
  - id: openwiki-source-a9f75f8713f57dc30266397c
    resource: repo://k8s/local/README.md
  - id: openwiki-source-b783b2abc7bd8ab8a58681f3
    resource: repo://k8s/rabbitmq/deployment.yaml
  - id: openwiki-source-c9445389d897ec65b04944e4
    resource: repo://k8s/rabbitmq/service.yaml
  - id: openwiki-source-abe04d56b39d4dffc90d2db5
    resource: repo://k8s/README.md
  - id: openwiki-source-283ec34d10d48f0d2198a165
    resource: repo://k8s/scheduler/deployment.yaml
  - id: openwiki-source-b8e99e4d9c7ab624d83025a1
    resource: repo://k8s/worker/deployment.yaml
  - id: openwiki-source-7f0d049ce76dee382babb0ea
    resource: repo://scripts/kubernetes/local_secrets.py
  - id: openwiki-source-ae3e812414fd7902fe28055f
    resource: repo://scripts/kubernetes/local-kind.sh
  - id: openwiki-source-ce61cfd2dc9ac8ebc8ca1440
    resource: repo://scripts/kubernetes/spike.py
generated: { by: "bifrost-atlas", at: "2026-10-18T02:51:00.673Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Kubernetes Manifests and Isolated Local Kind

There are three related but distinct surfaces here: the `k8s/` application manifests, the `deploy/kubernetes/builds/` overlay that opts the scheduler into Kubernetes App build Jobs, and the local Kind harness under `k8s/local/`. Repository YAML describes deployable intent, while the Kind scripts can produce local observations; neither proves which configuration is currently installed in an operator-managed cluster. See [deployment topology](deployment-and-infrastructure.md), [development and verification](development-and-verification.md), and [devices and workers](../integrations/devices-and-workers.md).

## Base Kubernetes manifests

The root `k8s/kustomization.yml` includes the namespace and nonsecret ConfigMap plus API, client, worker, scheduler, and RabbitMQ workloads/services. PostgreSQL and object storage are prerequisites configured outside this base; `k8s/README.md` supports S3-compatible object storage or native Azure Blob settings and warns that the generic manifests do not configure Azure Workload Identity ([base resources](../../k8s/kustomization.yml), [prerequisites and storage configuration](../../k8s/README.md#L5-L10), [Azure identity constraint](../../k8s/README.md#L89-L107)). The base also has no ingress manifest; the operator chooses the public exposure method ([exposure boundary](../../k8s/README.md#L143-L160)).

The API deployment runs the init container command before the API, loads both config and Secret values into the init and API containers, and defines readiness and liveness probes. The worker consumes the same runtime configuration but disables service-account token automount and adds a nonroot, read-only-root-filesystem security context with writable `emptyDir` mounts for its home and temp paths ([API init and probes](../../k8s/api/deployment.yaml#L18-L81), [worker security and mounts](../../k8s/worker/deployment.yaml#L18-L97)). The scheduler manifest notes PostgreSQL-backed leader election for trigger scheduling while allowing its platform-job pool to run across replicas; RabbitMQ is represented as a single `Recreate` deployment with an `emptyDir`, so that manifest alone does not provide broker persistence ([scheduler deployment](../../k8s/scheduler/deployment.yaml#L10-L63), [RabbitMQ storage and rollout](../../k8s/rabbitmq/deployment.yaml#L10-L71)).

The documented baseline is applied with Kustomize after the operator supplies the namespace secrets and configuration. The checked-in `k8s/secret.yaml` is a template and must not receive real credentials; no example values belong in this page or a committed manifest ([deployment instructions](../../k8s/README.md#L40-L72), [Kustomize application](../../k8s/README.md#L109-L125)).

## Workload and service roles

| Manifest family | Repository-declared role |
|---|---|
| `k8s/api/` | Two API replicas; init container runs application initialization before Uvicorn, and the ClusterIP Service exposes port 8000 to in-cluster consumers ([deployment](../../k8s/api/deployment.yaml#L1-L44), [service](../../k8s/api/service.yaml#L1-L18)). |
| `k8s/client/` | Two web-client replicas behind a ClusterIP Service on port 80 ([deployment](../../k8s/client/deployment.yaml#L1-L49), [service](../../k8s/client/service.yaml#L1-L18)). |
| `k8s/rabbitmq/` | Single RabbitMQ broker deployment with AMQP and management ports on a ClusterIP Service. The declared data volume is `emptyDir`, so the base manifest does not persist broker data across pod replacement ([deployment](../../k8s/rabbitmq/deployment.yaml#L1-L71), [service](../../k8s/rabbitmq/service.yaml#L1-L21)). |
| `k8s/scheduler/` | Runs `src.scheduler.main` separately from web and worker processes; comments specify PostgreSQL trigger-leader election while platform-job work can run across replicas ([scheduler deployment](../../k8s/scheduler/deployment.yaml#L1-L62)). |
| `k8s/worker/` | Runs the worker process separately, does not mount a Kubernetes service-account token, and allows a 360-second termination grace period for drain and cleanup ([worker deployment](../../k8s/worker/deployment.yaml#L1-L99)). |

These are repository resource definitions and topology roles. They do not describe the current MTG Azure deployment or establish that any cluster has applied these manifests; MTG's ownership boundary is documented in [delivery lanes](../../docs/dev/delivery-lanes.md).

## Optional Kubernetes App build overlay

`deploy/kubernetes/builds/` composes the base Kustomize resources with a build-controller ServiceAccount, a build-runner ServiceAccount, a namespaced Role/RoleBinding, and build backend settings. The Role is limited to Job create/read/list/patch/delete and Pod get/list in the Bifrost namespace. The runner has token automount disabled; the overlay's scheduler patch assigns controller access and enables its token, while the API patch only adds build settings ([overlay composition](../../deploy/kubernetes/builds/kustomization.yaml), [accounts and bounded RBAC](../../deploy/kubernetes/builds/serviceaccounts.yaml#L1-L22), [Job and Pod permissions](../../deploy/kubernetes/builds/rbac.yaml#L1-L56), [scheduler patch](../../deploy/kubernetes/builds/scheduler-controller-patch.yaml), [API patch](../../deploy/kubernetes/builds/api-env-patch.yaml)).

The build ConfigMap sets the Kubernetes backend, namespace, supported build job types, resource bounds, and pending timeout. Its image field is a literal `REPLACE_WITH_EXACT_FORK_API_IMAGE_DIGEST` placeholder, so an operator must supply a reviewed image digest before using the overlay; it is not ready-to-apply as committed ([build settings](../../deploy/kubernetes/builds/build-config.yaml#L1-L20)).

## Local Kind lifecycle and isolation

Use `./test.sh kubernetes up|status|collect|down` or the underlying `scripts/kubernetes/local-kind.sh`. The script derives a default cluster name and `/tmp` state directory from the absolute checkout path, stores its own Kind binary and kubeconfig there, and routes every `kubectl` call through that kubeconfig and explicit Kind context ([entry point](../../test.sh#L19-L21), [worktree state and context](../../scripts/kubernetes/local-kind.sh#L5-L17), [kubectl routing](../../scripts/kubernetes/local-kind.sh#L35-L37)). It renders local Kustomize output to that state directory and substitutes the worktree-specific image tag; the `up` sequence then applies the namespace/configuration, local dependencies, init job, Bifrost workloads, and KEDA resources, waiting on readiness along the way ([rendering](../../scripts/kubernetes/local-kind.sh#L94-L110), [startup](../../scripts/kubernetes/local-kind.sh#L214-L245)).

`local_secrets.py` generates random local credentials and creates the `bifrost-local-secrets` Secret through stdin only after validating an explicit `kind-*` context and absolute kubeconfig. If a Secret already exists, it verifies identity and completeness and leaves it intact; an incomplete Secret is an error rather than an invitation to rotate credentials. Keep any retrieved generated password outside logs and documentation ([local secret creation](../../scripts/kubernetes/local_secrets.py#L14-L56), [local credential boundary](../../k8s/local/README.md#L25-L37)).

The local manifest set includes PostgreSQL, PgBouncer, RabbitMQ, Redis, SeaweedFS, the init job, API, scheduler, workers, and KEDA. The worker `ScaledObject` watches RabbitMQ queues, and the local harness pauses scale-in while queue-drain behavior remains unproven. KEDA owns worker replica count while the `ScaledObject` exists; manual scaling requires an explicit pause or removal of that object ([local component set](../../k8s/local/README.md#L39-L48), [queue triggers and scale-in policy](../../k8s/local/keda.yaml#L12-L71), [ownership guidance](../../k8s/local/README.md#L68-L68)).

Kubernetes App build Jobs are disabled in Kind by default. Setting `BIFROST_KIND_BUILD_JOBS=1` applies the local build-job manifest and patches the scheduler onto the local controller account; the API and worker retain disabled token automount ([opt-in gate](../../scripts/kubernetes/local-kind.sh#L158-L164), [scheduler-only patch behavior](../../scripts/kubernetes/local-kind.sh#L126-L145), [local build-job boundaries](../../k8s/local/README.md#L50-L60)). This is an isolated experiment configuration, not authorization to enable the feature in a shared cluster.

## Experiments and collected evidence

The harness exposes local `experiment` modes for warm, SDK, cold start, burst/load, worker drain, and deadline behavior. The experiment script submits test workloads through the local API, records workload output, worker replica timelines, and memory snapshots, and restores the prior KEDA policy in a `finally` path. These reports document only that particular local run and should be paired with its source revision and local cluster identity; they do not establish production performance or behavior ([experiment modes and artifacts](../../scripts/kubernetes/spike.py#L1-L6), [workload setup and observations](../../scripts/kubernetes/spike.py#L79-L156), [KEDA policy restoration](../../scripts/kubernetes/spike.py#L157-L167)).

`status` reports the selected local cluster/context, `collect` writes pod/resource descriptions and API, scheduler, worker, and RabbitMQ logs under the harness state directory, and `down` deletes that Kind cluster and its kubeconfig ([status](../../scripts/kubernetes/local-kind.sh#L248-L264), [diagnostics](../../scripts/kubernetes/local-kind.sh#L266-L282), [teardown](../../scripts/kubernetes/local-kind.sh#L284-L290)). Those commands are local harness evidence only. Verify deployed image digests, cloud identity, ingress, storage, and runtime health through the owning deployment system when a live operational answer is needed.
