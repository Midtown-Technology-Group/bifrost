"""Shared helpers for workflow tool parameter schemas and validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

import pydantic_core
from jsonschema import FormatChecker
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for


_TYPE_TO_JSON_SCHEMA: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "integer": {"type": "integer"},
    "float": {"type": "number"},
    "number": {"type": "number"},
    "bool": {"type": "boolean"},
    "boolean": {"type": "boolean"},
    "list": {"type": "array", "items": {"type": "string"}},
    "array": {"type": "array", "items": {"type": "string"}},
    "json": {"type": "object", "additionalProperties": True},
    "dict": {"type": "object", "additionalProperties": True},
    "object": {"type": "object", "additionalProperties": True},
}


def parameter_json_schema(parameter: Mapping[str, Any]) -> dict[str, Any]:
    """Build a JSON Schema fragment for one workflow parameter."""
    raw_schema = parameter.get("json_schema")
    schema = deepcopy(raw_schema) if isinstance(raw_schema, dict) else deepcopy(_TYPE_TO_JSON_SCHEMA.get(
        str(parameter.get("type", "string")).lower(),
        {"type": "string"},
    ))

    options = parameter.get("options")
    if not isinstance(raw_schema, dict) and "enum" not in schema and isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        enum_values = []
        for option in options:
            if isinstance(option, Mapping) and "value" in option:
                enum_values.append(deepcopy(option["value"]))
            elif isinstance(option, (str, int, float, bool)):
                enum_values.append(option)
        if enum_values:
            schema["enum"] = enum_values

    default_value = parameter.get("default_value")
    if default_value is not None and "default" not in schema:
        schema["default"] = deepcopy(default_value)

    return schema


def validate_arguments_against_schema(
    schema: dict[str, Any],
    arguments: dict[str, Any],
    *,
    max_issues: int = 10,
) -> tuple[list[dict[str, Any]], str | None]:
    """Validate arguments against a JSON Schema and return structured issues.

    Returns:
        (issues, schema_error) where `schema_error` is set only when the
        schema itself is invalid and therefore cannot be used for validation.
    """
    try:
        validator_class = validator_for(schema)
        validator_class.check_schema(schema)
        validator = validator_class(schema, format_checker=FormatChecker())
    except SchemaError as exc:
        return [], exc.message

    errors = sorted(
        validator.iter_errors(arguments),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if not errors:
        return [], None

    issues = [
        {
            "path": _json_pointer(error.absolute_path),
            "message": error.message,
            "validator": error.validator,
            "expected": pydantic_core.to_jsonable_python(
                error.validator_value,
                fallback=str,
            ),
        }
        for error in errors[:max_issues]
    ]
    return issues, None


def _json_pointer(path: Any) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(parts) if parts else "/"
