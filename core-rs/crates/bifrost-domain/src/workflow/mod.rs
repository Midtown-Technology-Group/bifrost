//! Source-compatible workflow row plans, not durable ownership or Start permits.
//!
//! A future SQL caller must refresh these rows under its ratified lock order,
//! recheck caller/source/session/owner authority, apply matching SQL predicates
//! in that same transaction, and commit before derived effects. `Now` refers to
//! its source-compatible UTC sample. Unlisted columns stay unchanged; no plan
//! survives a transaction. Payloads, retry policy and legacy projection are
//! deliberately adapter responsibilities.

use bifrost_contracts::runtime::CanonicalUuid;

#[cfg(test)]
mod tests;

/// Parent-private capability. Equality is permitted; formatting/export is not.
#[derive(Clone, PartialEq, Eq)]
pub struct ClaimToken(CanonicalUuid);

impl ClaimToken {
    pub fn new(value: CanonicalUuid) -> Self {
        Self(value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogicalExecutionStatus {
    Scheduled,
    Pending,
    Running,
    Success,
    Failed,
    Timeout,
    Stuck,
    CompletedWithErrors,
    Cancelling,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AttemptStatus {
    Dispatching,
    Published,
    Claimed,
    Running,
    Succeeded,
    Failed,
    TimedOut,
    Cancelled,
    WorkerLost,
    AdmissionRejected,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AttemptPhase {
    Dispatch,
    Queue,
    Claim,
    Admission,
    Execution,
    Result,
    Terminal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailurePhase {
    Dispatch,
    Queue,
    Claim,
    Admission,
    Execution,
    Result,
    Worker,
    Cancellation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailureCode {
    ExecutionTimeout,
    Cancelled,
    ResultPersistFailed,
    TenantCodeError,
    CancelledBeforeClaim,
}

#[derive(Clone, PartialEq, Eq)]
pub struct LogicalExecutionView {
    pub id: CanonicalUuid,
    pub status: LogicalExecutionStatus,
}

// No Debug/serialization: this view contains the private claim capability.
#[derive(Clone, PartialEq, Eq)]
pub struct WorkflowAttemptView {
    pub id: CanonicalUuid,
    pub execution_id: CanonicalUuid,
    pub claim_token: Option<ClaimToken>,
    pub status: AttemptStatus,
    pub phase: AttemptPhase,
    pub started_at_present: bool,
    pub completed_at_present: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AttemptHistory {
    Recorded,
    Unrecorded,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeFailureKind {
    Timeout,
    Cancelled,
    ResultPersistence,
    TenantCode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkflowOutcome {
    /// Parent-normalized status, including the incumbent nonterminal values.
    Success(LogicalExecutionStatus),
    Failure(RuntimeFailureKind),
    /// Trusted parent classification; Running needs a separate retry decision.
    CoordinatorLoss,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimeWrite {
    Keep,
    Now,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputWrite {
    Keep,
    /// Assign the supplied value, including SQL NULL.
    SetSupplied,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProjectionPermission {
    Keep,
    /// Adapter may project supplied data using incumbent null/merge semantics.
    AllowSupplied,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecisionError {
    MissingExecution,
    MissingAttempt,
    InvalidAttemptFence,
    InvalidAttemptState,
    InvalidLogicalState,
    MissingFence,
    LegacyUnfencedOutsideTrackedPath,
    RequiresCoordinatorPolicy,
    InconsistentRows,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AttemptRunningPlan {
    pub status: AttemptStatus,
    pub phase: AttemptPhase,
    pub started_at: TimeWrite,
    pub heartbeat_at: TimeWrite,
    pub process_id: InputWrite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResultAttemptPlan {
    pub status: AttemptStatus,
    pub phase: AttemptPhase,
    pub failure_phase: Option<FailurePhase>,
    pub failure_code: Option<FailureCode>,
    pub started_at: TimeWrite,
    pub heartbeat_at: TimeWrite,
    pub completed_at: TimeWrite,
    pub duration_ms: InputWrite,
    pub peak_memory_bytes: InputWrite,
    pub cpu_total_seconds: InputWrite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResultExecutionPlan {
    pub status: LogicalExecutionStatus,
    pub duration_ms: InputWrite,
    pub completed_at: TimeWrite,
    pub result: ProjectionPermission,
    pub result_type: ProjectionPermission,
    pub error_message: ProjectionPermission,
    pub time_saved: ProjectionPermission,
    pub value: ProjectionPermission,
    pub variables: ProjectionPermission,
    pub execution_context: ProjectionPermission,
    pub metrics: ProjectionPermission,
    pub logs: ProjectionPermission,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FencedResultPlan {
    pub execution: ResultExecutionPlan,
    pub attempt: ResultAttemptPlan,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CancelAttemptPlan {
    pub status: AttemptStatus,
    pub phase: AttemptPhase,
    pub failure_phase: FailurePhase,
    pub failure_code: FailureCode,
    pub completed_at: TimeWrite,
    pub heartbeat_at: TimeWrite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CancelPlan {
    pub status: LogicalExecutionStatus,
    pub completed_at: TimeWrite,
    pub attempt: Option<CancelAttemptPlan>,
}

fn matched_attempt<'a>(
    attempt: Option<&'a WorkflowAttemptView>,
    execution_id: &CanonicalUuid,
    token: &ClaimToken,
) -> Result<&'a WorkflowAttemptView, DecisionError> {
    let attempt = attempt.ok_or(DecisionError::MissingAttempt)?;
    if attempt.execution_id != *execution_id
        || attempt.claim_token.as_ref() != Some(token)
        || attempt.completed_at_present
    {
        return Err(DecisionError::InvalidAttemptFence);
    }
    Ok(attempt)
}

pub fn plan_attempt_running(
    attempt: Option<&WorkflowAttemptView>,
    execution_id: &CanonicalUuid,
    token: &ClaimToken,
    process_id_supplied: bool,
) -> Result<AttemptRunningPlan, DecisionError> {
    let attempt = matched_attempt(attempt, execution_id, token)?;
    if !matches!(
        attempt.status,
        AttemptStatus::Claimed | AttemptStatus::Running
    ) {
        return Err(DecisionError::InvalidAttemptState);
    }
    Ok(AttemptRunningPlan {
        status: AttemptStatus::Running,
        phase: AttemptPhase::Execution,
        started_at: if attempt.started_at_present {
            TimeWrite::Keep
        } else {
            TimeWrite::Now
        },
        heartbeat_at: TimeWrite::Now,
        process_id: if process_id_supplied {
            InputWrite::SetSupplied
        } else {
            InputWrite::Keep
        },
    })
}

pub fn plan_fenced_result(
    execution: Option<&LogicalExecutionView>,
    attempt: Option<&WorkflowAttemptView>,
    execution_id: &CanonicalUuid,
    token: Option<&ClaimToken>,
    history: AttemptHistory,
    outcome: WorkflowOutcome,
    duration_supplied: bool,
) -> Result<FencedResultPlan, DecisionError> {
    // The incumbent missing-fence check precedes logical/attempt row guards.
    let token = token.ok_or(match history {
        AttemptHistory::Recorded => DecisionError::MissingFence,
        AttemptHistory::Unrecorded => DecisionError::LegacyUnfencedOutsideTrackedPath,
    })?;
    let execution = execution.ok_or(DecisionError::MissingExecution)?;
    if execution.id != *execution_id {
        return Err(DecisionError::MissingExecution);
    }
    if !matches!(
        execution.status,
        LogicalExecutionStatus::Running | LogicalExecutionStatus::Cancelling
    ) {
        return Err(DecisionError::InvalidLogicalState);
    }
    // No current attempt-status guard: finalize_attempt only matches the fence.
    matched_attempt(attempt, execution_id, token)?;
    let cancellation_won = execution.status == LogicalExecutionStatus::Cancelling;
    let (logical_status, attempt_status, failure_code, failure_phase) = if cancellation_won {
        (
            LogicalExecutionStatus::Cancelled,
            AttemptStatus::Cancelled,
            Some(FailureCode::Cancelled),
            Some(FailurePhase::Cancellation),
        )
    } else {
        match outcome {
            WorkflowOutcome::Success(status) => (status, AttemptStatus::Succeeded, None, None),
            WorkflowOutcome::Failure(RuntimeFailureKind::Timeout) => (
                LogicalExecutionStatus::Timeout,
                AttemptStatus::TimedOut,
                Some(FailureCode::ExecutionTimeout),
                Some(FailurePhase::Execution),
            ),
            WorkflowOutcome::Failure(RuntimeFailureKind::Cancelled) => (
                LogicalExecutionStatus::Cancelled,
                AttemptStatus::Cancelled,
                Some(FailureCode::Cancelled),
                Some(FailurePhase::Cancellation),
            ),
            WorkflowOutcome::Failure(RuntimeFailureKind::ResultPersistence) => (
                LogicalExecutionStatus::Failed,
                AttemptStatus::Failed,
                Some(FailureCode::ResultPersistFailed),
                Some(FailurePhase::Result),
            ),
            WorkflowOutcome::Failure(RuntimeFailureKind::TenantCode) => (
                LogicalExecutionStatus::Failed,
                AttemptStatus::Failed,
                Some(FailureCode::TenantCodeError),
                Some(FailurePhase::Execution),
            ),
            WorkflowOutcome::CoordinatorLoss => {
                return Err(DecisionError::RequiresCoordinatorPolicy);
            }
        }
    };
    let projection = if cancellation_won {
        ProjectionPermission::Keep
    } else {
        ProjectionPermission::AllowSupplied
    };
    Ok(FencedResultPlan {
        execution: ResultExecutionPlan {
            status: logical_status,
            duration_ms: if duration_supplied {
                InputWrite::SetSupplied
            } else {
                InputWrite::Keep
            },
            completed_at: if duration_supplied {
                TimeWrite::Now
            } else {
                TimeWrite::Keep
            },
            result: projection,
            result_type: projection,
            error_message: projection,
            time_saved: projection,
            value: projection,
            variables: ProjectionPermission::AllowSupplied,
            execution_context: ProjectionPermission::AllowSupplied,
            metrics: ProjectionPermission::AllowSupplied,
            logs: ProjectionPermission::AllowSupplied,
        },
        attempt: ResultAttemptPlan {
            status: attempt_status,
            phase: AttemptPhase::Terminal,
            failure_phase,
            failure_code,
            started_at: TimeWrite::Keep,
            heartbeat_at: TimeWrite::Now,
            completed_at: TimeWrite::Now,
            duration_ms: InputWrite::SetSupplied,
            peak_memory_bytes: InputWrite::SetSupplied,
            cpu_total_seconds: InputWrite::SetSupplied,
        },
    })
}

pub fn plan_cancel_state(
    execution: Option<&LogicalExecutionView>,
    active_attempt: Option<&WorkflowAttemptView>,
    execution_id: &CanonicalUuid,
) -> Result<CancelPlan, DecisionError> {
    let execution = execution.ok_or(DecisionError::MissingExecution)?;
    if execution.id != *execution_id {
        return Err(DecisionError::MissingExecution);
    }
    match execution.status {
        LogicalExecutionStatus::Scheduled | LogicalExecutionStatus::Pending => {
            // Only the queued branch selects/inspects an incomplete attempt.
            let attempt = match active_attempt {
                Some(attempt) if !attempt.completed_at_present => {
                    if attempt.execution_id != *execution_id {
                        return Err(DecisionError::InconsistentRows);
                    }
                    Some(CancelAttemptPlan {
                        status: AttemptStatus::Cancelled,
                        phase: AttemptPhase::Terminal,
                        failure_phase: FailurePhase::Cancellation,
                        failure_code: FailureCode::CancelledBeforeClaim,
                        completed_at: TimeWrite::Now,
                        heartbeat_at: TimeWrite::Now,
                    })
                }
                _ => None,
            };
            Ok(CancelPlan {
                status: LogicalExecutionStatus::Cancelled,
                completed_at: TimeWrite::Now,
                attempt,
            })
        }
        LogicalExecutionStatus::Running => Ok(CancelPlan {
            status: LogicalExecutionStatus::Cancelling,
            completed_at: TimeWrite::Keep,
            attempt: None,
        }),
        _ => Err(DecisionError::InvalidLogicalState),
    }
}

/// A future parent must supply a non-null claim capability after a plan exists.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClaimTokenWrite {
    SetParentNonNull,
}

/// Directives for reusing an already-published, unfenced active attempt.
/// Both `Now` writes require one future caller-owned UTC sample. Unlisted
/// columns remain unchanged; this value grants no durable claim authority.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExistingAttemptClaimPlan {
    pub logical_status: LogicalExecutionStatus,
    pub attempt_status: AttemptStatus,
    pub attempt_phase: AttemptPhase,
    pub claim_token: ClaimTokenWrite,
    pub worker_id: InputWrite,
    pub worker_incarnation_id: InputWrite,
    pub claimed_at: TimeWrite,
    pub heartbeat_at: TimeWrite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClaimDecision {
    Plan(ExistingAttemptClaimPlan),
    DeferLegacyInline,
    NoClaim,
    DeferAttemptAllocation,
}

/// Plan only the existing-attempt branch of the original claim operation.
/// Allocation, delivery ownership, locking, token generation and commit remain
/// caller obligations. Supplied worker `None` values are SQL NULL writes.
pub fn plan_existing_attempt_claim(
    execution: Option<&LogicalExecutionView>,
    active_attempt: Option<&WorkflowAttemptView>,
    execution_id: &CanonicalUuid,
) -> Result<ClaimDecision, DecisionError> {
    let Some(execution) = execution else {
        return Ok(ClaimDecision::DeferLegacyInline);
    };
    if execution.status != LogicalExecutionStatus::Pending {
        return Ok(ClaimDecision::NoClaim);
    }
    if &execution.id != execution_id {
        return Err(DecisionError::InconsistentRows);
    }
    let Some(attempt) = active_attempt else {
        return Ok(ClaimDecision::DeferAttemptAllocation);
    };
    if &attempt.execution_id != execution_id || attempt.completed_at_present {
        return Err(DecisionError::InconsistentRows);
    }
    if attempt.status != AttemptStatus::Published || attempt.claim_token.is_some() {
        return Err(DecisionError::InvalidAttemptState);
    }
    Ok(ClaimDecision::Plan(ExistingAttemptClaimPlan {
        logical_status: LogicalExecutionStatus::Running,
        attempt_status: AttemptStatus::Claimed,
        attempt_phase: AttemptPhase::Claim,
        claim_token: ClaimTokenWrite::SetParentNonNull,
        worker_id: InputWrite::SetSupplied,
        worker_incarnation_id: InputWrite::SetSupplied,
        claimed_at: TimeWrite::Now,
        heartbeat_at: TimeWrite::Now,
    }))
}
