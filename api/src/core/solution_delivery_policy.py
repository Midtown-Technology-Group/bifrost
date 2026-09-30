"""Explicit trust configuration for protected-Git Solution delivery."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def delivery_path(value: str) -> str:
    if (not value or value.startswith("/") or "\\" in value or ":" in value
            or "\0" in value or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Expected a normalized repository-relative delivery path")
    return value


class SolutionGitDeliveryPolicy(BaseModel):
    """A producer may deliver only these exact installed Solution recipes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    repository: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
    repository_id: int = Field(gt=0)
    repository_owner_id: int = Field(gt=0)
    organization_id: UUID
    workflow_path: str
    ci_workflow_path: str
    ci_workflow_id: int = Field(gt=0)
    solutions: dict[UUID, str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_paths(self):
        for path in (self.workflow_path, self.ci_workflow_path):
            delivery_path(path)
            if not path.startswith(".github/workflows/") or not path.endswith((".yml", ".yaml")):
                raise ValueError("Delivery and CI workflows must be pinned workflow paths")
        for path in self.solutions.values():
            delivery_path(path)
            if not path.startswith("config/solution-delivery/") or not path.endswith(".json"):
                raise ValueError("Installed Solution recipes must be reviewed delivery JSON")
        return self
