"""Minimal CLI-side mirror of event source / subscription DTOs."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from bifrost.contracts.enums import EventSourceType


class WebhookSourceConfig(BaseModel):
    """Webhook-specific configuration (CLI mirror)."""

    adapter_name: str | None = Field(default=None)
    integration_id: UUID | None = Field(default=None)
    config: dict[str, Any] = Field(default_factory=dict)


class ScheduleSourceConfig(BaseModel):
    """Schedule-specific configuration (CLI mirror)."""

    cron_expression: str
    timezone: str = Field(default="UTC")
    enabled: bool = Field(default=True)


class EventSourceCreate(BaseModel):
    """Request model for creating an event source (CLI mirror)."""

    name: str = Field(min_length=1, max_length=255)
    source_type: EventSourceType
    organization_id: UUID | None = Field(default=None)
    event_type: str | None = Field(default=None, max_length=100)
    webhook: WebhookSourceConfig | None = Field(default=None)
    schedule: ScheduleSourceConfig | None = Field(default=None)


class EventSourceUpdate(BaseModel):
    """Request model for updating an event source (CLI mirror)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = Field(default=None)
    organization_id: UUID | None = Field(default=None)
    webhook: WebhookSourceConfig | None = Field(default=None)
    schedule: ScheduleSourceConfig | None = Field(default=None)


class EventCriteriaOperator(str, Enum):
    """Bounded operators supported by event-subscription criteria v1."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"


class EventCriteriaCondition(BaseModel):
    """One comparison against the normalized event envelope."""

    kind: Literal["condition"] = "condition"
    field: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Dot-separated field rooted at event or schedule, for example "
            "event.body.ticket.priority"
        ),
    )
    operator: EventCriteriaOperator
    value: Any = Field(
        default=None,
        description="Comparison value; omitted for exists and not_exists",
    )

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        parts = value.split(".")
        if parts[0] not in {"event", "schedule"}:
            raise ValueError("criteria field must be rooted at event or schedule")
        if len(parts) < 2 or len(parts) > 10:
            raise ValueError("criteria field must contain between 2 and 10 segments")
        for part in parts:
            if not part or len(part) > 64:
                raise ValueError("criteria field segments must contain 1 to 64 characters")
            if not (part[0].isalpha() or part[0] == "_"):
                raise ValueError("criteria field segments must start with a letter or underscore")
            if not all(character.isalnum() or character in {"_", "-"} for character in part):
                raise ValueError("criteria field segments may contain letters, digits, underscores, and hyphens")
        return value

    @model_validator(mode="after")
    def validate_operator_value(self) -> "EventCriteriaCondition":
        has_value = "value" in self.model_fields_set
        if self.operator in {
            EventCriteriaOperator.EXISTS,
            EventCriteriaOperator.NOT_EXISTS,
        }:
            if has_value:
                raise ValueError(f"{self.operator.value} does not accept a value")
            return self

        if not has_value:
            raise ValueError(f"{self.operator.value} requires a value")

        if self.operator in {EventCriteriaOperator.IN, EventCriteriaOperator.NOT_IN}:
            if not isinstance(self.value, list) or not self.value or len(self.value) > 100:
                raise ValueError(f"{self.operator.value} requires a list of 1 to 100 scalar values")
            if any(not _is_criteria_scalar(item) for item in self.value):
                raise ValueError(f"{self.operator.value} accepts scalar list values only")
        elif self.operator in {
            EventCriteriaOperator.CONTAINS,
            EventCriteriaOperator.STARTS_WITH,
            EventCriteriaOperator.ENDS_WITH,
        }:
            if not isinstance(self.value, str):
                raise ValueError(f"{self.operator.value} requires a string value")
        elif self.operator in {
            EventCriteriaOperator.GREATER_THAN,
            EventCriteriaOperator.GREATER_THAN_OR_EQUAL,
            EventCriteriaOperator.LESS_THAN,
            EventCriteriaOperator.LESS_THAN_OR_EQUAL,
        }:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError(f"{self.operator.value} requires a numeric value")
        elif not _is_criteria_scalar(self.value):
            raise ValueError(f"{self.operator.value} accepts scalar values only")
        return self


class EventCriteriaGroup(BaseModel):
    """Boolean composition of event criteria nodes."""

    kind: Literal["all", "any", "not"]
    items: list["EventCriteriaNode"] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_not_arity(self) -> "EventCriteriaGroup":
        if self.kind == "not" and len(self.items) != 1:
            raise ValueError("not criteria groups must contain exactly one item")
        return self


EventCriteriaNode: TypeAlias = EventCriteriaCondition | EventCriteriaGroup


class EventCriteria(BaseModel):
    """Versioned, bounded criteria for deciding whether a subscription matches."""

    version: Literal[1] = 1
    root: EventCriteriaNode

    @model_validator(mode="after")
    def validate_complexity(self) -> "EventCriteria":
        node_count = 0

        def visit(node: EventCriteriaNode, depth: int) -> None:
            nonlocal node_count
            node_count += 1
            if depth > 5:
                raise ValueError("criteria nesting may not exceed 5 levels")
            if node_count > 50:
                raise ValueError("criteria may not contain more than 50 nodes")
            if isinstance(node, EventCriteriaGroup):
                for item in node.items:
                    visit(item, depth + 1)

        visit(self.root, 1)
        return self


def _is_criteria_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


EventCriteriaGroup.model_rebuild()


class EventSubscriptionCreate(BaseModel):
    """Request model for creating an event subscription (CLI mirror)."""

    target_type: str = Field(default="workflow")
    workflow_id: UUID | None = Field(default=None)
    agent_id: UUID | None = Field(default=None)
    event_type: str | None = Field(default=None, max_length=255)
    criteria: EventCriteria | None = Field(default=None)
    input_mapping: dict[str, Any] | None = Field(default=None)


class EventSubscriptionUpdate(BaseModel):
    """Request model for updating an event subscription (CLI mirror)."""

    event_type: str | None = Field(default=None, max_length=255)
    criteria: EventCriteria | None = Field(default=None)
    is_active: bool | None = Field(default=None)
    input_mapping: dict[str, Any] | None = Field(default=None)
