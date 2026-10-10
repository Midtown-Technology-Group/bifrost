# Isolated common owner foundation

This first Rust transaction observes actual retained owner/session identity under
Thomas's released common lock order. It does not write lifecycle state, admit a
workload, issue credentials or spawn processes. Production dispatch is absent.
Matching database custody digests are observations, never proof of a live channel.
The SQL returns no portable launch authority.

The separate crate deliberately imports no Go source or Rust control-plane
implementation. SQLx0.9.0/Tokio1.53.1/toolchain1.98.1 match the existing Rust SQL
characterization source0fa18ddda7ce8ac101df76fe06c7e72b075a8803. This is new source,
not transferred runtime evidence from that characterization or the codec tests.
An isolated dependency bootstrap generates and retains the lockfile before the
locked offline checks. The returned lockfile must be reviewed and committed;
real PostgreSQL pool/race evidence is required before extending this foundation
into admission, Start, cancellation and Result/Receipt transactions.

Observation takes the exact shared workspace fence, typed attempt, NOWAIT owner,
execution, deployment and Solution, then session. Every identity is bound as a
query parameter. Wrong login, stale claim/incarnation, missing association and
secondary contention reject; transaction drop rolls back. A failed commit
observation is explicitly uncertain and cannot trigger automatic authority retry.
No synthetic store or Go-specific public lifecycle state is introduced.
