"""Server exports of the canonical downloadable recipe compiler."""

from dataclasses import asdict

from bifrost.solution_delivery_review import (
    WORKFLOW_RECIPE_SCHEMA as WORKFLOW_RECIPE_SCHEMA,
    WORKFLOW_REVISION_MARKER as WORKFLOW_REVISION_MARKER,
    ReviewedRuntimeBounds as ReviewedRuntimeBounds,
    ReviewedWorkflowRecipe as ReviewedWorkflowRecipe,
    ReviewedWorkflowRegistration as ReviewedWorkflowRegistration,
    WorkflowRecipeError as WorkflowRecipeError,
    WorkflowRegistrationControls as WorkflowRegistrationControls,
    compile_workflow_registrations as _compile,
)
from bifrost.workflow_parameters import WorkflowParameterCompiler
from src.services.solutions.deployment_manifest import RuntimeEntityDefinition


def compile_workflow_registrations(recipe: ReviewedWorkflowRecipe, files: dict[str, bytes],
                                   indexer: WorkflowParameterCompiler) -> dict[str, RuntimeEntityDefinition]:
    return {ref: RuntimeEntityDefinition.model_validate(asdict(item))
            for ref, item in _compile(recipe, files, indexer).items()}
