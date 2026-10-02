"""Observe the existing isolated PG16 graph; collection is not writer safety.

Only the diagnostic transactions are read-only. Existing harness setup/reset
remains supported. Named testcase PASS and external artifact provenance are
required even if a raw-complete receipt exists after failed custody checks.
"""

import asyncio
import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from tests.conftest import TEST_DATABASE_URL
from tests.helpers import writer_catalog_receipt as contract

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]
RECEIPT_PATH = "/bifrost-results/writer-catalog-receipt.log"
DEADLINE_SECONDS = 60
SOURCE_BYTES = 1024 * 1024
# Literal bodies match the independently reviewed WEX-CAT0 source package.
SQL_BODIES = (
    """WITH roots(name) AS (VALUES
 ('executions'),('workflow_execution_attempts'),('agent_runs'),('agent_run_steps'),
 ('execution_attempts'),('execution_lifecycle_events'),('work_deliveries'),
 ('execution_logs'),('ai_usage'),('agent_run_verdict_history'),
 ('agent_run_flag_conversations'),('poison_message_dispositions'))
SELECT r.name,n.nspname,c.oid::bigint AS relation_oid,c.relkind::text
FROM roots r LEFT JOIN pg_catalog.pg_namespace n ON n.nspname='public'
LEFT JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=r.name
ORDER BY r.name LIMIT 13;
""",
    """SELECT current_database() AS database_name,session_user::text,current_user::text,
 pg_catalog.pg_backend_pid() AS backend_pid,
 pg_catalog.pg_postmaster_start_time() AS postmaster_started_at,
 current_setting('server_version_num')::integer AS server_version_num,
 current_setting('transaction_read_only') AS transaction_read_only,
 current_setting('transaction_isolation') AS transaction_isolation LIMIT 2;
""",
    """SELECT r.oid::bigint AS role_oid,r.rolname,r.rolsuper,r.rolinherit,
 r.rolcreaterole,r.rolcreatedb,r.rolcanlogin,r.rolreplication,r.rolbypassrls,
 pg_catalog.pg_has_role(session_user,r.oid,'MEMBER') AS session_member,
 pg_catalog.pg_has_role(session_user,r.oid,'USAGE') AS session_usage,
 pg_catalog.pg_has_role(current_user,r.oid,'MEMBER') AS current_member,
 pg_catalog.pg_has_role(current_user,r.oid,'USAGE') AS current_usage
FROM pg_catalog.pg_roles r WHERE r.rolname IN (session_user,current_user)
 OR pg_catalog.pg_has_role(session_user,r.oid,'MEMBER')
 OR pg_catalog.pg_has_role(current_user,r.oid,'MEMBER')
ORDER BY r.oid LIMIT 257;
""",
    """WITH RECURSIVE roots(oid) AS (
 SELECT c.oid FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname IN (
 'executions','workflow_execution_attempts','agent_runs','agent_run_steps',
 'execution_attempts','execution_lifecycle_events','work_deliveries',
 'execution_logs','ai_usage','agent_run_verdict_history',
 'agent_run_flag_conversations','poison_message_dispositions')),
 fk AS (SELECT * FROM pg_catalog.pg_constraint WHERE contype='f'),
 a(oid) AS (SELECT oid FROM roots UNION
 SELECT k.confrelid FROM fk k JOIN a ON k.conrelid=a.oid),
 d(oid) AS (SELECT oid FROM roots UNION
 SELECT k.conrelid FROM fk k JOIN d ON k.confrelid=d.oid),
 v(oid) AS (SELECT oid FROM a UNION SELECT oid FROM d),
 e AS (SELECT k.* FROM fk k WHERE k.conrelid IN (SELECT oid FROM v)
 OR k.confrelid IN (SELECT oid FROM v)),
 s(oid) AS (SELECT oid FROM v UNION SELECT conrelid FROM e
 UNION SELECT confrelid FROM e)
""",
    """SELECT c.oid::bigint AS relation_oid,n.nspname,c.relname,c.relkind::text,
 c.relowner::bigint AS owner_oid,pg_catalog.pg_get_userbyid(c.relowner) AS owner,
 n.nspowner::bigint AS schema_owner_oid,
 pg_catalog.pg_get_userbyid(n.nspowner) AS schema_owner,
 c.relrowsecurity,c.relforcerowsecurity,c.relispartition,
 EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h
 WHERE h.inhrelid=c.oid OR h.inhparent=c.oid) AS has_inheritance,
 c.oid IN (SELECT oid FROM roots) AS is_root,
 c.oid IN (SELECT oid FROM a) AS is_ancestor,
 c.oid IN (SELECT oid FROM d) AS is_dependent,
 c.oid NOT IN (SELECT oid FROM v) AS is_boundary,
 pg_catalog.has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage,
 pg_catalog.has_schema_privilege(current_user,n.oid,'CREATE') AS schema_create,
 pg_catalog.has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
 pg_catalog.has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
 pg_catalog.has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
 pg_catalog.has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,
 pg_catalog.has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,
 pg_catalog.has_table_privilege(current_user,c.oid,'REFERENCES') AS can_reference,
 pg_catalog.has_table_privilege(current_user,c.oid,'TRIGGER') AS can_trigger
FROM s JOIN pg_catalog.pg_class c ON c.oid=s.oid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace ORDER BY c.oid LIMIT 257;
""",
    """SELECT k.oid::bigint AS constraint_oid,k.conname,
 k.conrelid::bigint AS child_oid,k.confrelid::bigint AS parent_oid,
 k.conkey,k.confkey,
 ARRAY(SELECT z.attname FROM unnest(k.conkey) WITH ORDINALITY x(num,ord)
 JOIN pg_catalog.pg_attribute z ON z.attrelid=k.conrelid AND z.attnum=x.num
 ORDER BY x.ord) AS child_columns,
 ARRAY(SELECT z.attname FROM unnest(k.confkey) WITH ORDINALITY x(num,ord)
 JOIN pg_catalog.pg_attribute z ON z.attrelid=k.confrelid AND z.attnum=x.num
 ORDER BY x.ord) AS parent_columns,
 k.confdeltype::text,k.confupdtype::text,k.confmatchtype::text,
 k.condeferrable,k.condeferred,k.convalidated,k.conislocal,k.coninhcount,
 k.conparentid::bigint AS parent_constraint_oid
FROM e k ORDER BY k.oid LIMIT 1025;
""",
    """SELECT k.oid::bigint AS constraint_oid,k.conrelid::bigint AS relation_oid,
 k.conname,k.contype::text,k.conkey,
 ARRAY(SELECT z.attname FROM unnest(k.conkey) WITH ORDINALITY x(num,ord)
 JOIN pg_catalog.pg_attribute z ON z.attrelid=k.conrelid AND z.attnum=x.num
 ORDER BY x.ord) AS columns,k.conindid::bigint AS index_oid,
 k.condeferrable,k.condeferred,k.convalidated,k.conislocal,k.coninhcount,
 k.connoinherit,k.conparentid::bigint AS parent_constraint_oid,
 CASE WHEN k.conindid=0 THEN NULL ELSE i.indisvalid END AS index_valid,
 CASE WHEN k.conindid=0 THEN NULL ELSE i.indnullsnotdistinct END AS nulls_not_distinct
FROM pg_catalog.pg_constraint k LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=k.conindid
WHERE k.conrelid IN (SELECT oid FROM s) AND k.contype IN ('p','u','c')
ORDER BY k.oid LIMIT 2049;
""",
    """SELECT t.oid::bigint AS trigger_oid,t.tgrelid::bigint AS relation_oid,t.tgname,
 t.tgenabled::text,t.tgisinternal,t.tgtype,t.tgattr::smallint[] AS trigger_columns,
 t.tgconstraint::bigint AS constraint_oid,t.tgconstrrelid::bigint AS related_oid,
 t.tgparentid::bigint AS parent_trigger_oid,t.tgdeferrable,t.tginitdeferred,t.tgnargs,
 t.tgnargs>0 AS has_trigger_arguments,t.tgqual IS NOT NULL AS has_trigger_condition,
 ck.contype::text AS trigger_constraint_type,
 ck.conrelid::bigint AS constraint_relation_oid,
 ck.confrelid::bigint AS referenced_relation_oid,
 CASE WHEN ck.contype='f' AND (ck.conrelid IN (SELECT oid FROM v)
 OR ck.confrelid IN (SELECT oid FROM v)) THEN 'incident_fk'
 WHEN t.tgconstraint<>0 OR t.tgconstrrelid<>0 THEN 'outside_incident_scope'
 ELSE 'unbound' END AS reference_scope,
 p.oid::bigint AS function_oid,n.nspname AS function_schema,p.proname,
 n.nspowner::bigint AS function_schema_owner_oid,
 pg_catalog.pg_get_userbyid(n.nspowner) AS function_schema_owner,
 p.proowner::bigint AS function_owner_oid,
 pg_catalog.pg_get_userbyid(p.proowner) AS function_owner,
 o.rolsuper AS function_owner_super,o.rolbypassrls AS function_owner_bypassrls,
 p.prosecdef,p.proleakproof,p.provolatile::text,p.proparallel::text,
 p.proargtypes::oid[] AS argument_type_oids,p.prorettype::bigint AS return_type_oid,
 p.proconfig IS NOT NULL AS has_function_configuration,
 EXISTS (SELECT 1 FROM unnest(p.proconfig) z(setting)
 WHERE split_part(z.setting,'=',1)='search_path') AS has_configured_search_path,
 pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute
FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_roles o ON o.oid=p.proowner
LEFT JOIN pg_catalog.pg_constraint ck ON ck.oid=t.tgconstraint
WHERE t.tgrelid IN (SELECT oid FROM s) ORDER BY t.oid LIMIT 4097;
""",
    """SELECT p.oid::bigint AS policy_oid,p.polrelid::bigint AS relation_oid,p.polname,
 p.polcmd::text,p.polpermissive,p.polroles::oid[] AS role_oids,
 p.polqual IS NOT NULL AS has_using_expression,
 p.polwithcheck IS NOT NULL AS has_check_expression
FROM pg_catalog.pg_policy p WHERE p.polrelid IN (SELECT oid FROM s)
ORDER BY p.oid LIMIT 1025;
""",
    """SELECT version_num FROM public.alembic_version ORDER BY version_num LIMIT 17;
""",
)
QUERIES = (
    SQL_BODIES[0],
    SQL_BODIES[1],
    SQL_BODIES[2],
    SQL_BODIES[3] + SQL_BODIES[4],
    SQL_BODIES[3] + SQL_BODIES[5],
    SQL_BODIES[3] + SQL_BODIES[6],
    SQL_BODIES[3] + SQL_BODIES[7],
    SQL_BODIES[3] + SQL_BODIES[8],
    SQL_BODIES[9],
)
EXPECTED_QUERY_HASHES = (
    "fade2cb15750e2b299848b1bb77ffba4fbeef7af8e9468069ddfd0ae3f777a65",
    "dc1656cd0ddd84907420e74ca2f80f3f25e33985336e49a81abefc335101efc8",
    "4960eee678dfad793c12f9a3571069d542aa20b5ca779d613ad55da6ca7feea8",
    "e9cf78d3cf5ddf67fcf69bffe1a847ae54542461e819cfc47077dbbe67b670cb",
    "dff0b186c1643261fcfb5e87867971b174d04c73eed352e519733c4548f4d8b2",
    "ac61db30e35307b546d042eb1b8ae602f7eb32e5d0023b651495072bb9e316a3",
    "ce2dbc1d5163f3d56b0121bc90a226e93f99ade802cd41f62b72bd25e99ae6d7",
    "3e7af6b4443809d53a86a1766979eadd6ceeabf947468e69cab459603eee8b10",
    "bed514154f5e69c22a4de9ce881886324defe829d3124680b520113da4e722d6",
)


class CatalogCollectionError(RuntimeError):
    """A fixed failure stage, raised outside the driver exception context."""


def _budget(deadline, maximum=5):
    remaining = deadline - monotonic()
    contract.require(remaining > 0, "absolute-deadline-expired")
    return min(maximum, remaining)


def _check_deadline(deadline):
    contract.require(monotonic() < deadline, "absolute-deadline-expired")


def _query_hashes():
    observed = tuple(
        hashlib.sha256(query.encode("utf-8")).hexdigest() for query in QUERIES
    )
    contract.require(observed == EXPECTED_QUERY_HASHES, "fixed-query-source-hash")
    return observed


def _admit_url():
    url = make_url(TEST_DATABASE_URL)
    contract.require(
        url.drivername == "postgresql+asyncpg" and not url.query, "fixed-dsn-driver"
    )
    contract.require(url.host == "pgbouncer" and url.port == 5432, "fixed-dsn-host")
    contract.require(
        url.database == "bifrost_test" and url.username == "bifrost",
        "fixed-dsn-database",
    )
    contract.require(
        type(url.password) is str and bool(url.password), "fixed-dsn-password-presence"
    )
    return url.set(drivername="postgresql")


def _driver_rows(query_index, records):
    contract.require(
        type(records) is list and len(records) <= contract.ROW_CAPS[query_index],
        "row-cap",
    )
    contract.require(
        all(type(record) is asyncpg.Record for record in records), "native-record-type"
    )
    rows = [dict(record) for record in records]
    contract.validate_rows(query_index, rows)
    if rows:
        # Real Record positive seam plus malformed local copies; no extra SQL.
        bad_type = dict(rows[0])
        first_column = contract.SCHEMAS[query_index][0][0]
        bad_type[first_column] = object()
        missing_field = dict(rows[0])
        del missing_field[first_column]
        for bad_rows in (
            [bad_type],
            [missing_field],
            [rows[0]] * (contract.ROW_CAPS[query_index] + 1),
        ):
            rejected = False
            try:
                contract.validate_rows(query_index, bad_rows)
            except contract.CatalogContractError:
                rejected = True
            contract.require(rejected, "actual-record-local-red-case")
    return rows


async def _transaction_operation(transaction, operation, deadline):
    budget = _budget(deadline)
    async with asyncio.timeout_at(min(deadline, monotonic() + budget)):
        if operation == "start":
            await transaction.start()
        elif operation == "commit":
            await transaction.commit()
        elif operation == "rollback":
            await transaction.rollback()
        else:
            raise contract.CatalogContractError("fixed-transaction-operation")
    _check_deadline(deadline)


def _terminate(connection):
    failed = False
    try:
        connection.terminate()
    except Exception:
        failed = True
    return not failed


async def _cleanup_connection(connection, transaction, transaction_started, deadline):
    if connection is None:
        return True
    failed = False
    if transaction_started:
        if monotonic() >= deadline:
            _terminate(connection)
            return False
        try:
            await _transaction_operation(transaction, "rollback", deadline)
        except (Exception, asyncio.CancelledError):
            failed = True
        if failed:
            _terminate(connection)
            return False
    if monotonic() >= deadline:
        _terminate(connection)
        return False
    try:
        budget = _budget(deadline, 2)
        async with asyncio.timeout_at(min(deadline, monotonic() + budget)):
            await connection.close(timeout=budget)
        _check_deadline(deadline)
        contract.require(connection.is_closed(), "connection-close-incomplete")
    except (Exception, asyncio.CancelledError):
        failed = True
    if failed:
        _terminate(connection)
    return not failed


async def _snapshot(url, endpoint, deadline):
    contract.require(
        type(endpoint) is str and endpoint in {"direct", "pool"}, "fixed-endpoint-label"
    )
    connection = None
    transaction = None
    transaction_started = False
    observations = []
    stage = "connect"
    failed_stage = None
    started_at = datetime.now(timezone.utc)
    started = monotonic()
    try:
        budget = _budget(deadline)
        async with asyncio.timeout_at(min(deadline, monotonic() + budget)):
            connection = await asyncpg.connect(
                url.render_as_string(hide_password=False),
                statement_cache_size=0,
                timeout=budget,
            )
        _check_deadline(deadline)
        stage = "transaction-enter"
        transaction = connection.transaction(isolation="repeatable_read", readonly=True)
        # A failed start may have reached the server; cleanup closes/terminates it.
        await _transaction_operation(transaction, "start", deadline)
        transaction_started = True
        for query_index, query in enumerate(QUERIES):
            stage = f"Q{query_index}"
            budget = _budget(deadline)
            async with asyncio.timeout_at(min(deadline, monotonic() + budget)):
                records = await connection.fetch(query, timeout=budget)
            _check_deadline(deadline)
            observations.append(_driver_rows(query_index, records))
        stage = "cross-query-contract"
        graph_counts = contract.validate_snapshot(observations)
        stage = "transaction-exit"
        await _transaction_operation(transaction, "commit", deadline)
        transaction_started = False
    except (Exception, asyncio.CancelledError):
        failed_stage = stage
    # Outside except: never retain/log the driver exception or its context.
    clean = await _cleanup_connection(
        connection, transaction, transaction_started, deadline
    )
    if not clean and failed_stage is None:
        failed_stage = "connection-cleanup"
    if failed_stage is not None:
        raise CatalogCollectionError(f"writer-catalog:{endpoint}:{failed_stage}")
    _check_deadline(deadline)
    snapshot = {
        "endpoint": endpoint,
        "sample_index": 0,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc),
        "elapsed_seconds": monotonic() - started,
        "graph_counts": graph_counts,
        "queries": contract.tabular_observations(observations),
        "codec_evidence": [
            {
                "query": f"Q{index}",
                "actual_row_count": len(rows),
                "observed_empty": not rows,
                "native_records_validated": bool(rows),
                "local_copy_red_cases": [
                    "wrong-native-type",
                    "missing-field",
                    "row-cap",
                ]
                if rows
                else [],
                "nonempty_row_classes_not_observed": not rows,
                "nonempty_array_fields_observed": [
                    name
                    for name, rule in contract.SCHEMAS[index]
                    if rule.startswith("A")
                    and any(type(row[name]) is list and bool(row[name]) for row in rows)
                ],
                "nonempty_array_fields_not_observed": [
                    name
                    for name, rule in contract.SCHEMAS[index]
                    if rule.startswith("A")
                    and not any(
                        type(row[name]) is list and bool(row[name]) for row in rows
                    )
                ],
            }
            for index, rows in enumerate(observations)
        ],
    }
    contract.encode_bounded(snapshot, contract.SNAPSHOT_BYTES)
    _check_deadline(deadline)
    return observations, snapshot


def _directory_facts(info):
    contract.require(stat.S_ISDIR(info.st_mode), "results-directory-type")
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "owner_uid": info.st_uid,
        "mode_octal": format(stat.S_IMODE(info.st_mode), "04o"),
    }


def _file_facts(info):
    contract.require(
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "receipt-file-type"
    )
    contract.require(
        info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o644,
        "receipt-file-owner-mode",
    )
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "owner_uid": info.st_uid,
        "mode_octal": "0644",
        "links": info.st_nlink,
    }


def _check_directory(directory_fd, directory_path, expected, deadline):
    observed = _directory_facts(os.fstat(directory_fd))
    _check_deadline(deadline)
    by_path = _directory_facts(os.stat(directory_path, follow_symlinks=False))
    _check_deadline(deadline)
    contract.require(
        observed == expected == by_path, "results-directory-identity-drift"
    )


def _write_receipt(receipt, deadline):
    directory_fd = None
    file_fd = None
    failed = False
    result = None
    path = Path(RECEIPT_PATH)
    try:
        _check_deadline(deadline)
        directory_fd = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        _check_deadline(deadline)
        directory = _directory_facts(os.fstat(directory_fd))
        _check_deadline(deadline)
        _check_directory(directory_fd, path.parent, directory, deadline)
        file_fd = os.open(
            path.name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o644,
            dir_fd=directory_fd,
        )
        _check_deadline(deadline)
        # Only the newly exclusive-created descriptor; never chmod existing work.
        os.fchmod(file_fd, 0o644)
        _check_deadline(deadline)
        file = _file_facts(os.fstat(file_fd))
        _check_deadline(deadline)
        receipt["custody"] = {
            "path": RECEIPT_PATH,
            "directory": directory,
            "file": file,
            "directory_authority": "not_established",
            "verification": "requires_named_testcase_pass",
        }
        data = contract.encode_bounded(receipt, contract.RECEIPT_BYTES)
        _check_deadline(deadline)
        offset = 0
        while offset < len(data):
            written = os.write(file_fd, data[offset:])
            _check_deadline(deadline)
            contract.require(
                type(written) is int and 0 < written <= len(data) - offset,
                "receipt-write-progress",
            )
            offset += written
        os.fsync(file_fd)
        _check_deadline(deadline)
        os.lseek(file_fd, 0, os.SEEK_SET)
        _check_deadline(deadline)
        readback = bytearray()
        while True:
            chunk = os.read(
                file_fd, min(65536, contract.RECEIPT_BYTES + 1 - len(readback))
            )
            _check_deadline(deadline)
            if not chunk:
                break
            readback.extend(chunk)
            contract.require(
                len(readback) <= contract.RECEIPT_BYTES, "receipt-readback-byte-cap"
            )
        contract.require(bytes(readback) == data, "receipt-readback-mismatch")
        final = os.fstat(file_fd)
        _check_deadline(deadline)
        by_path = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        _check_deadline(deadline)
        contract.require(
            _file_facts(final) == file == _file_facts(by_path),
            "receipt-file-identity-drift",
        )
        contract.require(
            final.st_size == by_path.st_size == len(data), "receipt-file-size-mismatch"
        )
        _check_directory(directory_fd, path.parent, directory, deadline)
        result = {"byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    except Exception:
        failed = True
    # Synchronous descriptor close is mandatory even after expiry; kernel I/O
    # is not preemptible. A late close still prevents successful collection.
    for descriptor in (file_fd, directory_fd):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                failed = True
            if monotonic() >= deadline:
                failed = True
    if failed:
        raise CatalogCollectionError("writer-catalog:receipt-custody")
    _check_deadline(deadline)
    return result


def _source_hashes(deadline):
    test_path = Path(__file__)
    contract.require(
        test_path.name == "test_writer_catalog_receipt.py"
        and tuple(parent.name for parent in test_path.parents[:3])
        == ("platform", "e2e", "tests"),
        "admitted-test-source-path",
    )
    helper_path = test_path.parents[2] / "helpers" / "writer_catalog_receipt.py"
    hashes = {}
    for label, path in (
        ("test_writer_catalog_receipt.py", test_path),
        ("writer_catalog_receipt.py", helper_path),
    ):
        fd = None
        failed = False
        try:
            _check_deadline(deadline)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            _check_deadline(deadline)
            before = os.fstat(fd)
            _check_deadline(deadline)
            contract.require(
                stat.S_ISREG(before.st_mode) and 0 < before.st_size <= SOURCE_BYTES,
                "source-file-type-size",
            )
            content = bytearray()
            while True:
                chunk = os.read(fd, min(65536, SOURCE_BYTES + 1 - len(content)))
                _check_deadline(deadline)
                if not chunk:
                    break
                content.extend(chunk)
                contract.require(len(content) <= SOURCE_BYTES, "source-byte-cap")
            after = os.fstat(fd)
            _check_deadline(deadline)
            contract.require(
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                and len(content) == after.st_size,
                "source-file-drift",
            )
            hashes[label] = hashlib.sha256(content).hexdigest()
        except Exception:
            failed = True
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                failed = True
        if failed:
            raise CatalogCollectionError("writer-catalog:source-bytes")
        _check_deadline(deadline)
    return hashes


async def test_collect_existing_writer_catalog_receipt():
    failed = False
    deadline = monotonic() + DEADLINE_SECONDS
    try:
        async with asyncio.timeout_at(deadline):
            query_hashes = _query_hashes()
            url = _admit_url()
            # No task/connect is started for an expired deadline.
            expired_rejected = False
            try:
                await _snapshot(url.set(host="postgres"), "direct", monotonic() - 1)
            except CatalogCollectionError:
                expired_rejected = True
            contract.require(expired_rejected, "expired-deadline-red-case")
            direct_rows, direct = await _snapshot(
                url.set(host="postgres"), "direct", deadline
            )
            pool_rows, pool = await _snapshot(url, "pool", deadline)
            contract.compare_snapshots(direct_rows, pool_rows)
            receipt = {
                "schema": "bifrost.writer-catalog-receipt/v1",
                "purpose": "existing-isolated-test-db-catalog-graph",
                "reference_source_main": contract.REFERENCE_SOURCE_MAIN,
                "source_sha256": _source_hashes(deadline),
                "contract_sha256": contract.contract_sha256(),
                "query_sha256": {
                    f"Q{index}": digest for index, digest in enumerate(query_hashes)
                },
                "collection_status": "raw_complete_pending_custody",
                "reconciliation_status": "reconciliation_pending",
                "security_readiness": "not_established",
                "catalog_char_representation": "PG16 internal char explicitly cast to text; native values retained",
                "deadline_seconds": DEADLINE_SECONDS,
                "scope": "existing-isolated-test-runner-direct-and-pool",
                "limits": [
                    "directional-closure-and-one-hop-boundary-only",
                    "no-expression-body-argument-or-configuration-value-proof",
                    "observed-empty-does-not-prove-nonempty-codecs",
                    "not-api-worker-scheduler-session-or-deployment-proof",
                    "actual-commit-tree-run-shard-image-binding-is-external",
                    "parent-directory-mode-is-not-authority-proof",
                    "named-testcase-pass-required-for-custody",
                ],
                "samples": [direct, pool],
            }
            _write_receipt(receipt, deadline)
    except (Exception, asyncio.CancelledError):
        failed = True
    if failed:
        pytest.fail(
            "Writer catalog collection failed; no source/security readiness proof.",
            pytrace=False,
        )
