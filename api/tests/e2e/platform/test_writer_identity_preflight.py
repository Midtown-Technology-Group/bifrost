"""Collect existing test DB identities; success never establishes writer safety."""

import asyncio
import json
import os
import stat
from datetime import datetime
from time import monotonic

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from tests.conftest import TEST_DATABASE_URL

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]
TABLES = (
    "executions",
    "workflow_execution_attempts",
    "agent_runs",
    "agent_run_steps",
    "execution_attempts",
    "execution_lifecycle_events",
    "work_deliveries",
    "execution_logs",
    "ai_usage",
    "agent_run_verdict_history",
    "agent_run_flag_conversations",
    "poison_message_dispositions",
)
QUERIES = (
    (
        """SELECT current_database() AS database_name, session_user::text AS session_user,
       current_user::text AS current_user, pg_catalog.pg_backend_pid() AS backend_pid,
       pg_catalog.pg_postmaster_start_time() AS postmaster_started_at,
       current_setting('server_version_num')::integer AS server_version_num,
       current_setting('transaction_read_only') AS transaction_read_only,
       current_setting('transaction_isolation') AS transaction_isolation""",
        1,
    ),
    (
        """SELECT r.rolname,r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,
       r.rolcanlogin,r.rolreplication,r.rolbypassrls,
       pg_catalog.pg_has_role(session_user,r.oid,'MEMBER') AS session_member,
       pg_catalog.pg_has_role(session_user,r.oid,'USAGE') AS session_usage,
       pg_catalog.pg_has_role(current_user,r.oid,'MEMBER') AS current_member,
       pg_catalog.pg_has_role(current_user,r.oid,'USAGE') AS current_usage
       FROM pg_catalog.pg_roles r WHERE r.rolname IN (session_user,current_user)
       OR pg_catalog.pg_has_role(session_user,r.oid,'MEMBER')
       OR pg_catalog.pg_has_role(current_user,r.oid,'MEMBER')
       ORDER BY r.rolname LIMIT 257""",
        256,
    ),
    (
        """SELECT n.nspname,c.relname,c.relkind,
       pg_catalog.pg_get_userbyid(c.relowner) AS owner,c.relrowsecurity,c.relforcerowsecurity,
       pg_catalog.pg_get_userbyid(n.nspowner) AS schema_owner,
       pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage,
       pg_catalog.has_schema_privilege(current_user,n.oid,'CREATE') AS schema_create,
       pg_catalog.has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
       pg_catalog.has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
       pg_catalog.has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
       pg_catalog.has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,
       pg_catalog.has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,
       pg_catalog.has_table_privilege(current_user,c.oid,'REFERENCES') AS can_reference,
       pg_catalog.has_table_privilege(current_user,c.oid,'TRIGGER') AS can_trigger
       FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relname=ANY($1::text[]) ORDER BY c.relname""",
        12,
    ),
    (
        """SELECT c.relname,a.attname,
       pg_catalog.has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS can_select,
       pg_catalog.has_column_privilege(current_user,c.oid,a.attnum,'INSERT') AS can_insert,
       pg_catalog.has_column_privilege(current_user,c.oid,a.attnum,'UPDATE') AS can_update,
       pg_catalog.has_column_privilege(current_user,c.oid,a.attnum,'REFERENCES') AS can_reference
       FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
       WHERE n.nspname='public' AND c.relname=ANY($1::text[])
       AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum LIMIT 1025""",
        1024,
    ),
    (
        """SELECT c.relname,t.tgname,t.tgenabled,t.tgisinternal,
       nf.nspname AS function_schema,p.proname,p.oid AS function_oid,
       pg_catalog.pg_get_userbyid(p.proowner) AS function_owner,p.prosecdef,
       pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute
       FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
       JOIN pg_catalog.pg_namespace nf ON nf.oid=p.pronamespace
       WHERE n.nspname='public' AND c.relname=ANY($1::text[])
       ORDER BY c.relname,t.tgname LIMIT 257""",
        256,
    ),
    (
        """SELECT schemaname,tablename,policyname,permissive,roles,cmd
       FROM pg_catalog.pg_policies WHERE schemaname='public'
       AND tablename=ANY($1::text[]) ORDER BY tablename,policyname LIMIT 257""",
        256,
    ),
)
QUERY_COLUMNS = (
    "database_name session_user current_user backend_pid postmaster_started_at server_version_num transaction_read_only transaction_isolation".split(),
    "rolname rolsuper rolinherit rolcreaterole rolcreatedb rolcanlogin rolreplication rolbypassrls session_member session_usage current_member current_usage".split(),
    "nspname relname relkind owner relrowsecurity relforcerowsecurity schema_owner schema_usage schema_create can_select can_insert can_update can_delete can_truncate can_reference can_trigger".split(),
    "relname attname can_select can_insert can_update can_reference".split(),
    "relname tgname tgenabled tgisinternal function_schema proname function_oid function_owner prosecdef can_execute".split(),
    "schemaname tablename policyname permissive roles cmd".split(),
)
TEXT_FIELDS = frozenset(
    (
        "database_name",
        "session_user",
        "current_user",
        "transaction_read_only",
        "transaction_isolation",
        "rolname",
        "nspname",
        "relname",
        "relkind",
        "owner",
        "schema_owner",
        "attname",
        "tgname",
        "tgenabled",
        "function_schema",
        "proname",
        "function_owner",
        "schemaname",
        "tablename",
        "policyname",
        "permissive",
        "cmd",
    )
)
BOOL_FIELDS = frozenset(
    (
        "rolsuper",
        "rolinherit",
        "rolcreaterole",
        "rolcreatedb",
        "rolcanlogin",
        "rolreplication",
        "rolbypassrls",
        "session_member",
        "session_usage",
        "current_member",
        "current_usage",
        "relrowsecurity",
        "relforcerowsecurity",
        "schema_usage",
        "schema_create",
        "can_select",
        "can_insert",
        "can_update",
        "can_delete",
        "can_truncate",
        "can_reference",
        "can_trigger",
        "tgisinternal",
        "prosecdef",
        "can_execute",
    )
)
RECEIPT_PATH = "/bifrost-results/writer-identity-preflight.log"
MAX_BYTES = 131072


def _require(condition):
    if not condition:
        raise ValueError("preflight validation failed")


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 128 and value.isprintable())
    return value


def _catalog_row(row):
    result = {}
    for key, value in dict(row).items():
        if key in {"relkind", "tgenabled"}:
            admitted = (b"r",) if key == "relkind" else (b"O", b"D", b"R", b"A")
            _require(type(value) is bytes and len(value) == 1 and value in admitted)
            result[key] = value.decode("ascii")
        elif key in TEXT_FIELDS:
            result[key] = _text(value)
        elif key in BOOL_FIELDS:
            _require(type(value) is bool)
            result[key] = value
        elif key in {"backend_pid", "server_version_num", "function_oid"}:
            _require(type(value) is int and 0 < value <= 4294967295)
            result[key] = value
        elif key == "postmaster_started_at":
            _require(type(value) is datetime and value.utcoffset() is not None)
            result[key] = value.isoformat()
        elif key == "roles":
            _require(type(value) is list and len(value) <= 256)
            result[key] = [_text(role) for role in value]
        else:
            raise ValueError("unexpected catalog field")
    return result


async def _snapshot(url, endpoint, index, deadline):
    connection = None
    started = monotonic()
    try:
        connection = await asyncpg.connect(
            url.render_as_string(hide_password=False),
            statement_cache_size=0,
            timeout=min(5, deadline - monotonic()),
        )
        observations = []
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            for query_index, (query, cap) in enumerate(QUERIES):
                args = (list(TABLES),) if query_index >= 2 else ()
                rows = await connection.fetch(
                    query, *args, timeout=min(5, deadline - monotonic())
                )
                _require(len(rows) <= cap)
                _require(
                    all(list(row.keys()) == QUERY_COLUMNS[query_index] for row in rows)
                )
                observations.append([_catalog_row(row) for row in rows])
            _require(len(observations[0]) == 1 and bool(observations[1]))
            identity = observations[0][0]
            _require(identity["database_name"] == "bifrost_test")
            _require(identity["transaction_read_only"] == "on")
            _require(identity["transaction_isolation"] == "repeatable read")
            relations = observations[2]
            _require(len(relations) == len(TABLES))
            _require({row["relname"] for row in relations} == set(TABLES))
            _require(
                all(
                    row["nspname"] == "public" and row["relkind"] == "r"
                    for row in relations
                )
            )
        tabular = [
            {
                "query": f"Q{number + 1}",
                "columns": QUERY_COLUMNS[number],
                "rows": [
                    [row[column] for column in QUERY_COLUMNS[number]] for row in rows
                ],
            }
            for number, rows in enumerate(observations)
        ]
        return {
            "endpoint": endpoint,
            "sample_index": index,
            "queries": tabular,
            "elapsed_seconds": monotonic() - started,
        }
    finally:
        if connection is not None:
            if monotonic() >= deadline:
                connection.terminate()
            else:
                try:
                    async with asyncio.timeout_at(deadline):
                        await connection.close(timeout=min(5, deadline - monotonic()))
                finally:
                    if not connection.is_closed():
                        connection.terminate()


def _write_receipt(receipt, deadline):
    _require(monotonic() < deadline)
    data = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()
    _require(len(data) <= MAX_BYTES)
    fd = os.open(
        RECEIPT_PATH, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644
    )
    try:
        os.fchmod(fd, 0o644)
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        _require(stat.S_IMODE(before.st_mode) == 0o644)
        offset = 0
        while offset < len(data):
            _require(monotonic() < deadline)
            written = os.write(fd, data[offset:])
            _require(written > 0)
            offset += written
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        observed = os.read(fd, MAX_BYTES + 1)
        _require(observed == data)
        final = os.fstat(fd)
        path = os.stat(RECEIPT_PATH, follow_symlinks=False)
        _require(stat.S_ISREG(final.st_mode) and final.st_nlink == 1)
        _require(stat.S_IMODE(final.st_mode) == 0o644)
        _require((path.st_dev, path.st_ino) == (final.st_dev, final.st_ino))
        _require(monotonic() < deadline)
    finally:
        os.close(fd)
    _require(monotonic() < deadline)


async def test_collect_existing_writer_identity_receipt():
    failed = False
    started = monotonic()
    deadline = started + 30
    try:
        async with asyncio.timeout_at(deadline):
            url = make_url(TEST_DATABASE_URL)
            _require(url.drivername == "postgresql+asyncpg" and not url.query)
            _require(url.host == "pgbouncer" and url.port == 5432)
            _require(url.database == "bifrost_test" and url.username == "bifrost")
            _require(type(url.password) is str and bool(url.password))
            pooled = url.set(drivername="postgresql")
            samples = []
            for endpoint, index in (("direct", 0), ("pool", 0), ("pool", 1)):
                target = pooled.set(host="postgres") if endpoint == "direct" else pooled
                samples.append(await _snapshot(target, endpoint, index, deadline))
            receipt = {
                "schema": "bifrost.writer-identity-preflight/v1",
                "purpose": "existing-test-db-catalog-identity",
                "collection_status": "collected",
                "security_readiness": "not_established",
                "scope": "isolated-ci-test-runner",
                "limits": [
                    "attached-triggers-only",
                    "member-usage-not-admin-set-role-graph",
                    "not-api-worker-scheduler-session-proof",
                    "not-deployment-proof",
                ],
                "catalog_char_representation": "relkind/tgenabled: native one-byte bytes to closed ASCII",
                "deadline_seconds": 30,
                "elapsed_seconds_before_receipt": monotonic() - started,
                "samples": samples,
            }
            _write_receipt(receipt, deadline)
    except (Exception, asyncio.CancelledError):
        failed = True
    if failed:
        pytest.fail(
            "Writer identity preflight collection failed; no readiness proof.",
            pytrace=False,
        )
