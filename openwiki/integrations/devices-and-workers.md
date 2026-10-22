---
type: integration-architecture
title: Devices, Workers, and Execution Placement
description: Distinguishes Bifrost's local workflow workers, remote device execution protocol, platform-job Kubernetes runner, and externally scaled worker identity plane.
tags: [devices, workers, execution, kubernetes, identity]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:14:28.318Z
sources:
  - id: openwiki-source-715e62999408f6c91b4f4866
    resource: repo://api/src/jobs/platform/kubernetes_runner.py
  - id: openwiki-source-eb00519af644573d95d2e36b
    resource: repo://api/src/models/orm/device_jobs.py
  - id: openwiki-source-82570a9ca3247e57c1aca3a1
    resource: repo://api/src/routers/device_protocol.py
  - id: openwiki-source-a012db5b16b7df7be931f4a2
    resource: repo://api/src/routers/devices.py
  - id: openwiki-source-5dc66493e40630035956139a
    resource: repo://api/src/routers/platform/external_workers.py
  - id: openwiki-source-5b038673241cb6c91390d8fa
    resource: repo://api/src/services/device_jobs.py
  - id: openwiki-source-5ce2c45b8047de237c7809f7
    resource: repo://api/src/services/devices.py
  - id: openwiki-source-1e6106e351e530bb0aa77c1c
    resource: repo://docs/architecture/device-control-plane.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:14:28.318Z" }
---

# Devices, Workers, and Execution Placement

Bifrost has several execution placements with different authorities. Workflow workers execute queued workflows; Kubernetes can run eligible platform build jobs; external workers participate through an Azure managed-identity boundary; and enrolled estate devices execute device jobs under a separate server-tracked protocol. These paths should not be treated as interchangeable queues. See [execution and workflows](../runtime/execution-and-workflows.md), [durable jobs](../runtime/jobs-and-schedulers.md), and [deployment topology](../operations/deployment-and-infrastructure.md).

## Remote device control plane

Device registry routes create an organization-scoped pending device and return a one-time enrollment token. The unauthenticated enrollment endpoint accepts only that token and returns the device key once; persisted device and key records store hashes. Later device protocol requests authenticate with the device key and reject unknown or disabled devices. Device heartbeat updates `last_seen_at`, reports a poll hint, and surfaces a cooperative cancellation request. The HTTP protocol is authoritative; WebSocket broadcasts only hint that work may be available.

Job creation is guarded by either a user with device execution permission or a device-scoped control key. The device must be active and in the caller's organization/scope; attribution records the initiating user or control-key ID and optional workflow/execution references. The server creates a `device_jobs` row and treats notification as best-effort, so a failed WebSocket hint does not undo accepted work. The agent later claims one row with `FOR UPDATE SKIP LOCKED`, receives a fresh claim token, and submits `running`, logs, and terminal results fenced by that token.

Device jobs are a separate domain execution record because the remote device, rather than a platform worker, owns the side effect. Expired pre-run claims can be reclaimed, but after a run may have started, watchdog loss is terminal and is never silently requeued. Cancellation while running sets a flag the agent observes on heartbeat; the platform does not guarantee process termination. This is an accepted architecture decision recorded in the device-control-plane design and matched by the current service state machine. `lost` is written by the server watchdog, not by the agent. The source tree establishes the server contract; the remote agent implementation and its current installed version are outside this repository.

## Platform workers and Kubernetes

Platform jobs remain the shared durable path for platform-owned work. Their Kubernetes runner admits only a preclaimed platform job with the expected lease token, backend, Kubernetes job identity, and no already-bound pod. It further requires the registered job definition to use the `build` execution class, then binds the pod and renews job lease/heartbeat metadata before running the claimed job. Missing Kubernetes identity metadata or a stale/mismatched lease fails admission.

The external-worker API is a separate platform-admin control surface. Replica enrollment and identity operations are disabled by feature settings unless explicitly enabled, validate the caller's bearer identity through the Azure adapter, and return bounded authorization/unavailability errors. The identity endpoint constrains fields by operation and delegates replica ownership changes to the external-worker ownership service. This checkout shows the Bifrost side of the boundary; it does not prove Azure configuration or external replica health.

## Investigation and verification

For a device execution issue, correlate device activity, claim token/attempt state, logs, and the periodic lost-job sweep. For a platform build job, inspect its PlatformJob lease, backend, Kubernetes job/pod IDs, and scheduler diagnostics. For external workers, inspect the guarded enrollment/identity state and managed-identity configuration through its designated operations lane. Focused source tests include device protocol/auth/job state tests, Kubernetes runner tests, and external worker ownership/enrollment tests. No live worker or device operation was performed for this architecture page.
