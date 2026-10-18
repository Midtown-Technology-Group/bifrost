"""Retained namespace sharing uses current role boundaries without content grants."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.core.constants import PROVIDER_ORG_ID
from src.core.principal import UserPrincipal
from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller, load_caller
from src.services.knowledge_namespace_access import (
    accessible_knowledge_namespaces,
    effective_namespace_role_ids,
)


def _caller(home, *, grants=(), external=False):
    user_id = uuid4()
    principal = UserPrincipal(user_id=user_id, email="reader@example.test", organization_id=home,
                              is_external=external)
    ctx = AuthorizationContext(user_id=user_id, home_organization_id=home, base_role_id=uuid4(),
                               is_external=external, base_permissions=frozenset(), role_grants=grants)
    return Caller(principal, ctx)


@pytest.mark.parametrize("external", [False, True])
def test_selector_includes_base_and_home_roles_without_permission_rows(external):
    home, role_id = uuid4(), uuid4()
    grant = RoleGrant(role_id=role_id, permissions=frozenset(),
                     boundaries=(Boundary(BoundaryKind.ORGANIZATION, home),))
    caller = _caller(home, grants=(grant,), external=external)
    assert effective_namespace_role_ids(caller) == {caller.ctx.base_role_id, role_id}


def test_foreign_absent_and_platform_boundaries_do_not_reveal_home_selector():
    home = uuid4()
    grants = tuple(
        RoleGrant(role_id=uuid4(), permissions=frozenset(), boundaries=boundaries)
        for boundaries in [(), (Boundary(BoundaryKind.ORGANIZATION, uuid4()),),
                           (Boundary(BoundaryKind.PLATFORM),)]
    )
    caller = _caller(home, grants=grants)
    assert effective_namespace_role_ids(caller) == {caller.ctx.base_role_id}


@pytest.mark.parametrize("provider", [False, True])
def test_managed_boundary_is_effective_only_at_customer_home(provider):
    home, role_id = PROVIDER_ORG_ID if provider else uuid4(), uuid4()
    grant = RoleGrant(role_id=role_id, permissions=frozenset(),
                     boundaries=(Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),))
    caller = _caller(home, grants=(grant,))
    assert (role_id in effective_namespace_role_ids(caller)) is not provider


def test_no_context_and_null_home_are_not_platform_scope():
    caller = _caller(None)
    assert effective_namespace_role_ids(caller) == frozenset()
    assert effective_namespace_role_ids(replace(caller, ctx=None)) == frozenset()


def test_admin_uses_stored_wildcard_and_retains_assigned_role_scope_exception():
    home, admin_role, foreign_role = uuid4(), uuid4(), uuid4()
    caller = _caller(home, grants=(
        RoleGrant(role_id=admin_role, permissions=frozenset({"*"}),
                  boundaries=(Boundary(BoundaryKind.PLATFORM),)),
        RoleGrant(role_id=foreign_role, permissions=frozenset(),
                  boundaries=(Boundary(BoundaryKind.ORGANIZATION, uuid4()),)),
    ))
    assert effective_namespace_role_ids(caller) == {caller.ctx.base_role_id, admin_role, foreign_role}


@pytest.mark.asyncio
async def test_real_selector_preserves_scope_ids_counts_and_revocation(db_session):
    from src.models.orm.organizations import Organization
    from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary

    home, foreign = Organization(name=f"Home-{uuid4()}", created_by="test"), Organization(name=f"Foreign-{uuid4()}", created_by="test")
    base = Role(name=f"Base-{uuid4()}", created_by="test")
    additional = Role(name=f"Sharing-{uuid4()}", created_by="test")
    db_session.add_all([home, foreign, base, additional])
    await db_session.flush()
    user = User(email=f"namespace-{uuid4()}@test.local", organization_id=home.id,
                base_role_id=base.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=additional.id, assigned_by="test"))
    await db_session.flush()
    db_session.add(UserRoleBoundary(user_id=user.id, role_id=additional.id,
                                   kind="organization", organization_id=home.id))
    rows = [KnowledgeNamespaceRole(namespace=name, role_id=role, organization_id=org)
            for name, role, org in [("base-home", base.id, home.id), ("base-global", base.id, None),
                                    ("additional", additional.id, home.id),
                                    ("foreign", additional.id, foreign.id)]]
    db_session.add_all(rows)
    await db_session.flush()
    original_ids = {row.id for row in rows}
    principal = UserPrincipal(user_id=user.id, email=user.email, organization_id=home.id)
    caller = await load_caller(db_session, principal)
    assert await accessible_knowledge_namespaces(db_session, caller) == ["additional", "base-global", "base-home"]
    # One sharing role held in two organizations keeps each holder's selector
    # at their own home/global namespace assignments.
    other = User(email=f"namespace-other-{uuid4()}@test.local", organization_id=foreign.id,
                 base_role_id=base.id)
    db_session.add(other)
    await db_session.flush()
    db_session.add(UserRole(user_id=other.id, role_id=additional.id, assigned_by="test"))
    await db_session.flush()
    db_session.add(UserRoleBoundary(user_id=other.id, role_id=additional.id,
                                   kind="organization", organization_id=foreign.id))
    await db_session.flush()
    other_principal = UserPrincipal(user_id=other.id, email=other.email, organization_id=foreign.id)
    other_caller = await load_caller(db_session, other_principal)
    assert await accessible_knowledge_namespaces(db_session, other_caller) == ["base-global", "foreign"]
    # Selector use creates no permission row, changes no namespace assignment.
    assert not list((await db_session.execute(select(RolePermission).where(
        RolePermission.role_id.in_([base.id, additional.id])))).scalars())
    assert {row.id for row in (await db_session.execute(select(KnowledgeNamespaceRole).where(
        KnowledgeNamespaceRole.role_id.in_([base.id, additional.id])))).scalars()} == original_ids

    await db_session.execute(delete(UserRoleBoundary).where(UserRoleBoundary.user_id == user.id))
    await db_session.flush()
    caller = await load_caller(db_session, principal)
    assert await accessible_knowledge_namespaces(db_session, caller) == ["base-global", "base-home"]


@pytest.mark.asyncio
async def test_global_namespace_assignment_remains_nulls_not_distinct(db_session):
    from src.models.orm.users import Role

    role = Role(name=f"Unique-{uuid4()}", created_by="test")
    db_session.add(role)
    await db_session.flush()
    db_session.add(KnowledgeNamespaceRole(namespace="shared", role_id=role.id, organization_id=None))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(KnowledgeNamespaceRole(namespace="shared", role_id=role.id, organization_id=None))
            await db_session.flush()
