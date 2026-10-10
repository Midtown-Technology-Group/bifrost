"""Persisted scope/controls are independent of publication bookkeeping."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.models.orm.app_roles import AppRole
from src.models.orm.users import Role
from src.services.application_publication import publication_controls_hash


@pytest.mark.asyncio
async def test_publication_controls_hash_preserves_scope_roles_and_binary_metadata(db_session):
    app = Application(id=uuid4(), name="publication-test", slug="publication-" + uuid4().hex,
                      repo_path="apps/test", organization_id=None, logo_data=b"\xff\x00")
    role = Role(id=uuid4(), name="publication-role-" + uuid4().hex, created_by="test")
    db_session.add_all([app, role])
    await db_session.flush()
    baseline = await publication_controls_hash(db_session, app.id, lock=True)
    app.published_at = datetime.now(timezone.utc)
    app.published_snapshot = {"entry.js": ""}
    await db_session.flush()
    assert await publication_controls_hash(db_session, app.id) == baseline
    app.access_level = "role_based"
    await db_session.flush()
    changed = await publication_controls_hash(db_session, app.id)
    assert changed != baseline
    db_session.add(AppRole(app_id=app.id, role_id=role.id))
    await db_session.flush()
    assert await publication_controls_hash(db_session, app.id) != changed
