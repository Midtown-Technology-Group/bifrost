from src.services.solutions.deploy_job_lifecycle import failure_projection_status


def test_connected_first_deploy_failure_stays_nonterminal_until_rollback():
    assert failure_projection_status(allow_connected_install=True) == "running"
    assert (
        failure_projection_status(
            allow_connected_install=True,
            rollback_committed=True,
        )
        == "failed"
    )


def test_ordinary_deploy_failure_is_terminal():
    assert failure_projection_status(allow_connected_install=False) == "failed"
