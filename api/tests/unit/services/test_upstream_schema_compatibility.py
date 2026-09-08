"""Validation contracts shared by indexed source, Solution bundles and runtime tools."""
from unittest.mock import MagicMock

import pytest
from jsonschema import Draft202012Validator

from src.services.execution.type_inference import extract_parameters_from_signature
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.tool_registry import workflow_parameters_to_json_schema
from src.services.tool_schema import parameter_json_schema

SOURCE = '''
from typing import Any, Literal
from enum import IntEnum
class Priority(IntEnum):
    LOW = 1
    HIGH = 2

def run(payload: Any, nullable: str | None, nested: dict[str, list[int | None]],
        choice: Literal[1, "one", None], priority: Priority, *, count: int,
        optional: int | None = None):
    pass
'''


@pytest.fixture
def schemas():
    namespace = {}
    exec(SOURCE, namespace)
    indexed = WorkflowIndexer(MagicMock()).extract_parameters_from_source(SOURCE, "run")
    runtime = workflow_parameters_to_json_schema(extract_parameters_from_signature(namespace["run"]))
    return indexed, runtime


@pytest.mark.parametrize("change,valid", [
    ({}, True),
    ({"payload": [1, None, {"arbitrary": True}]}, True),
    ({"nullable": "value", "choice": "one", "optional": 3}, True),
    ({"priority": "1"}, False),
    ({"nested": {"key": ["bad"]}}, False),
    ({"count": None}, False),
    ({"unknown": 1}, False),
    ({"choice": "invalid"}, False),
])
def test_ast_runtime_validate_the_same_arguments(schemas, change, valid):
    arguments = {"payload": None, "nullable": None, "nested": {"key": [1, None]},
                 "choice": None, "priority": 1, "count": 2}
    arguments.update(change)
    for schema in schemas:
        Draft202012Validator.check_schema(schema)
        assert Draft202012Validator(schema).is_valid(arguments) is valid


@pytest.mark.parametrize("missing", ["nullable", "count", "payload"])
def test_nullable_and_keyword_only_arguments_still_required(schemas, missing):
    arguments = {"payload": None, "nullable": None, "nested": {}, "choice": 1, "priority": 1, "count": 2}
    del arguments[missing]
    for schema in schemas:
        assert not Draft202012Validator(schema).is_valid(arguments)


def test_legacy_empty_registration_is_distinct_from_explicit_zero_args():
    explicit = WorkflowIndexer(MagicMock()).extract_parameters_from_source("def run(): pass", "run")
    legacy = workflow_parameters_to_json_schema([], allow_unknown_when_empty=True)
    current = workflow_parameters_to_json_schema(explicit, allow_unknown_when_empty=True)
    assert Draft202012Validator(legacy).is_valid({"legacy_argument": 1})
    assert not Draft202012Validator(current).is_valid({"legacy_argument": 1})


def test_full_schema_is_preserved_and_detached():
    stored = {"$defs": {"value": {"type": "integer"}}, "type": "object",
              "properties": {"value": {"$ref": "#/$defs/value"}},
              "required": ["value"], "additionalProperties": False}
    schema = workflow_parameters_to_json_schema(stored)
    assert schema == stored
    assert Draft202012Validator(schema).is_valid({"value": 1})
    schema["$defs"]["value"]["type"] = "string"
    assert stored["$defs"]["value"]["type"] == "integer"


def test_explicit_unconstrained_schema_does_not_inherit_ui_enum():
    assert parameter_json_schema({"json_schema": {}, "options": [{"value": "old"}]}) == {}


def test_missing_or_invalid_carried_source_does_not_invent_zero_argument_schema():
    indexer = WorkflowIndexer(MagicMock())
    assert indexer.extract_parameters_from_source("def other(): pass", "run") is None
    assert indexer.extract_parameters_from_source("def run(:", "run") is None


def test_unresolved_imported_type_is_not_falsely_restricted_to_objects():
    schema = WorkflowIndexer(MagicMock()).extract_parameters_from_source(
        "from external import Identifier\ndef run(value: Identifier): pass", "run"
    )
    assert Draft202012Validator(schema).is_valid({"value": "external-id"})


def test_annotated_metadata_does_not_erase_the_runtime_signature():
    from typing import Annotated

    def run(value: Annotated[int, {"minimum": 1}], other: str):
        pass

    schema = workflow_parameters_to_json_schema(extract_parameters_from_signature(run))
    assert schema["required"] == ["value", "other"]
    assert schema["properties"]["value"]["type"] == "integer"


def test_legacy_mutable_values_are_detached():
    record = {"type": "json", "default_value": {"x": []}, "options": [{"value": {"x": 1}}]}
    schema = parameter_json_schema(record)
    schema["default"]["x"].append(1)
    schema["enum"][0]["x"] = 2
    assert record["default_value"] == {"x": []}
    assert record["options"][0]["value"] == {"x": 1}
    first = parameter_json_schema({"type": "list"})
    first["items"]["type"] = "integer"
    assert parameter_json_schema({"type": "list"})["items"]["type"] == "string"


def test_enum_private_member_and_variadic_tuple_parity():
    source = """
from enum import Enum
class Choice(Enum):
    A = 1
    _B = 2

def run(value: Choice, items: tuple[int, ...], context: str): pass
"""
    namespace = {}
    exec(source, namespace)
    schemas = [WorkflowIndexer(MagicMock()).extract_parameters_from_source(source, "run"),
               workflow_parameters_to_json_schema(extract_parameters_from_signature(namespace["run"]))]
    for schema in schemas:
        assert Draft202012Validator(schema).is_valid({"value": 2, "items": [1, 2, 3], "context": "text"})
        assert not Draft202012Validator(schema).is_valid({"value": 2, "items": ["bad"], "context": "text"})


def test_openapi_legacy_any_keeps_unconstrained_schema():
    from src.services.openapi_endpoints import _param_to_openapi_schema

    assert _param_to_openapi_schema({"name": "value", "type": "json", "json_schema": {}}) == {}
