"""The metadata-only source comparison must reject authorization/body changes."""

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/operation_catalog/verify_metadata_only.py"
spec = importlib.util.spec_from_file_location("metadata_only_proof", SCRIPT)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

BEFORE = '''from src.core.auth import CurrentSuperuser
@router.get("/items", dependencies=[Depends(require_admin)])
async def items(user: CurrentSuperuser):
    return "items"
'''
AFTER = '''from src.core.auth import CurrentSuperuser
from src.services.operation_catalog import operation_route
@router.get("/items", dependencies=[Depends(require_admin)], **operation_route("items.list"))
async def items(user: CurrentSuperuser):
    return "items"
'''


def test_only_catalog_import_and_route_metadata_are_removed() -> None:
    assert proof.normalized(BEFORE) == proof.normalized(AFTER)
    assert proof.route_dependencies(BEFORE) == proof.route_dependencies(AFTER)


@pytest.mark.parametrize("before,after", [
    ("CurrentSuperuser", "CurrentActiveUser"),
    ("Depends(require_admin)", "Depends(require_user)"),
    ('"/items"', '"/other"'),
    ('return "items"', 'return "changed"'),
])
def test_dependency_signature_path_and_handler_changes_fail(before: str, after: str) -> None:
    assert proof.normalized(BEFORE) != proof.normalized(AFTER.replace(before, after))
