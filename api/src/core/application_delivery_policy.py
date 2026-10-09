"""Reviewed enrollment for publishing existing inline Apps from protected Git."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.solution_delivery_policy import ProtectedGitRepositoryPolicy


class InlineAppGitEnrollment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    organization_id: UUID | None
    repo_subpath: str = Field(pattern=r"^apps/[a-z0-9][a-z0-9-]*$")


class InlineAppGitDeliveryPolicy(ProtectedGitRepositoryPolicy):
    """Enrollment grants no new App creation, control or SDK deployment authority."""

    applications: dict[UUID, InlineAppGitEnrollment] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_source_targets(self):
        paths = [item.repo_subpath for item in self.applications.values()]
        if len(paths) != len(set(paths)):
            raise ValueError("Each authored App subtree must have one enrolled target")
        return self

    def enrollment_for(self, application_id: UUID) -> InlineAppGitEnrollment:
        if application_id not in self.applications:
            raise ValueError("App is outside the protected Git publication allowlist")
        return self.applications[application_id]
