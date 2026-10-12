from src.services.execution import requirements_setup_helper
from src.services.execution.requirements_setup_result import RequirementsInstallResult


def test_status_matches_installed_distribution_names(monkeypatch):
    monkeypatch.setattr(
        "src.core.requirements_cache.get_requirements_sync",
        lambda: "uvicorn[standard]\nPyJWT[crypto]  # JWT tokens\naio-pika  # RabbitMQ\n",
    )
    monkeypatch.setattr(
        requirements_setup_helper,
        "_get_installed_packages",
        lambda: [{"name": name} for name in ("uvicorn", "PyJWT", "aio_pika")],
    )
    result = RequirementsInstallResult()

    requirements_setup_helper._update_requirements_status(result)

    assert result.requirements_total == 3
    assert result.requirements_installed == 3


def test_tab_comment_and_unparseable_line_do_not_abort_count(monkeypatch):
    monkeypatch.setattr(
        "src.core.requirements_cache.get_requirements_sync",
        lambda: "requests==2.28\t# tab comment\nnot-a-valid!!!\nDjango>=3\n",
    )
    monkeypatch.setattr(
        requirements_setup_helper,
        "_get_installed_packages",
        lambda: [{"name": name} for name in ("requests", "Django")],
    )
    result = RequirementsInstallResult()

    requirements_setup_helper._update_requirements_status(result)

    # The garbage line has no verifiable package name, so it is counted in
    # the total but never as installed (unknown/incomplete, not false-healthy).
    assert result.requirements_total == 3
    assert result.requirements_installed == 2


def test_pip_vcs_requirement_matched_via_egg_fragment(monkeypatch):
    monkeypatch.setattr(
        "src.core.requirements_cache.get_requirements_sync",
        lambda: "git+https://github.com/example/pkg.git#egg=sample\nrequests==2.28\n",
    )
    monkeypatch.setattr(
        requirements_setup_helper,
        "_get_installed_packages",
        lambda: [{"name": name} for name in ("sample", "requests")],
    )
    result = RequirementsInstallResult()

    requirements_setup_helper._update_requirements_status(result)

    assert result.requirements_total == 2
    assert result.requirements_installed == 2


def test_pip_vcs_requirement_missing_install_reports_incomplete(monkeypatch):
    monkeypatch.setattr(
        "src.core.requirements_cache.get_requirements_sync",
        lambda: "git+https://github.com/example/pkg.git#egg=sample\n",
    )
    monkeypatch.setattr(
        requirements_setup_helper, "_get_installed_packages", lambda: []
    )
    result = RequirementsInstallResult()

    requirements_setup_helper._update_requirements_status(result)

    assert result.requirements_total == 1
    assert result.requirements_installed == 0
