//! Synthetic row facts derived from the frozen Python predicate/column tables.
//! These tests prove no SQL permission, refreshed snapshot, caller or owner.

use super::*;

const LOGICAL_STATES: [LogicalExecutionStatus; 10] = [
    LogicalExecutionStatus::Scheduled,
    LogicalExecutionStatus::Pending,
    LogicalExecutionStatus::Running,
    LogicalExecutionStatus::Success,
    LogicalExecutionStatus::Failed,
    LogicalExecutionStatus::Timeout,
    LogicalExecutionStatus::Stuck,
    LogicalExecutionStatus::CompletedWithErrors,
    LogicalExecutionStatus::Cancelling,
    LogicalExecutionStatus::Cancelled,
];

const ATTEMPT_STATES: [AttemptStatus; 10] = [
    AttemptStatus::Dispatching,
    AttemptStatus::Published,
    AttemptStatus::Claimed,
    AttemptStatus::Running,
    AttemptStatus::Succeeded,
    AttemptStatus::Failed,
    AttemptStatus::TimedOut,
    AttemptStatus::Cancelled,
    AttemptStatus::WorkerLost,
    AttemptStatus::AdmissionRejected,
];

fn id(number: u32) -> CanonicalUuid {
    match CanonicalUuid::new(format!("00000000-0000-4000-8000-{number:012x}")) {
        Ok(value) => value,
        Err(_) => panic!("Invalid synthetic public UUID fixture"),
    }
}

fn token(number: u32) -> ClaimToken {
    ClaimToken::new(id(number))
}

fn execution(status: LogicalExecutionStatus) -> LogicalExecutionView {
    LogicalExecutionView { id: id(1), status }
}

fn attempt(status: AttemptStatus) -> WorkflowAttemptView {
    WorkflowAttemptView {
        id: id(2),
        execution_id: id(1),
        claim_token: Some(token(3)),
        status,
        phase: AttemptPhase::Claim,
        started_at_present: false,
        completed_at_present: false,
    }
}

fn expected_result(
    status: LogicalExecutionStatus,
    attempt_status: AttemptStatus,
) -> FencedResultPlan {
    FencedResultPlan {
        execution: ResultExecutionPlan {
            status,
            duration_ms: InputWrite::Keep,
            completed_at: TimeWrite::Keep,
            result: ProjectionPermission::AllowSupplied,
            result_type: ProjectionPermission::AllowSupplied,
            error_message: ProjectionPermission::AllowSupplied,
            time_saved: ProjectionPermission::AllowSupplied,
            value: ProjectionPermission::AllowSupplied,
            variables: ProjectionPermission::AllowSupplied,
            execution_context: ProjectionPermission::AllowSupplied,
            metrics: ProjectionPermission::AllowSupplied,
            logs: ProjectionPermission::AllowSupplied,
        },
        attempt: ResultAttemptPlan {
            status: attempt_status,
            phase: AttemptPhase::Terminal,
            failure_phase: None,
            failure_code: None,
            started_at: TimeWrite::Keep,
            heartbeat_at: TimeWrite::Now,
            completed_at: TimeWrite::Now,
            duration_ms: InputWrite::SetSupplied,
            peak_memory_bytes: InputWrite::SetSupplied,
            cpu_total_seconds: InputWrite::SetSupplied,
        },
    }
}

fn expected_cancelled_result() -> FencedResultPlan {
    let mut plan = expected_result(LogicalExecutionStatus::Cancelled, AttemptStatus::Cancelled);
    plan.attempt.failure_code = Some(FailureCode::Cancelled);
    plan.attempt.failure_phase = Some(FailurePhase::Cancellation);
    plan.execution.result = ProjectionPermission::Keep;
    plan.execution.result_type = ProjectionPermission::Keep;
    plan.execution.error_message = ProjectionPermission::Keep;
    plan.execution.time_saved = ProjectionPermission::Keep;
    plan.execution.value = ProjectionPermission::Keep;
    plan
}

#[test]
fn running_covers_all_current_attempt_states_and_exact_columns() {
    for state in ATTEMPT_STATES {
        let view = attempt(state);
        let actual = plan_attempt_running(Some(&view), &id(1), &token(3), false);
        if matches!(state, AttemptStatus::Claimed | AttemptStatus::Running) {
            assert_eq!(
                actual,
                Ok(AttemptRunningPlan {
                    status: AttemptStatus::Running,
                    phase: AttemptPhase::Execution,
                    started_at: TimeWrite::Now,
                    heartbeat_at: TimeWrite::Now,
                    process_id: InputWrite::Keep,
                })
            );
        } else {
            assert_eq!(actual, Err(DecisionError::InvalidAttemptState));
        }
    }
}

#[test]
fn running_preserves_first_start_and_non_none_process_including_empty() {
    for started in [false, true] {
        for process in [None, Some(""), Some("synthetic-process")] {
            let mut view = attempt(AttemptStatus::Running);
            view.started_at_present = started;
            assert_eq!(
                plan_attempt_running(Some(&view), &id(1), &token(3), process.is_some()),
                Ok(AttemptRunningPlan {
                    status: AttemptStatus::Running,
                    phase: AttemptPhase::Execution,
                    started_at: if started {
                        TimeWrite::Keep
                    } else {
                        TimeWrite::Now
                    },
                    heartbeat_at: TimeWrite::Now,
                    process_id: if process.is_some() {
                        InputWrite::SetSupplied
                    } else {
                        InputWrite::Keep
                    },
                }),
            );
        }
    }
}

#[test]
fn running_rejects_absence_foreign_missing_wrong_and_completed_fences() {
    assert_eq!(
        plan_attempt_running(None, &id(1), &token(3), true,),
        Err(DecisionError::MissingAttempt)
    );
    let mut foreign = attempt(AttemptStatus::Claimed);
    foreign.execution_id = id(9);
    let mut missing = attempt(AttemptStatus::Claimed);
    missing.claim_token = None;
    let mut wrong = attempt(AttemptStatus::Claimed);
    wrong.claim_token = Some(token(9));
    let mut completed = attempt(AttemptStatus::Claimed);
    completed.completed_at_present = true;
    for view in [foreign, missing, wrong, completed] {
        assert_eq!(
            plan_attempt_running(Some(&view), &id(1), &token(3), true,),
            Err(DecisionError::InvalidAttemptFence)
        );
    }
}

#[test]
fn missing_result_fence_precedes_every_logical_or_attempt_guard() {
    let stale = attempt(AttemptStatus::Succeeded);
    for state in LOGICAL_STATES {
        let row = execution(state);
        for logical in [None, Some(&row)] {
            assert_eq!(
                plan_fenced_result(
                    logical,
                    Some(&stale),
                    &id(9),
                    None,
                    AttemptHistory::Recorded,
                    WorkflowOutcome::CoordinatorLoss,
                    false,
                ),
                Err(DecisionError::MissingFence),
            );
            assert_eq!(
                plan_fenced_result(
                    logical,
                    Some(&stale),
                    &id(9),
                    None,
                    AttemptHistory::Unrecorded,
                    WorkflowOutcome::CoordinatorLoss,
                    false,
                ),
                Err(DecisionError::LegacyUnfencedOutsideTrackedPath),
            );
        }
    }
}

#[test]
fn tracked_result_checks_every_logical_state_before_attempt_and_outcome() {
    let view = attempt(AttemptStatus::Claimed);
    for state in LOGICAL_STATES {
        let row = execution(state);
        let actual = plan_fenced_result(
            Some(&row),
            Some(&view),
            &id(1),
            Some(&token(3)),
            AttemptHistory::Recorded,
            WorkflowOutcome::Success(LogicalExecutionStatus::Success),
            false,
        );
        match state {
            LogicalExecutionStatus::Running => assert_eq!(
                actual,
                Ok(expected_result(
                    LogicalExecutionStatus::Success,
                    AttemptStatus::Succeeded,
                ))
            ),
            LogicalExecutionStatus::Cancelling => {
                assert_eq!(actual, Ok(expected_cancelled_result()))
            }
            _ => assert_eq!(actual, Err(DecisionError::InvalidLogicalState)),
        }
    }
    let row = execution(LogicalExecutionStatus::Running);
    assert_eq!(
        plan_fenced_result(
            None,
            Some(&view),
            &id(1),
            Some(&token(3)),
            AttemptHistory::Recorded,
            WorkflowOutcome::CoordinatorLoss,
            false,
        ),
        Err(DecisionError::MissingExecution)
    );
    assert_eq!(
        plan_fenced_result(
            Some(&row),
            Some(&view),
            &id(9),
            Some(&token(3)),
            AttemptHistory::Recorded,
            WorkflowOutcome::CoordinatorLoss,
            false,
        ),
        Err(DecisionError::MissingExecution)
    );
}

#[test]
fn all_ten_normalized_success_statuses_and_optional_zero_duration_are_preserved() {
    let row = execution(LogicalExecutionStatus::Running);
    let view = attempt(AttemptStatus::Claimed);
    for status in LOGICAL_STATES {
        for duration in [None, Some(0), Some(7)] {
            let mut expected = expected_result(status, AttemptStatus::Succeeded);
            if duration.is_some() {
                expected.execution.duration_ms = InputWrite::SetSupplied;
                expected.execution.completed_at = TimeWrite::Now;
            }
            assert_eq!(
                plan_fenced_result(
                    Some(&row),
                    Some(&view),
                    &id(1),
                    Some(&token(3)),
                    AttemptHistory::Unrecorded,
                    WorkflowOutcome::Success(status),
                    duration.is_some(),
                ),
                Ok(expected),
            );
        }
    }
}

#[test]
fn result_has_no_new_attempt_status_or_phase_start_guard() {
    let row = execution(LogicalExecutionStatus::Running);
    // Some tuples are unreachable under DB shape constraints. They characterize
    // the helper predicate only, not valid stored rows or a SQL admission proof.
    for status in ATTEMPT_STATES {
        let mut view = attempt(status);
        view.phase = AttemptPhase::Admission;
        assert_eq!(
            plan_fenced_result(
                Some(&row),
                Some(&view),
                &id(1),
                Some(&token(3)),
                AttemptHistory::Recorded,
                WorkflowOutcome::Success(LogicalExecutionStatus::Success),
                false,
            ),
            Ok(expected_result(
                LogicalExecutionStatus::Success,
                AttemptStatus::Succeeded
            )),
        );
    }
}

#[test]
fn result_rejects_exact_stale_fences_before_coordinator_policy() {
    let row = execution(LogicalExecutionStatus::Running);
    assert_eq!(
        plan_fenced_result(
            Some(&row),
            None,
            &id(1),
            Some(&token(3)),
            AttemptHistory::Recorded,
            WorkflowOutcome::CoordinatorLoss,
            false,
        ),
        Err(DecisionError::MissingAttempt)
    );
    let mut foreign = attempt(AttemptStatus::Running);
    foreign.execution_id = id(9);
    let mut missing = attempt(AttemptStatus::Running);
    missing.claim_token = None;
    let mut wrong = attempt(AttemptStatus::Running);
    wrong.claim_token = Some(token(9));
    let mut completed = attempt(AttemptStatus::Running);
    completed.completed_at_present = true;
    for view in [foreign, missing, wrong, completed] {
        assert_eq!(
            plan_fenced_result(
                Some(&row),
                Some(&view),
                &id(1),
                Some(&token(3)),
                AttemptHistory::Recorded,
                WorkflowOutcome::CoordinatorLoss,
                false,
            ),
            Err(DecisionError::InvalidAttemptFence)
        );
    }
}

#[test]
fn four_failure_mappings_assign_nullable_attempt_inputs_without_payload_clearing() {
    let row = execution(LogicalExecutionStatus::Running);
    let view = attempt(AttemptStatus::Claimed);
    let cases = [
        (
            RuntimeFailureKind::Timeout,
            LogicalExecutionStatus::Timeout,
            AttemptStatus::TimedOut,
            FailureCode::ExecutionTimeout,
            FailurePhase::Execution,
        ),
        (
            RuntimeFailureKind::Cancelled,
            LogicalExecutionStatus::Cancelled,
            AttemptStatus::Cancelled,
            FailureCode::Cancelled,
            FailurePhase::Cancellation,
        ),
        (
            RuntimeFailureKind::ResultPersistence,
            LogicalExecutionStatus::Failed,
            AttemptStatus::Failed,
            FailureCode::ResultPersistFailed,
            FailurePhase::Result,
        ),
        (
            RuntimeFailureKind::TenantCode,
            LogicalExecutionStatus::Failed,
            AttemptStatus::Failed,
            FailureCode::TenantCodeError,
            FailurePhase::Execution,
        ),
    ];
    for (kind, status, attempt_status, code, phase) in cases {
        let mut expected = expected_result(status, attempt_status);
        expected.attempt.failure_code = Some(code);
        expected.attempt.failure_phase = Some(phase);
        assert_eq!(
            plan_fenced_result(
                Some(&row),
                Some(&view),
                &id(1),
                Some(&token(3)),
                AttemptHistory::Recorded,
                WorkflowOutcome::Failure(kind),
                false,
            ),
            Ok(expected)
        );
    }
}

#[test]
fn coordinator_defers_running_but_cancelling_overrides_every_outcome() {
    let view = attempt(AttemptStatus::Claimed);
    let running = execution(LogicalExecutionStatus::Running);
    assert_eq!(
        plan_fenced_result(
            Some(&running),
            Some(&view),
            &id(1),
            Some(&token(3)),
            AttemptHistory::Recorded,
            WorkflowOutcome::CoordinatorLoss,
            false,
        ),
        Err(DecisionError::RequiresCoordinatorPolicy)
    );
    let cancelling = execution(LogicalExecutionStatus::Cancelling);
    let mut outcomes: Vec<WorkflowOutcome> = LOGICAL_STATES
        .into_iter()
        .map(WorkflowOutcome::Success)
        .collect();
    outcomes.extend([
        WorkflowOutcome::Failure(RuntimeFailureKind::Timeout),
        WorkflowOutcome::Failure(RuntimeFailureKind::Cancelled),
        WorkflowOutcome::Failure(RuntimeFailureKind::ResultPersistence),
        WorkflowOutcome::Failure(RuntimeFailureKind::TenantCode),
        WorkflowOutcome::CoordinatorLoss,
    ]);
    for outcome in outcomes {
        for duration in [None, Some(0)] {
            let mut expected = expected_cancelled_result();
            if duration.is_some() {
                expected.execution.duration_ms = InputWrite::SetSupplied;
                expected.execution.completed_at = TimeWrite::Now;
            }
            assert_eq!(
                plan_fenced_result(
                    Some(&cancelling),
                    Some(&view),
                    &id(1),
                    Some(&token(3)),
                    AttemptHistory::Recorded,
                    outcome,
                    duration.is_some(),
                ),
                Ok(expected)
            );
        }
    }
}

#[test]
fn queued_cancel_has_exact_optional_active_attempt_plan() {
    for status in [
        LogicalExecutionStatus::Scheduled,
        LogicalExecutionStatus::Pending,
    ] {
        let row = execution(status);
        let view = attempt(AttemptStatus::Published);
        let expected = CancelPlan {
            status: LogicalExecutionStatus::Cancelled,
            completed_at: TimeWrite::Now,
            attempt: Some(CancelAttemptPlan {
                status: AttemptStatus::Cancelled,
                phase: AttemptPhase::Terminal,
                failure_phase: FailurePhase::Cancellation,
                failure_code: FailureCode::CancelledBeforeClaim,
                completed_at: TimeWrite::Now,
                heartbeat_at: TimeWrite::Now,
            }),
        };
        assert_eq!(
            plan_cancel_state(Some(&row), Some(&view), &id(1)),
            Ok(expected)
        );
        let mut without_attempt = expected;
        without_attempt.attempt = None;
        assert_eq!(
            plan_cancel_state(Some(&row), None, &id(1)),
            Ok(without_attempt)
        );
        let mut completed = view.clone();
        completed.completed_at_present = true;
        completed.execution_id = id(9);
        assert_eq!(
            plan_cancel_state(Some(&row), Some(&completed), &id(1)),
            Ok(without_attempt)
        );
        let mut foreign = view;
        foreign.execution_id = id(9);
        assert_eq!(
            plan_cancel_state(Some(&row), Some(&foreign), &id(1),),
            Err(DecisionError::InconsistentRows)
        );
    }
}

#[test]
fn running_cancel_ignores_foreign_and_completed_attempts() {
    let row = execution(LogicalExecutionStatus::Running);
    let expected = CancelPlan {
        status: LogicalExecutionStatus::Cancelling,
        completed_at: TimeWrite::Keep,
        attempt: None,
    };
    let mut view = attempt(AttemptStatus::Running);
    view.execution_id = id(9);
    assert_eq!(plan_cancel_state(Some(&row), None, &id(1)), Ok(expected));
    assert_eq!(
        plan_cancel_state(Some(&row), Some(&view), &id(1)),
        Ok(expected)
    );
    view.completed_at_present = true;
    assert_eq!(
        plan_cancel_state(Some(&row), Some(&view), &id(1)),
        Ok(expected)
    );
}

#[test]
fn cancel_checks_logical_identity_and_rejects_other_states_before_attempt() {
    let mut foreign = attempt(AttemptStatus::Claimed);
    foreign.execution_id = id(9);
    assert_eq!(
        plan_cancel_state(None, Some(&foreign), &id(1),),
        Err(DecisionError::MissingExecution)
    );
    let row = execution(LogicalExecutionStatus::Pending);
    assert_eq!(
        plan_cancel_state(Some(&row), Some(&foreign), &id(9),),
        Err(DecisionError::MissingExecution)
    );
    for status in LOGICAL_STATES {
        if matches!(
            status,
            LogicalExecutionStatus::Scheduled
                | LogicalExecutionStatus::Pending
                | LogicalExecutionStatus::Running
        ) {
            continue;
        }
        let row = execution(status);
        assert_eq!(
            plan_cancel_state(Some(&row), Some(&foreign), &id(1),),
            Err(DecisionError::InvalidLogicalState)
        );
    }
}

#[test]
fn opaque_token_supports_clone_equality_without_secret_projection() {
    let original = token(3);
    assert!(original == original.clone());
    assert!(original != token(4));
}

fn expected_existing_claim() -> ClaimDecision {
    ClaimDecision::Plan(ExistingAttemptClaimPlan {
        logical_status: LogicalExecutionStatus::Running,
        attempt_status: AttemptStatus::Claimed,
        attempt_phase: AttemptPhase::Claim,
        claim_token: ClaimTokenWrite::SetParentNonNull,
        worker_id: InputWrite::SetSupplied,
        worker_incarnation_id: InputWrite::SetSupplied,
        claimed_at: TimeWrite::Now,
        heartbeat_at: TimeWrite::Now,
    })
}

#[test]
fn claim_skip_precedence_covers_all_logical_states_and_foreign_rows() {
    let mut malformed = attempt(AttemptStatus::Failed);
    malformed.execution_id = id(90);
    malformed.completed_at_present = true;
    assert_eq!(
        plan_existing_attempt_claim(None, Some(&malformed), &id(1)),
        Ok(ClaimDecision::DeferLegacyInline)
    );
    for status in LOGICAL_STATES {
        for foreign in [false, true] {
            let mut logical = execution(status);
            if foreign {
                logical.id = id(91);
            }
            let expected = if status != LogicalExecutionStatus::Pending {
                Ok(ClaimDecision::NoClaim)
            } else {
                Err(DecisionError::InconsistentRows)
            };
            assert_eq!(
                plan_existing_attempt_claim(Some(&logical), Some(&malformed), &id(1)),
                expected
            );
            if status != LogicalExecutionStatus::Pending {
                assert_eq!(
                    plan_existing_attempt_claim(Some(&logical), None, &id(1)),
                    Ok(ClaimDecision::NoClaim)
                );
            }
        }
    }
}

#[test]
fn claim_defers_allocation_only_after_pending_identity_and_active_selection() {
    let mut logical = execution(LogicalExecutionStatus::Pending);
    assert_eq!(
        plan_existing_attempt_claim(Some(&logical), None, &id(1)),
        Ok(ClaimDecision::DeferAttemptAllocation)
    );
    logical.id = id(91);
    assert_eq!(
        plan_existing_attempt_claim(Some(&logical), None, &id(1)),
        Err(DecisionError::InconsistentRows)
    );
    logical.id = id(1);
    let mut historical = attempt(AttemptStatus::Published);
    historical.claim_token = None;
    historical.completed_at_present = true;
    assert_eq!(
        plan_existing_attempt_claim(Some(&logical), Some(&historical), &id(1)),
        Err(DecisionError::InconsistentRows)
    );
    // A source-selected absence excludes completed history; allocation and MAX
    // over that history are deferred rather than reconstructed by this kernel.
    assert_eq!(
        plan_existing_attempt_claim(Some(&logical), None, &id(1)),
        Ok(ClaimDecision::DeferAttemptAllocation)
    );
}

#[test]
fn claim_checks_every_attempt_status_and_token_without_mutating_inputs() {
    let logical = execution(LogicalExecutionStatus::Pending);
    let logical_before = logical.clone();
    for status in ATTEMPT_STATES {
        for stored_token in [None, Some(token(3))] {
            let mut active = attempt(status);
            active.claim_token = stored_token;
            let before = active.clone();
            let expected = if status == AttemptStatus::Published && active.claim_token.is_none() {
                Ok(expected_existing_claim())
            } else {
                Err(DecisionError::InvalidAttemptState)
            };
            assert_eq!(
                plan_existing_attempt_claim(Some(&logical), Some(&active), &id(1)),
                expected
            );
            assert!(active == before);
            assert!(logical == logical_before);
            active.execution_id = id(90);
            assert_eq!(
                plan_existing_attempt_claim(Some(&logical), Some(&active), &id(1)),
                Err(DecisionError::InconsistentRows)
            );
        }
    }
}

#[test]
fn claim_keeps_odd_phases_and_start_history_and_sets_exact_directives() {
    let logical = execution(LogicalExecutionStatus::Pending);
    for phase in [
        AttemptPhase::Dispatch,
        AttemptPhase::Claim,
        AttemptPhase::Admission,
        AttemptPhase::Queue,
        AttemptPhase::Execution,
        AttemptPhase::Result,
        AttemptPhase::Terminal,
    ] {
        for started in [false, true] {
            let mut active = attempt(AttemptStatus::Published);
            active.claim_token = None;
            active.phase = phase;
            active.started_at_present = started;
            let before = active.clone();
            assert_eq!(
                plan_existing_attempt_claim(Some(&logical), Some(&active), &id(1)),
                Ok(expected_existing_claim())
            );
            assert!(active == before);
            active.completed_at_present = true;
            assert_eq!(
                plan_existing_attempt_claim(Some(&logical), Some(&active), &id(1)),
                Err(DecisionError::InconsistentRows)
            );
        }
    }
    // No worker values are inputs: SetSupplied applies equally to None (NULL)
    // and an empty supplied string. Both timestamps use the same future sample.
    let ClaimDecision::Plan(plan) = expected_existing_claim() else {
        panic!("expected claim plan");
    };
    assert_eq!(plan.worker_id, InputWrite::SetSupplied);
    assert_eq!(plan.worker_incarnation_id, InputWrite::SetSupplied);
    assert_eq!(plan.claimed_at, TimeWrite::Now);
    assert_eq!(plan.heartbeat_at, TimeWrite::Now);
}
