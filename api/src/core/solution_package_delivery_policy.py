"""Explicit enrollment for complete Solution packages containing V2 Apps."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.solution_delivery_policy import ProtectedGitRepositoryPolicy


class SolutionPackageGitEnrollment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    organization_id: UUID | None
    repo_subpath: str = Field(pattern=r"^solutions/[a-z0-9]+(?:-[a-z0-9]+)*$")
    recipe_path: str = Field(
        pattern=r"^config/solution-package-delivery/[a-z0-9]+(?:-[a-z0-9]+)*\.json$"
    )


class SolutionPackageGitDeliveryPolicy(ProtectedGitRepositoryPolicy):
    """Source enrollment grants no role, resource or publication permission."""

    packages: dict[UUID, SolutionPackageGitEnrollment] = Field(
        min_length=1, max_length=100
    )

    @model_validator(mode="after")
    def unique_targets(self):
        for field in ("repo_subpath", "recipe_path"):
            values = [getattr(item, field) for item in self.packages.values()]
            if len(values) != len(set(values)):
                raise ValueError("Each package subtree/recipe must have one target")
        return self

    def enrollment_for(self, solution_id: UUID) -> SolutionPackageGitEnrollment:
        if solution_id not in self.packages:
            raise ValueError("Solution package is outside the delivery allowlist")
        return self.packages[solution_id]
