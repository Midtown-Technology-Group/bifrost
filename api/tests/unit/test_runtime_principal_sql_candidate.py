"""Compare inactive SQL proposal to its separately reviewed finite ACL contract.

This is artifact consistency evidence, not SQL execution or effective authority.
"""

import json
import re
from pathlib import Path

CANDIDATES = Path(__file__).resolve().parents[2] / "alembic/candidates"
PROFILE = json.loads((CANDIDATES / "runtime_principals_v1.json").read_text())


def test_grants_have_exactly_the_profile_authority_without_activation():
    source = (CANDIDATES / "runtime_principals_grants.sql").read_text()
    grants = set()
    for line in source.splitlines():
        if not line.startswith("GRANT "):
            continue
        match = re.fullmatch(
            r"GRANT ([A-Z, ]+?)(?: \(([a-z_, ]+)\))? ON "
            r'(DATABASE|SCHEMA|TABLE|SEQUENCE|FUNCTION) ([a-z_.()]+) TO "([a-z_]+)";',
            line,
        )
        assert match is not None, "unmodeled grant syntax must be reviewed"
        privileges, columns, kind, target, role = match.groups()
        for privilege in privileges.split(", "):
            for column in columns.split(", ") if columns else (None,):
                grants.add((role, kind, target, privilege, column))
    expected = set()
    for role, contract in PROFILE["roles"].items():
        for key, kind in (
            ("database_acl", "DATABASE"),
            ("schema_acl", "SCHEMA"),
            ("table_acl", "TABLE"),
            ("sequence_acl", "SEQUENCE"),
            ("function_acl", "FUNCTION"),
        ):
            for target, privileges in contract[key].items():
                expected.update(
                    (role, kind, target, privilege, None) for privilege in privileges
                )
        for target, privileges in contract["column_acl"].items():
            for privilege, columns in privileges.items():
                expected.update(
                    (role, "TABLE", target, privilege, column) for column in columns
                )
    assert grants == expected
    assert not re.search(r"^ALTER ROLE|^CREATE ROLE|^REVOKE", source, re.MULTILINE)
    assert "WITH GRANT OPTION" not in source


def test_preparation_is_fresh_cluster_nologin_with_explicit_ambient_disposition():
    source = (CANDIDATES / "runtime_principals_prepare.sql").read_text()
    declarations = re.findall(r'^CREATE ROLE "([a-z_]+)" (.+);$', source, re.MULTILINE)
    assert len(declarations) == len(PROFILE["roles"])
    assert {role for role, _ in declarations} == set(PROFILE["roles"])
    for _, attributes in declarations:
        assert set(attributes.split()) == {
            "NOLOGIN",
            "NOINHERIT",
            "NOSUPERUSER",
            "NOCREATEDB",
            "NOCREATEROLE",
            "NOREPLICATION",
            "NOBYPASSRLS",
        }
    assert not re.search(r"\bPASSWORD\b|^GRANT|^ALTER DEFAULT", source, re.MULTILINE)
    assert {line for line in source.splitlines() if line.startswith("REVOKE ")} == {
        "REVOKE CONNECT, TEMPORARY ON DATABASE bifrost_test FROM PUBLIC;",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC;",
    }
    # Existing identities must be rejected rather than adopted or repaired.
    assert "EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN" in source
    assert "20261012_work_delivery_started" in source
    assert "current_database() <> 'bifrost_test'" in source
