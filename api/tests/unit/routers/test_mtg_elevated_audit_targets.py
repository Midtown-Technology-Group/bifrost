"""Audit the actual target and only the elevated part of retained MTG writes."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers.files import _require_local_mode_superuser
from src.routers.tables import _resolve_attribution


def principal(*, admin=False):
    return UserPrincipal(user_id=uuid4(), email="audit@example.test", organization_id=uuid4(), is_superuser=admin)


@pytest.mark.parametrize("write,permission", [(False, "repository.read"), (True, "repository.readwrite")])
def test_local_files_note_only_allowed_elevated_access(write, permission):
    with patch("src.routers.files.access_checks.note_power") as note:
        _require_local_mode_superuser("cloud", principal(), write=write)
        note.assert_not_called()
        with pytest.raises(HTTPException) as denied:
            _require_local_mode_superuser("local", principal(), write=write)
        assert denied.value.status_code == 403
        note.assert_not_called()
        _require_local_mode_superuser("local", principal(admin=True), write=write)
        note.assert_called_once_with(permission, None, subject="local-files")


def test_attribution_override_notes_table_target_not_actor_home():
    actor = principal(admin=True)
    target = uuid4()
    with patch("src.routers.tables.access_checks.note_power") as note:
        assert _resolve_attribution(actor, "source-actor", None, organization_id=target) == ("source-actor", "source-actor")
        note.assert_called_once_with("tableattribution.readwrite", target)


def test_attribution_omission_or_refusal_does_not_claim_power():
    actor = principal()
    with patch("src.routers.tables.access_checks.note_power") as note:
        assert _resolve_attribution(actor, None, None, organization_id=uuid4()) == (str(actor.user_id), str(actor.user_id))
        with pytest.raises(HTTPException) as denied:
            _resolve_attribution(actor, "forged", None, organization_id=uuid4())
        assert denied.value.status_code == 403
        note.assert_not_called()
