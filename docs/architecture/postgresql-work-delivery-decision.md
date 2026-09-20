# Proposed: PostgreSQL-backed work delivery

Date: 2026-09-19. Status: **Proposed; contract and prototype required**.
Scope: MTG platform fork; docs only. No default transport or schema changes.

Parent: [#654](https://github.com/Midtown-Technology-Group/bifrost/issues/654).
Program: [infra #1481](https://github.com/MTG-Thomas/bifrost-infra/issues/1481).
Initial work: [contract #762](https://github.com/Midtown-Technology-Group/bifrost/issues/762)
and [summarization prototype #763](https://github.com/Midtown-Technology-Group/bifrost/issues/763).

## Context and proposed decision

Evaluate replacing RabbitMQ delivery with PostgreSQL while preserving the
execution engine and its domain contracts. Dependency reduction and retiring
the instance's AKS broker now serve an explicit infrastructure budget: USD
300/month preferred, USD 500/month acceptable for the complete Azure estate,
excluding AI/API usage and ignoring sponsorship credits.

Target service recovery within 15 minutes of host failure/deployment, preserving
accepted work and explicitly reconciling interrupted executions. Database
restore/regional-disaster RPO/RTO remain separate infra decisions. No claim of
exactly-once external side effects follows from transactional admission.

Source reviewed: `db8c959af8d93c5736fb10bd28fe22b9b90e1275`.

| Existing responsibility | Preserve or investigate |
| --- | --- |
| Workflow, agent and attempt records | Remain authoritative for execution identity and domain outcomes. |
| `jobs/schedulers/platform_jobs.py` | Reuse lease/fencing/retry patterns. Its all-candidate-ID claimant is not a general high-volume dispatch algorithm. |
| `jobs/execution_policy.py` | Preserve workload-specific admission, completion and failure semantics. |
| `jobs/rabbitmq.py` and consumers | Five durable queues: workflow, agent runs, summaries, summary backfill, tuning. Retain handler behavior and operator poison/replay capabilities. |
| `services/worker_control_commands.py` | Candidate for package convergence; currently supports recycle actions only, so install/uninstall needs explicit design. |
| Redis pub/sub and PostgreSQL platform jobs | Do not replace them merely to consolidate terminology. |

Workflow messages are currently acknowledged after child dispatch; agent
handlers can settle after durable domain completion. A generic visibility
timeout must not silently replace those boundaries. Legacy inline execution
does not yet have the same durable admission path and must be covered.

## Smallest seam, still to decide

Compare eligibility on existing domain records with a narrow delivery record
linked transactionally to its domain authority. Do not duplicate results,
credentials or authorization state into a second job system. Use the existing
PlatformJob system for durable non-workflow operations where appropriate;
workflow and agent execution retain their own lifecycles.

The selected design must support bounded `SKIP LOCKED` claims, short
transactions, ownership generations, renewal, delayed eligibility, retry
budgets, cancellation, audited terminal disposition and replay. Execute outside
transactions. Use database time for lease decisions where practical. Polling
must recover lost wake-up hints; defer LISTEN/NOTIFY optimization until needed.

Do not emulate all AMQP features. Audit and exclude unused stream/priority
helpers. Decide permanent Rabbit backend support versus temporary rollback
support before introducing a public provider interface.

## Failure contract to prove

| Interruption | Required observable behavior |
| --- | --- |
| Publisher before/after acceptance commit | No accepted job disappears; repeated admission has one durable logical identity. |
| Worker before claim or before child dispatch | Bounded recovery without competing valid owners or an unsafe execution attempt. |
| Lease expiry or database disconnect | Stale owners cannot commit a newer attempt's state; ambiguous effects are surfaced. |
| External action may have started | Apply existing workload retry restrictions. Do not automatically replay arbitrary tenant code. |
| Durable outcome committed, completion acknowledgement lost | Recovery sees the recorded outcome and does not blindly repeat the action. |
| Retry, poison or replay interruption | Preserve timing/budget, provenance and inspectability; domain and delivery state reconcile. |
| Cancellation or deployment races | Resolve with current ownership, drain admission, report unfinished work and recover explicitly. |
| Worker joins/leaves during package change | Record desired state and per-worker convergence without making shared pub/sub authoritative. |

The original #654 phrase "no lost or duplicate domain effects" is not a blanket
external exactly-once guarantee. Replace it in acceptance tests with these
specific guarantees and effect-aware reconciliation. Storage-level fencing
cannot undo an external API request already sent by an obsolete worker.

## Prototype and rollout gates

1. Resolve the contract and collect the infra cost/capacity baseline. Identify
   payload sizes, tenancy boundaries, backlog cardinality, retention, connection
   limits, indexes and database headroom before selecting a queue shape.
2. Keep #654's lower-risk **agent summarization** first prototype, with isolated
   inputs, deterministic external-provider stubs and bounded concurrency. Do
   not change production defaults or incur uncontrolled model spend.
3. Run real PostgreSQL/container tests through repo tooling: process death,
   network loss, lease expiry, stale completion, cancellation, retries, poison,
   replay and shutdown. Measure pickup latency and API/database interference
   under representative bursts. No mock-only certification.
4. Report a go/no-go and revised estimate. Only then create detailed migration
   slices for the remaining queues, workflow canary/inline recovery, package
   convergence and operator surfaces. Summarization proof is not workflow proof.
5. Select one authoritative transport per workload throughout staged migration.
   Preserve delayed, leased and poison work. Do not independently dual-publish.
6. Preserve the existing **30-day production soak before Rabbit removal** unless
   explicitly revised with evidence. Remove clients/resources only after parity
   and operational gates; compute migration follows in infra.

Rollback must respect schema compatibility and transport ownership. Fence
producers, including recovery publishers, before drain/transfer. Preserve stable
execution identity and delivery generations. Neither database downgrade nor a
backend-setting flip is a safe general rollback.

## Limits and ownership

PostgreSQL is already required for execution; adding queue traffic increases
contention, vacuum/retention and availability responsibilities. Reject or revise
the design if measured queue work threatens control-plane latency or if safe
recovery/package convergence cannot be expressed simply.

Platform owns schema, handlers, lifecycle, tests and delivery diagnostics. Infra
owns hosting, autoscaling, identity, image promotion, deploy/recovery and cost.
Workspace may supply registered benign verification workflows later; it must
not become a broker adapter or shadow platform fork.

The original 10-16-week estimate and shorter conversational estimates are
unvalidated. Re-estimate after the bounded prototype; no delivery date or paid
deployment is committed by this ADR. Preserve process isolation and existing
runtime provenance throughout. CIPP/Craft is a reference, not a dependency to
import or a reason to port BiFrost to PowerShell/.NET.
