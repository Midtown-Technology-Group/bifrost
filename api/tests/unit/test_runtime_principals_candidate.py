"""Static qualification-profile boundaries, not effective ACL or SQL proof."""

import json
from pathlib import Path

PROFILE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "alembic/candidates/runtime_principals_v1.json"
    ).read_text()
)


def test_proposal_cannot_be_mistaken_for_role_activation() -> None:
    assert PROFILE["schema"] == "bifrost.common-sql-qualification-proposal/v1"
    assert PROFILE["status"] == "review-only-unapproved"
    assert PROFILE["database"] == "bifrost_test"
    assert set(PROFILE["roles"]) == {
        "wex_core",
        "wex_incumbent",
        "runtime_lifecycle_guard",
        "runtime_artifact_lock_guard",
    }
    for role in PROFILE["roles"].values():
        assert role["memberships"] == []
        assert role["owns_native_relations"] == []
        for flag in (
            "inherit",
            "superuser",
            "create_role",
            "create_database",
            "replication",
            "bypass_rls",
        ):
            assert role[flag] is False
        for collection in ("table_acl", "column_acl", "sequence_acl", "function_acl"):
            assert all(
                name.startswith("public.") and "*" not in name
                for name in role[collection]
            )
    assert PROFILE["held_decisions"] and PROFILE["required_catalog_evidence"]


def test_artifact_locker_is_the_only_proposed_user_key_update_authority() -> None:
    roles = PROFILE["roles"]
    locker = roles["runtime_artifact_lock_guard"]
    assert locker["can_login_after_explicit_activation"] is False
    assert locker["owns_functions"] == ["public.guard_runtime_artifact_identity()"]
    assert locker["table_acl"] == locker["sequence_acl"] == locker["function_acl"] == {}
    assert set(locker["column_acl"]) == {
        "public.users",
        "public.workflows",
        "public.solutions",
        "public.solution_deployments",
    }
    assert all(acl["UPDATE"] == ["id"] for acl in locker["column_acl"].values())
    assert locker["column_acl"]["public.users"]["SELECT"] == ["id"]
    # Incumbent retains its native Python administration authority; the new
    # coordinator and read-only guard receive no user write privileges.
    core = roles["wex_core"]
    assert "public.users" not in core["table_acl"]
    assert "public.users" not in core["column_acl"]
    assert core["owns_functions"] == []


def test_connection_and_schema_access_do_not_depend_on_ambient_public_grants() -> None:
    for role in PROFILE["roles"].values():
        assert role["schema_acl"] == {"public": ["USAGE"]}
        expected = (
            {"bifrost_test": ["CONNECT"]}
            if role["can_login_after_explicit_activation"]
            else {}
        )
        assert role["database_acl"] == expected


def test_lifecycle_custodian_stays_read_only_with_full_session_row_access() -> None:
    guard = PROFILE["roles"]["runtime_lifecycle_guard"]
    assert guard["can_login_after_explicit_activation"] is False
    assert guard["table_acl"] == {"public.runtime_sessions": ["SELECT"]}
    assert all(set(acl) == {"SELECT"} for acl in guard["column_acl"].values())
    assert guard["sequence_acl"] == guard["function_acl"] == {}
    assert len(guard["owns_functions"]) == 9


def test_rust_row_locks_have_finite_update_privilege_without_source_user_update() -> (
    None
):
    core = PROFILE["roles"]["wex_core"]
    for table in PROFILE["rust_consumer"]["required_for_update_relations"]:
        assert "UPDATE" in core["table_acl"].get(table, []) or core["column_acl"].get(
            table, {}
        ).get("UPDATE")
    for table in (
        "public.users",
        "public.workflows",
        "public.solutions",
        "public.solution_deployments",
    ):
        assert "UPDATE" not in core["table_acl"].get(table, [])
        assert not core["column_acl"].get(table, {}).get("UPDATE")
    assert core["column_acl"]["public.runtime_starts"]["UPDATE"] == ["id"]
    assert core["column_acl"]["public.runtime_admissions"]["UPDATE"] == ["id"]
    assert core["column_acl"]["public.runtime_report_receipts"]["UPDATE"] == [
        "result_message_id"
    ]


def test_sequences_are_usage_only_and_incumbent_has_no_direct_common_authority() -> (
    None
):
    roles = PROFILE["roles"]
    expected_sequences = {
        "public.execution_logs_id_seq": ["USAGE"],
        "public.ai_usage_id_seq": ["USAGE"],
    }
    for name in ("wex_core", "wex_incumbent"):
        assert roles[name]["sequence_acl"] == expected_sequences
    incumbent = roles["wex_incumbent"]
    common = {
        name
        for name in roles["wex_core"]["table_acl"]
        if name.startswith(("public.runtime_", "public.workflow_runtime_sdk_"))
    }
    assert common.isdisjoint(incumbent["table_acl"])
    assert common.isdisjoint(incumbent["column_acl"])
    assert incumbent["function_acl"] == {
        "public.runtime_undrained_sources()": ["EXECUTE"]
    }
