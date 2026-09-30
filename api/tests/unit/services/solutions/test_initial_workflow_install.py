"""The initial installer rejects any pre-existing Solution-owned state."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.events import EventSource, EventSubscription
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.models.orm.forms import Form
from src.models.orm.pending_capture import PendingCaptureORM
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.services import ServiceDefinition
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.solutions.initial_workflow_install import (
    InitialWorkflowInstallService,
)
from src.services.solutions.source_revision import SolutionSourceRevisionConflict

OWNED_ENTITY_MODELS = [
    Workflow, Agent, Application, CustomClaim, EventSource, EventSubscription,
    FileMetadata, FilePolicy, Form, PendingCaptureORM, PolicyRule,
    ServiceDefinition, SolutionConfigSchema, SolutionConnectionSchema,
    SolutionFileLocation, Table,
]


class GuardSession:
    def __init__(self, solution, occupied_model=None):
        self.solution = solution
        self.occupied_model = occupied_model

    async def scalar(self, statement):
        model = statement.column_descriptions[0]["entity"]
        if model is Solution:
            return self.solution
        if model is self.occupied_model:
            return uuid4()
        return None


@pytest.mark.parametrize("model", OWNED_ENTITY_MODELS)
@pytest.mark.asyncio
async def test_initial_install_rejects_each_owned_entity_model(model):
    solution_id = uuid4()
    solution = SimpleNamespace(
        id=solution_id, status="active", setup_complete=True,
        allow_outbound_access=False, git_connected=False,
        active_deployment_id=None, execution_runtime_mode="repo-v1",
    )
    service = InitialWorkflowInstallService(GuardSession(solution, model))

    with pytest.raises(SolutionSourceRevisionConflict):
        await service._require_empty_solution(solution_id, lock=False)


@pytest.mark.asyncio
async def test_initial_install_rejects_prior_deployment_history():
    solution_id = uuid4()
    solution = SimpleNamespace(
        id=solution_id, status="active", setup_complete=True,
        allow_outbound_access=False, git_connected=False,
        active_deployment_id=None, execution_runtime_mode="repo-v1",
    )
    service = InitialWorkflowInstallService(GuardSession(solution, SolutionDeployment))

    with pytest.raises(SolutionSourceRevisionConflict, match="deployment history"):
        await service._require_empty_solution(solution_id, lock=False)

