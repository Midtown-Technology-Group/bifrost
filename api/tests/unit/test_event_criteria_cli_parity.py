"""Standalone event criteria retain validation and CLI object transport."""
import json

import click
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from bifrost.commands.events import _criteria_flag, subscribe, update_subscription
from bifrost.contracts.events import EventCriteria as CLICriteria
from src.models.contracts.events import EventCriteria as ServerCriteria


def _condition(**overrides):
    return {"kind": "condition", "field": "event.body.priority",
            "operator": "equals", "value": "critical", **overrides}


@pytest.mark.parametrize("root", [
    _condition(),
    {"kind": "all", "items": [_condition(), _condition(field="schedule.enabled", value=True)]},
    {"kind": "condition", "field": "event.body.id", "operator": "exists"},
])
def test_valid_criteria_have_identical_wire_shape(root):
    payload = {"version": 1, "root": root}
    assert CLICriteria.model_validate(payload).model_dump(mode="json", exclude_unset=True) == ServerCriteria.model_validate(payload).model_dump(mode="json", exclude_unset=True)


@pytest.mark.parametrize("root", [
    _condition(field="credentials.secret"),
    _condition(operator="exists"),
    _condition(operator="greater_than", value=True),
    _condition(operator="in", value=[]),
    _condition(value={"nested": "object"}),
    {"kind": "not", "items": [_condition(), _condition()]},
    {"kind": "all", "items": [_condition()] * 50},
])
def test_cli_refuses_the_same_malformed_criteria_as_server(root):
    for model in (CLICriteria, ServerCriteria):
        with pytest.raises(ValidationError):
            model.model_validate({"root": root})


def test_subscription_commands_expose_one_criteria_flag():
    for command in (subscribe, update_subscription):
        assert [p.name for p in command.params].count("criteria") == 1


@pytest.mark.parametrize("from_file", [False, True])
def test_cli_passes_json_object_for_criteria(tmp_path, from_file):
    payload = {"version": 1, "root": _condition()}
    seen = []

    @click.command()
    @_criteria_flag
    def command(criteria):
        seen.append(criteria)

    value = json.dumps(payload)
    if from_file:
        path = tmp_path / "criteria.json"
        path.write_text(value)
        value = "@" + str(path)
    result = CliRunner().invoke(command, ["--criteria", value])
    assert result.exit_code == 0, result.output
    assert seen == [payload]


def test_cli_refuses_non_object_criteria():
    @click.command()
    @_criteria_flag
    def command(criteria):
        raise AssertionError("invalid criteria dispatched")

    result = CliRunner().invoke(command, ["--criteria", "[]"])
    assert result.exit_code == 2
