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
