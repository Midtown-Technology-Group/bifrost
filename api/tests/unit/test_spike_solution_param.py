"""SPIKE: per-call solution= resolves slug/name inside the resolved scope."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.core.auth import get_execution_context
from src.core.principal import UserPrincipal
from src.models.orm.applications import Application
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.solution_scope import (
    check_inbound_allowed,
    derive_execution_solution_scope,
    resolve_trustworthy_caller,
    resolve_solution_ref,
)


async def _org(db):
    o = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
    db.add(o)
    await db.flush()
    return o


async def _sol(db, org_id, slug):
    s = Solution(id=uuid4(), slug=slug, name=f"Name-{slug}", organization_id=org_id)
    db.add(s)
    await db.flush()
    return s


def _no_ctx():
    return SimpleNamespace(solution_id=None, app_id=None)


@pytest.mark.e2e
class TestSpikeSolutionRef:
    async def test_unauthorized_app_header_is_not_trusted_caller(
        self, db_session
    ):
        """A raw header cannot impersonate a role-restricted Solution app."""
        org = await _org(db_session)
        sol = await _sol(db_session, org.id, "sealed")
        sol.allow_inbound_access = False
        app = Application(
            id=uuid4(),
            name="Restricted app",
            slug=f"restricted-{uuid4().hex[:8]}",
            repo_path=f"apps/{uuid4().hex}",
            organization_id=org.id,
            solution_id=sol.id,
            app_model="standalone_v2",
            access_level="role_based",
        )
        db_session.add(app)
        await db_session.flush()

        user = UserPrincipal(
            user_id=uuid4(),
            email="unprivileged@example.test",
            organization_id=org.id,
        )
        request = SimpleNamespace(
            headers={"X-Bifrost-App": str(app.id)},
            query_params={},
            url=SimpleNamespace(path="/api/tables"),
        )

        ctx = await get_execution_context(request, user, db_session)

        assert ctx.app_id is None
        assert ctx.solution_id is None
        assert await resolve_trustworthy_caller(db_session, ctx) is None
        assert await check_inbound_allowed(db_session, sol.id, None) is False

    async def test_authorized_app_header_remains_trusted_caller(self, db_session):
        """An in-scope app that grants access still establishes its install."""
        org = await _org(db_session)
        sol = await _sol(db_session, org.id, "authorized")
        app = Application(
            id=uuid4(),
            name="Authorized app",
            slug=f"authorized-{uuid4().hex[:8]}",
            repo_path=f"apps/{uuid4().hex}",
            organization_id=org.id,
            solution_id=sol.id,
            app_model="standalone_v2",
            access_level="authenticated",
        )
        db_session.add(app)
        await db_session.flush()

        user = UserPrincipal(
            user_id=uuid4(),
            email="member@example.test",
            organization_id=org.id,
        )
        request = SimpleNamespace(
            headers={"X-Bifrost-App": str(app.id)},
            query_params={},
            url=SimpleNamespace(path="/api/tables"),
        )

        ctx = await get_execution_context(request, user, db_session)

        assert ctx.app_id == str(app.id)
        assert ctx.solution_id == str(sol.id)
        assert await resolve_trustworthy_caller(db_session, ctx) == sol.id

    async def test_slug_resolves_in_scope(self, db_session):
        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "acme-crm")
        got = await resolve_solution_ref(db_session, "acme-crm", org)
        assert got == sol.id

    async def test_slug_does_not_cross_org(self, db_session):
        org_a = (await _org(db_session)).id
        org_b = (await _org(db_session)).id
        await _sol(db_session, org_a, "acme-crm")
        got = await resolve_solution_ref(db_session, "acme-crm", org_b)
        assert got is None

    async def test_derive_body_slug_wins_when_ctx_unset(self, db_session):
        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "acme-crm")
        got = await derive_execution_solution_scope(
            db_session, _no_ctx(), solution_id="acme-crm", form_id=None, app_id=None,
            target_org_id=org,
        )
        assert got == sol.id

    async def test_uuid_passthrough_unchanged(self, db_session):
        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "acme-crm")
        got = await derive_execution_solution_scope(
            db_session, _no_ctx(), solution_id=str(sol.id), form_id=None, app_id=None,
            target_org_id=org,
        )
        assert got == sol.id

    async def test_sealed_install_denies_cross_caller(self, db_session):
        from src.services.solution_scope import check_inbound_allowed

        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "sealed")
        sol.allow_inbound_access = False
        await db_session.flush()
        # Outside caller (None) denied…
        assert await check_inbound_allowed(db_session, sol.id, None) is False
        # …own-install caller always passes.
        assert await check_inbound_allowed(db_session, sol.id, sol.id) is True

    async def test_open_install_allows_cross_caller(self, db_session):
        from src.services.solution_scope import check_inbound_allowed

        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "open")
        assert await check_inbound_allowed(db_session, sol.id, None) is True

    async def test_inactive_install_denies_own_caller(self, db_session):
        from src.services.solution_scope import check_inbound_allowed

        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "inactive")
        sol.status = "inactive"
        await db_session.flush()
        assert await check_inbound_allowed(db_session, sol.id, sol.id) is False

    async def test_sealed_body_target_raises_no_fallback(self, db_session):
        """Codex P1: a sealed explicit target raises (routers 404 without
        shared fallback) instead of resolving or falling back."""
        from src.services.solution_scope import SolutionInboundDenied

        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "sealed")
        sol.allow_inbound_access = False
        await db_session.flush()
        with pytest.raises(SolutionInboundDenied):
            await derive_execution_solution_scope(
                db_session, _no_ctx(), solution_id=str(sol.id),
                form_id=None, app_id=None, target_org_id=org,
            )

    async def test_sealed_ctx_target_raises(self, db_session):
        """Codex P1: ?solution= targeting a sealed install is gated too."""
        from src.services.solution_scope import SolutionInboundDenied

        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "sealed")
        sol.allow_inbound_access = False
        await db_session.flush()
        with pytest.raises(SolutionInboundDenied):
            await derive_execution_solution_scope(
                db_session,
                SimpleNamespace(solution_id=str(sol.id), app_id=None),
                solution_id=None, form_id=None, app_id=None,
            )

    async def test_signed_engine_claim_beats_spoofed_param(self, db_session):
        """Codex P1: the signed engine_solution_id is the caller even when a
        request param claims otherwise."""
        from src.services.solution_scope import resolve_trustworthy_caller

        org = (await _org(db_session)).id
        real = await _sol(db_session, org, "real")
        spoof = uuid4()
        ctx = SimpleNamespace(
            solution_id=None,
            app_id=None,
            caller_solution_id=str(spoof),
            user=SimpleNamespace(
                engine_execution_id="exec-1",
                engine_solution_id=str(real.id),
            ),
        )
        assert await resolve_trustworthy_caller(db_session, ctx) == real.id
