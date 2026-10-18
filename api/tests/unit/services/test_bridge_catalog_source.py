"""Bridge enforcement badges require a source path to the current-row gate.

Behavioral bridge tests own the allow/deny matrix. These checks keep the
catalog's special enforcement registry from silently claiming an unguarded
handler is implemented, while preserving its real dependency floor.
"""

import ast
from pathlib import Path

import pytest

from src.services.permission_catalog import _BRIDGE_ROW_CHECKS


_API_ROOT = Path(__file__).resolve().parents[3]


def _functions(relative: str) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    module = ast.parse((_API_ROOT / relative).read_text(encoding="utf-8"))
    return {
        node.name: node for node in module.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }


def _calls(node: ast.AST) -> list[ast.Call]:
    return [child for child in ast.walk(node) if isinstance(child, ast.Call)]


def _names(node: ast.AST) -> set[str]:
    return {call.func.id for call in _calls(node) if isinstance(call.func, ast.Name)}


def _reachable(start: ast.AST, functions: dict) -> set[str]:
    pending = list(_names(start))
    found: set[str] = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        if name in functions:
            pending.extend(_names(functions[name]))
    return found


@pytest.mark.parametrize("key,permission", sorted(_BRIDGE_ROW_CHECKS.items()))
def test_every_bridge_enforcement_badge_has_a_source_link(key, permission) -> None:
    method, path = key
    if permission == "agents.promote":
        module_path, prefix = "src/routers/agents.py", "/api/agents"
    elif path.startswith("/api/device-control-keys"):
        module_path, prefix = "src/routers/device_control_keys.py", "/api/device-control-keys"
    else:
        module_path, prefix = "src/routers/devices.py", "/api/devices"
    functions = _functions(module_path)
    handlers = [
        function for function in functions.values()
        if any(
            isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
            and dec.func.attr.upper() == method and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and prefix + dec.args[0].value == path
            for dec in function.decorator_list
        )
    ]
    assert len(handlers) == 1, key
    route = handlers[0]
    if permission == "agents.promote":
        assert "load_caller" in _names(route)
        calls = [call for call in _calls(route) if getattr(call.func, "id", None) == "bridge_permission_allowed"]
        assert len(calls) == 1
        assert ast.literal_eval(calls[0].args[1]) == permission
        assert ast.unparse(calls[0].args[2]) == "agent.organization_id"
        return
    gates = _reachable(route, functions)
    if path == "/api/device-control-keys" and method == "POST" or path.endswith("/rotate"):
        expected_gate = "require_device_control_key_issue_permission"
    elif permission == "devices.execute":
        expected_gate = "require_device_execute_permission"
    else:
        expected_gate = "require_device_manage_permission"
    assert expected_gate in gates, (key, expected_gate, gates)


def test_device_gate_loads_current_rows_and_evaluates_one_target_for_each_grant() -> None:
    functions = _functions("src/services/devices.py")
    gate = functions["_require_device_permissions"]
    assert "load_caller" in _names(gate)
    decisions = [call for call in _calls(gate) if getattr(call.func, "id", None) == "_device_permission_allowed"]
    assert len(decisions) == 1
    assert [ast.unparse(arg) for arg in decisions[0].args] == ["caller", "permission", "target_org"]
    issue = functions["require_device_control_key_issue_permission"]
    conjunctions = [call for call in _calls(issue) if getattr(call.func, "id", None) == "_require_device_permissions"]
    assert len(conjunctions) == 1
    assert ast.literal_eval(conjunctions[0].args[2]) == ("devices.readwrite", "devices.execute")
    for helper, permission in (
        ("require_device_manage_permission", "devices.readwrite"),
        ("require_device_execute_permission", "devices.execute"),
    ):
        calls = [call for call in _calls(functions[helper]) if getattr(call.func, "id", None) == "_require_device_permissions"]
        assert len(calls) == 1
        assert ast.literal_eval(calls[0].args[2]) == (permission,)
