"""Canonical static workflow parameter compiler; never imports carried code."""

import ast
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowParameterCompiler:
    def extract_parameters_from_source(
        self, source: str | bytes, function_name: str, *, path: str = "<workflow>"
    ) -> dict[str, Any] | None:
        """Infer a carried source contract without registering or writing rows."""
        content = source.decode("utf-8", errors="replace") if isinstance(source, bytes) else source
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError:
            logger.warning("Cannot infer workflow parameters from invalid Python")
            return None
        enums = self._collect_enum_definitions(tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                return self._extract_parameters_from_ast(node, enum_definitions=enums)
        return None

    def _collect_enum_definitions(self, tree: ast.Module) -> dict[str, ast.AST]:
        """Resolve only complete, literal local enums; dynamic enums stay unknown."""
        definitions: dict[str, ast.AST] = {}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not any(
                self._annotation_base_name(base) in {"Enum", "IntEnum", "StrEnum"}
                for base in node.bases
            ):
                continue
            values: list[ast.expr] = []
            complete = True
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    targets = statement.targets
                    value = statement.value
                elif isinstance(statement, ast.AnnAssign):
                    targets = [statement.target]
                    value = statement.value
                else:
                    continue
                if any(isinstance(target, ast.Name) and target.id == "_ignore_" for target in targets):
                    complete = False
                    break
                if not any(isinstance(target, ast.Name) and not (target.id.startswith("_") and target.id.endswith("_")) for target in targets):
                    continue
                try:
                    literal = ast.literal_eval(value) if value is not None else None
                    if value is None or not isinstance(literal, (str, int, float, bool, type(None))):
                        complete = False
                        break
                except (ValueError, TypeError):
                    complete = False
                    break
                values.append(ast.Constant(value=literal))
            if values and complete:
                definitions[node.name] = ast.Subscript(
                    value=ast.Name(id="Literal", ctx=ast.Load()),
                    slice=ast.Tuple(elts=values, ctx=ast.Load()), ctx=ast.Load(),
                )
        return definitions

    def _extract_parameters_from_ast(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef, *,
        enum_definitions: dict[str, ast.AST] | None = None,
    ) -> dict[str, Any]:
        """Extract a complete schema; nullability never makes an argument omittable."""
        from copy import deepcopy

        definitions = enum_definitions or {}

        class ResolveEnum(ast.NodeTransformer):
            def visit_Name(self, node: ast.Name) -> ast.AST:
                return deepcopy(definitions.get(node.id, node))

        properties: dict[str, Any] = {}
        required: list[str] = []
        args = func_node.args
        positional = [*args.posonlyargs, *args.args]
        defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        parameters = list(zip(positional, defaults)) + list(zip(args.kwonlyargs, args.kw_defaults))
        for arg, default_node in parameters:
            name = arg.arg
            if name in ("self", "cls") or (name == "context" and arg.annotation is None) or (
                arg.annotation and "ExecutionContext" in self._annotation_to_string(arg.annotation)
            ):
                continue
            annotation = arg.annotation
            if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
                try:
                    annotation = ast.parse(annotation.value, mode="eval").body
                except SyntaxError:
                    annotation = None
            schema = self._annotation_to_json_schema(ResolveEnum().visit(deepcopy(annotation))) if annotation else {}
            schema["title"] = re.sub(r"([a-z])([A-Z])", r"\1 \2", name.replace("_", " ")).title()
            if default_node is not None:
                try:
                    schema["default"] = ast.literal_eval(default_node)
                except (ValueError, TypeError):
                    pass
            else:
                required.append(name)
            properties[name] = schema
        result: dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "properties": properties,
            # **kwargs is an explicit opt-in to arbitrary keyword arguments.
            "additionalProperties": args.kwarg is not None,
        }
        if required:
            result["required"] = required
        return result

    def _annotation_to_string(self, annotation: ast.AST) -> str:
        """Convert annotation AST to string representation."""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return str(annotation.value)
        elif isinstance(annotation, ast.Subscript):
            return f"{self._annotation_to_string(annotation.value)}[...]"
        elif isinstance(annotation, ast.Attribute):
            return f"{self._annotation_to_string(annotation.value)}.{annotation.attr}"
        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            # Python 3.10+ union syntax: str | None
            left = self._annotation_to_string(annotation.left)
            right = self._annotation_to_string(annotation.right)
            return f"{left} | {right}"
        return ""

    @staticmethod
    def _annotation_base_name(annotation: ast.AST) -> str:
        if isinstance(annotation, ast.Name):
            return annotation.id
        if isinstance(annotation, ast.Attribute):
            return annotation.attr
        return ""

    @staticmethod
    def _json_type_for_literal(value: Any) -> str | None:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        return None

    def _annotation_to_json_schema(self, annotation: ast.AST) -> dict[str, Any]:
        """Convert a Python annotation AST into a nested JSON Schema."""
        primitive_types = {
            "str": "string",
            "int": "integer",
            "float": "number",
            "bool": "boolean",
            "None": "null",
            "NoneType": "null",
        }

        if isinstance(annotation, ast.Constant):
            if annotation.value is None:
                return {"type": "null"}
            return {}

        if isinstance(annotation, (ast.Name, ast.Attribute)):
            name = self._annotation_base_name(annotation)
            if name in primitive_types:
                return {"type": primitive_types[name]}
            if name in {"list", "List", "Sequence"}:
                return {"type": "array", "items": {}}
            if name in {"dict", "Dict", "Mapping"}:
                return {"type": "object", "additionalProperties": True}
            if name in {"Any", "object"}:
                return {}
            return {}

        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return {
                "anyOf": [
                    self._annotation_to_json_schema(annotation.left),
                    self._annotation_to_json_schema(annotation.right),
                ]
            }

        if not isinstance(annotation, ast.Subscript):
            return {}

        base_name = self._annotation_base_name(annotation.value)
        slice_node = annotation.slice
        slice_items = (
            list(slice_node.elts)
            if isinstance(slice_node, ast.Tuple)
            else [slice_node]
        )
        return self._subscript_annotation_to_json_schema(base_name, slice_items)

    def _subscript_annotation_to_json_schema(
        self,
        base_name: str,
        slice_items: list[ast.AST],
    ) -> dict[str, Any]:
        """Convert a parameterized annotation into JSON Schema."""
        if base_name in {"list", "List", "Sequence"}:
            item_schema = (
                self._annotation_to_json_schema(slice_items[0])
                if slice_items
                else {}
            )
            return {"type": "array", "items": item_schema}

        if base_name in {"dict", "Dict", "Mapping"}:
            value_schema = (
                self._annotation_to_json_schema(slice_items[1])
                if len(slice_items) > 1
                else {}
            )
            return {"type": "object", "additionalProperties": value_schema}

        if base_name == "Literal":
            return self._literal_items_to_json_schema(slice_items)

        if base_name == "Optional":
            if not slice_items:
                return {}
            inner = self._annotation_to_json_schema(slice_items[0])
            return {"anyOf": [inner, {"type": "null"}]}

        if base_name == "Union":
            return {
                "anyOf": [
                    self._annotation_to_json_schema(item)
                    for item in slice_items
                ]
            }

        if base_name == "Annotated" and slice_items:
            return self._annotation_to_json_schema(slice_items[0])

        if base_name in {"tuple", "Tuple"}:
            if len(slice_items) == 2 and isinstance(slice_items[1], ast.Constant) and slice_items[1].value is Ellipsis:
                return {"type": "array", "items": self._annotation_to_json_schema(slice_items[0])}
            return {
                "type": "array",
                "prefixItems": [
                    self._annotation_to_json_schema(item)
                    for item in slice_items
                ],
                "minItems": len(slice_items),
                "maxItems": len(slice_items),
            }

        return {}

    def _literal_items_to_json_schema(
        self,
        slice_items: list[ast.AST],
    ) -> dict[str, Any]:
        """Build an enum schema from statically resolvable Literal members."""
        values: list[Any] = []
        for item in slice_items:
            try:
                values.append(ast.literal_eval(item))
            except (ValueError, TypeError):
                continue
        if not values:
            return {}

        schema: dict[str, Any] = {"enum": values}
        literal_types = {
            json_type
            for value in values
            if (json_type := self._json_type_for_literal(value)) is not None
        }
        if len(literal_types) == 1:
            schema["type"] = literal_types.pop()
        return schema

    def _is_optional_annotation(self, annotation: ast.AST) -> bool:
        """Check if annotation represents an optional type."""
        if isinstance(annotation, ast.Subscript):
            if self._annotation_base_name(annotation.value) == "Optional":
                return True
            if self._annotation_base_name(annotation.value) == "Union":
                items = (
                    annotation.slice.elts
                    if isinstance(annotation.slice, ast.Tuple)
                    else [annotation.slice]
                )
                return any(
                    self._annotation_to_string(item) == "None"
                    for item in items
                )

        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            # Check for str | None pattern
            right_str = self._annotation_to_string(annotation.right)
            left_str = self._annotation_to_string(annotation.left)
            if right_str == "None" or left_str == "None":
                return True

        return False

