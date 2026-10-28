"""Env parsing for an opted-in static launcher (#1105)."""

from __future__ import annotations

import pytest

from src.worker.remote.static_lifecycle import (
    StaticLauncherConfigError,
    static_launcher_policy,
)


def test_unset_static_variables_leave_the_launcher_always_on() -> None:
    policy = static_launcher_policy({}, form="static", version="1.2.3")

    assert policy.version == "1.2.3"
    assert policy.drain_when_outdated is False
    assert policy.exit_when_idle_seconds is None
    assert policy.max_runs is None


def test_static_opt_in_reads_the_launcher_environment() -> None:
    policy = static_launcher_policy(
        {
            "BIFROST_STATIC_DRAIN_WHEN_OUTDATED": "true",
            "BIFROST_STATIC_EXIT_WHEN_IDLE_SECONDS": "0",
            "BIFROST_STATIC_MAX_RUNS": "4",
        },
        form="static",
        version="launcher-9",
    )

    assert policy.drain_when_outdated is True
    assert policy.exit_when_idle_seconds == 0
    assert policy.max_runs == 4


def test_a_job_launcher_reports_its_build_and_ignores_static_limits() -> None:
    policy = static_launcher_policy(
        {
            "BIFROST_STATIC_DRAIN_WHEN_OUTDATED": "yes",
            "BIFROST_STATIC_EXIT_WHEN_IDLE_SECONDS": "15",
            "BIFROST_STATIC_MAX_RUNS": "1",
        },
        form="job",
        version="job-build",
    )

    assert policy.version == "job-build"
    assert policy.drain_when_outdated is False
    assert policy.exit_when_idle_seconds is None
    assert policy.max_runs is None


def test_invalid_static_limits_fail_closed() -> None:
    with pytest.raises(StaticLauncherConfigError, match="MAX_RUNS"):
        static_launcher_policy(
            {"BIFROST_STATIC_MAX_RUNS": "0"}, form="static", version="1"
        )
    with pytest.raises(StaticLauncherConfigError, match="IDLE"):
        static_launcher_policy(
            {"BIFROST_STATIC_EXIT_WHEN_IDLE_SECONDS": "-1"}, form="static", version="1"
        )
    with pytest.raises(StaticLauncherConfigError, match="DRAIN_WHEN_OUTDATED"):
        static_launcher_policy(
            {"BIFROST_STATIC_DRAIN_WHEN_OUTDATED": "sometimes"}, form="static", version="1"
        )
    with pytest.raises(StaticLauncherConfigError, match="BIFROST_VERSION"):
        static_launcher_policy({}, form="static", version="")
