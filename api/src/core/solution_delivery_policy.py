"""Explicit trust configuration for protected-Git Solution delivery."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def delivery_path(value: str) -> str:
    if (not value or value.startswith("/") or "\\" in value or ":" in value
            or "\0" in value or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Expected a normalized repository-relative delivery path")
    return value


def reviewed_package_registry(value: object) -> list[dict[str, str]]:
    """Validate three adapter namespaces without borrowing their authority."""
    if (not isinstance(value, dict) or set(value) != {"schema_version", "installations"}
            or value["schema_version"] not in {"bifrost.solution-delivery-installations/v1",
                "bifrost.package-delivery-installations/v1"}
            or not isinstance(value["installations"], list)
            or not 1 <= len(value["installations"]) <= 100):
        raise ValueError("Invalid package registry")
    tagged = value["schema_version"] == "bifrost.package-delivery-installations/v1"
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in value["installations"]:
        if (not isinstance(row, dict) or set(row) != ({"kind", "target", "recipe"} if tagged else {"target", "recipe"})
                or row["target"] not in {"production", "canary"}
                or row.get("kind", "solution") not in {"solution", "solution_package", "inline_app"}
                or not isinstance(row["recipe"], str)):
            raise ValueError("Invalid package registry entry")
        kind, path = row.get("kind", "solution"), delivery_path(row["recipe"])
        prefix = {
            "solution": "config/solution-delivery/",
            "solution_package": "config/solution-package-delivery/",
            "inline_app": "config/app-delivery/",
        }[kind]
        if not path.startswith(prefix) or not path.endswith(".json") or path == "config/solution-delivery/installations.json":
            raise ValueError("Package recipe is outside its adapter namespace")
        key = row["target"], path
        if key in seen:
            raise ValueError("Duplicate package registry entry")
        seen.add(key)
        result.append({"kind": kind, "target": row["target"], "recipe": path})
    return result


class ProtectedGitRepositoryPolicy(BaseModel):
    """Pinned repository and producer identity shared by package adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    repository: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
    repository_id: int = Field(gt=0)
    repository_owner_id: int = Field(gt=0)
    organization_id: UUID
    workflow_path: str
    ci_workflow_path: str
    ci_workflow_id: int = Field(gt=0)
    @model_validator(mode="after")
    def validate_workflows(self):
        for path in (self.workflow_path, self.ci_workflow_path):
            delivery_path(path)
            if not path.startswith(".github/workflows/") or not path.endswith((".yml", ".yaml")):
                raise ValueError("Delivery and CI workflows must be pinned workflow paths")
        return self


class SolutionGitDeliveryPolicy(ProtectedGitRepositoryPolicy):
    """A producer may deliver only these exact installed Solution recipes."""

    solutions: dict[UUID, str] = Field(min_length=1, max_length=100)
    solution_organization_ids: dict[UUID, UUID | None] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_paths(self):
        for path in self.solutions.values():
            delivery_path(path)
            if not path.startswith("config/solution-delivery/") or not path.endswith(".json"):
                raise ValueError("Installed Solution recipes must be reviewed delivery JSON")
        if (self.solution_organization_ids is not None
                and set(self.solution_organization_ids) != set(self.solutions)):
            raise ValueError("Explicit organization scopes must cover exactly the Solution allowlist")
        return self

    def organization_id_for(self, solution_id: UUID) -> UUID | None:
        """Resolve one exact install scope; an unknown install never means Global."""
        if solution_id not in self.solutions:
            raise ValueError("Solution is outside the delivery allowlist")
        if self.solution_organization_ids is None:
            return self.organization_id
        return self.solution_organization_ids[solution_id]
