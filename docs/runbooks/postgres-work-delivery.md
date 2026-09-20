# PostgreSQL work-delivery cutover and rollback

This runbook covers the bounded transition from RabbitMQ work delivery to the
PostgreSQL delivery tables, and the reverse transition. It is an operator
procedure for a coordinated maintenance window. It does not make a mixed
RabbitMQ/PostgreSQL deployment safe, replay accepted work, or promise recovery
of work that was never durably accepted.

## Invariants

- `BIFROST_WORK_DELIVERY_BACKEND` defaults to `rabbitmq`. Keep that value in
  `.env` after the first deployment unless this runbook is being followed.
- The API, scheduler, and every worker must select the same backend. Do not
  roll only one deployment or leave old pods consuming while new pods publish
  to another backend.
- The additive delivery schema and its rows are retained across rollback.
  Never run an Alembic downgrade, purge a queue, or copy/replay accepted
  messages as a cutover step.
- Package installation fan-out is included in the maintenance window. In
  PostgreSQL mode it uses the durable worker-control command path; a new worker
  warms the requirements cache and converges package requirements at startup.
- Delivery and package-command budgets are bounded recovery signals. They are
  not a guarantee that an interrupted domain operation or package install can
  be recovered automatically.

## Preflight

Run the checks from the API image/container (or an equivalent environment with
the repository's `src` package and database credentials). Record the output in
the change record.

```text
alembic current
```

After the target process environment is set to PostgreSQL, run the status
check. It is intentionally unavailable in RabbitMQ mode; it summarizes
`work_deliveries` and package control-command states. For a bounded inspection
of transport rows:

```text
python -m src.jobs.dlq_cli status
python -m src.jobs.dlq_cli inspect workflow-executions --status all --limit 100
python -m src.jobs.dlq_cli inspect agent-runs --status all --limit 100
```

The API readiness endpoint checks the selected work-delivery schema in
PostgreSQL. It deliberately reports legacy RabbitMQ as `not_configured` in
PostgreSQL mode. Readiness is not proof that RabbitMQ queues are empty or that
domain executions have reached terminal outcomes.

Before changing the flag, verify that the target image contains the additive
migration and that the migration has been applied with `alembic upgrade head`.
The Kubernetes API init container runs that command. Do not downgrade to make
the old backend look clean; package-command rows are operational evidence.

## Quiesce and drain

There is currently no first-class API command that pauses admissions or the
scheduler. The operator must use the deployment's ingress/admission control to
stop new execution and package requests, and stop scheduler triggers for the
maintenance window. In the checked-in Kubernetes template this is represented
by scaling the scheduler deployment to zero, but the template's `bifrost`
namespace is not the Midtown production namespace. Use the exact protected
infrastructure command for the target environment; this repository does not
provide a live admission or scheduler pause command.

Keep workers running while they drain. Do not scale workers down until their
old-backend work and in-flight handlers have settled. The worker drain budget
is 300 seconds plus a 60-second termination margin in the Kubernetes manifest.

RabbitMQ drain verification must include each primary queue and its retry and
poison obligations. The current execution policies name:

- `workflow-executions`
- `agent-runs`
- `agent-summarization`
- `agent-summarization-backfill`
- `agent-tuning-chat`
- `package-installations` (fan-out exchange/consumer path)

Retry queues are generated per policy and stage (for example,
`<queue>-retry-<stage>`), and poison queues are generated as
`<queue>-poison`. Use the broker's authenticated management tooling and record
ready, unacknowledged, retry, and poison counts for all of them. This
repository does not provide a safe bulk RabbitMQ drain command. Do not purge,
blindly replay, or copy messages to another backend.

For PostgreSQL inventory, use the CLI above. A queued or claimed row, a poison
or interrupted row, and a package command that has not reached a terminal state
must be reconciled through its owning workflow or package-control path before
the cutover is declared complete. Generic PostgreSQL replay is intentionally
refused by `src.jobs.dlq_cli`; use the domain-specific recovery procedure with
an exact delivery identity when one exists.

Confirm that the remaining domain executions have the expected terminal
outcomes in the platform UI/API and that no new admission, scheduler, retry,
or fan-out publisher is producing work. Queue depth alone is not a domain
outcome check.

## Kubernetes cutover

The checked-in Kubernetes template consumes `bifrost-config` for API, scheduler,
and workers and now carries the safe default `rabbitmq`. It is a template, not
the Midtown production namespace or deployment source. The actual promoted
configuration is owned by `bifrost-infra`; use its protected deployment path to
set the same value in the app, scheduler, and worker sources. Do not live-patch
an arbitrary ConfigMap or invent a one-command coordinated rollout here.

Before the protected restart, the infrastructure change must also disable the
RabbitMQ queue-length scaler for the workflow worker and keep a nonzero worker
floor while PostgreSQL delivery is being canaried. A RabbitMQ KEDA trigger that
remains active can scale the PostgreSQL workers to zero or create a second
consumer population. The platform template does not own the Midtown KEDA
ScaledObject, so this is a required infra promotion gate rather than a command
from this repository.

The protected rollout must quiesce admissions and scheduler triggers, set
`BIFROST_WORK_DELIVERY_BACKEND=postgres`, and restart API, scheduler, and all
workers together. Verify the resulting pods through the deployment's
authoritative read-only status and logs. In PostgreSQL mode workers should use
the workflow poller for package commands rather than starting the RabbitMQ
package consumer. Do not reopen admissions until all three roles report the
same backend and old Rabbit consumers are gone.

Run the status/inspection commands again from a PostgreSQL-mode API or worker
container. A canary also needs an isolated workflow-only worker process using
the same image, credentials, backend, and
`BIFROST_WORKFLOW_QUEUE_NAME=workflow-executions-canary`; it must be started by
the protected deployment tooling before the publisher runs. Do not point a
normal production worker at the canary queue or run only the publisher.

With that worker available, run the existing publisher from the API/worker
environment:

```text
BIFROST_WORK_DELIVERY_BACKEND=postgres BIFROST_WORKFLOW_QUEUE_NAME=workflow-executions-canary python -m src.jobs.workflow_canary
```

`workflow_canary.py` enforces the `-canary` suffix and uses the selected
backend's poison-depth check. Confirm the resulting workflow's actual domain
outcome and the absence of a new poison/interrupted row before reopening
admissions. A publisher-only run is not a canary proof.

## Rollback to RabbitMQ

Use the same quiesce procedure. Stop new admissions and scheduler triggers,
let PostgreSQL workers finish or explicitly reconcile their in-flight work, and
record `python -m src.jobs.dlq_cli status` plus the relevant `inspect` output.
Do not treat a worker disappearing or a lease expiring as successful completion.

Set the same backend value on the API, scheduler, and workers through the
protected infrastructure source, then restart all three roles together through
that deployment path. There is no verified Midtown namespace, deployment
selector, or one-command rollout in this repository, so an operator must use
the exact command emitted by the infrastructure promotion. Keep the RabbitMQ
scaler disabled until the Rabbit worker population is deliberately restored.

Verify RabbitMQ health, the primary plus retry/poison queues, API readiness,
scheduler health, worker consumer startup, and domain outcomes before reopening
admissions. Keep the PostgreSQL schema and all rows. Do not run `alembic
downgrade`, purge queues, or replay/copy accepted work during rollback.

## Compose and known gates

`.env.example`, `docker-compose.yml`, and `docker-compose.dev.yml` now carry the
safe default and forward the flag to the `api`, `scheduler`, and `worker`
services. Verify the resolved service environment before using Compose:

```text
docker compose config
docker compose ps
```

Do not infer propagation from the presence of a line in `.env`; `docker compose
config` must show the same selected value for all three services. Compose still
requires the same quiesce, drain, coordinated restart, and canary gates; the
flag is process-start configuration, not a live hot switch. There is also no
repository-owned admission pause or broker-wide drain command, and no live
deployment proof has been performed as part of this documentation change.
