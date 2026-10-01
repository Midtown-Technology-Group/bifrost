"""ORM mapping contract: deleting a Role removes its user assignments.

Regression guard for MIDT-103: ``DELETE /api/roles/{id}`` returned 500 for
roles with ``user_roles`` rows because ``Role.users`` had no delete cascade,
so the ORM tried to null the ``user_roles.role_id`` primary-key column.
The endpoint documents "CASCADE removes all role assignments", so the
mapping must carry ``delete-orphan`` (mirroring ``User.roles``).

These checks run without a database; end-to-end delete behavior is covered
by ``test_delete_role_with_assigned_users_cascades`` in
``api/tests/e2e/api/test_roles.py`` (CI gate).
"""

from src.models.orm.users import Role, User, UserRole


def _cascade_names(relationship) -> set[str]:
    return set(relationship.property.cascade)


class TestRoleUsersDeleteCascade:
    def test_role_users_cascades_delete_orphan(self):
        """Role.users must delete orphaned UserRole rows on role delete."""
        cascade = _cascade_names(Role.users)
        assert "delete" in cascade
        assert "delete-orphan" in cascade

    def test_role_users_back_populates_user_role(self):
        """Role.users <-> UserRole.role stay a consistent bidirectional pair."""
        assert Role.users.property.back_populates == "role"
        assert UserRole.role.property.back_populates == "users"

    def test_user_roles_cascade_stays_symmetric(self):
        """User.roles keeps its cascade so user deletes still clean up."""
        cascade = _cascade_names(User.roles)
        assert "delete" in cascade
        assert "delete-orphan" in cascade
        assert User.roles.property.back_populates == "user"
