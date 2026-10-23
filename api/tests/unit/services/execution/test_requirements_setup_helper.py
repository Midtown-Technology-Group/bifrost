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


def test_setup_status_keeps_counting_packages_with_install_directives(monkeypatch):
    result = RequirementsInstallResult()
    monkeypatch.setattr(
        "src.services.execution.simple_worker.install_requirements",
        lambda: result,
    )
    monkeypatch.setattr(
        "src.core.requirements_cache.get_requirements_sync",
        lambda: (
            "missing-package==1\n"
            "-e git+https://example.test/editable.git@v1#egg=editable\n"
            "-r /workspace/policy.txt\n"
        ),
    )
    monkeypatch.setattr(
        requirements_setup_helper,
        "_get_installed_packages",
        lambda: [{"name": "unrelated"}],
    )

    observed = requirements_setup_helper.run_requirements_setup()

    assert observed is result
    assert observed.requirements_total == 1
    assert observed.requirements_installed == 0
