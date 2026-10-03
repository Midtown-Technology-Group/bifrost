"""Supported-container-only fixed SELECT identity primer; no application imports."""

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import asyncpg

DATABASE = "bifrost_wex_primer"
HEAD = "20261001_solution_src_account"
ROLES = {
    "wex_incumbent": "synthetic_primer_incumbent",
    "wex_core": "synthetic_primer_core",
}
PAIRS = (
    ("postgres", "wex_incumbent"),
    ("postgres", "wex_core"),
    ("pool", "wex_incumbent"),
    ("pool", "wex_core"),
)
OUTPUT_LIMIT = 2 * 1024 * 1024
QUERIES = {
    "Q0": "SELECT session_user::text AS session_user,current_user::text AS current_user,current_database()::text AS database_name,current_setting('server_version_num')::integer AS server_version_num,pg_backend_pid()::integer AS backend_pid",
    "Q1": "SELECT oid::bigint,rolname::text,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname IN ('bifrost','wex_incumbent','wex_core') ORDER BY oid LIMIT 4",
    "Q2": "SELECT m.roleid::bigint,m.member::bigint,m.grantor::bigint,m.admin_option,m.inherit_option,m.set_option FROM pg_catalog.pg_auth_members m WHERE m.member IN (SELECT oid FROM pg_catalog.pg_roles WHERE rolname IN ('wex_incumbent','wex_core')) ORDER BY m.member,m.roleid LIMIT 17",
    "Q3": "SELECT d.oid::bigint,d.datdba::bigint,n.oid::bigint AS schema_oid,n.nspowner::bigint,dbowner.rolname::text AS database_owner_name,schemaowner.rolname::text AS schema_owner_name,has_database_privilege(current_user,d.oid,'CONNECT') AS can_connect,has_database_privilege(current_user,d.oid,'CREATE') AS can_create_database_objects,has_database_privilege(current_user,d.oid,'TEMP') AS can_temp,has_schema_privilege(current_user,n.oid,'USAGE') AS can_use_schema,has_schema_privilege(current_user,n.oid,'CREATE') AS can_create_schema_objects FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles dbowner ON dbowner.oid=d.datdba CROSS JOIN pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles schemaowner ON schemaowner.oid=n.nspowner WHERE d.datname=current_database() AND n.nspname='public'",
    "Q4": "SELECT c.oid::bigint,c.relname::text,c.relkind::text,c.relowner::bigint,has_table_privilege(current_user,c.oid,'SELECT') AS can_select,has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,has_table_privilege(current_user,c.oid,'REFERENCES') AS can_reference,has_table_privilege(current_user,c.oid,'TRIGGER') AS can_trigger FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') ORDER BY c.oid LIMIT 513",
    "Q5": "SELECT p.oid::bigint,n.nspname::text,p.proname::text,p.proowner::bigint,p.prosecdef,p.proleakproof,p.prokind::text,has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY p.oid LIMIT 2049",
    "Q6": "SELECT version_num FROM public.alembic_version ORDER BY version_num LIMIT 2",
    "Q7": "SELECT oid::bigint,extname::text,extversion::text,extowner::bigint FROM pg_catalog.pg_extension WHERE extname='vector' ORDER BY oid LIMIT 2",
    "Q8": "WITH wanted(signature) AS (VALUES ('pg_catalog.pg_read_file(text)'),('pg_catalog.pg_read_file(text,boolean)'),('pg_catalog.pg_read_file(text,bigint,bigint)'),('pg_catalog.pg_read_file(text,bigint,bigint,boolean)'),('pg_catalog.pg_read_binary_file(text)'),('pg_catalog.pg_read_binary_file(text,boolean)'),('pg_catalog.pg_read_binary_file(text,bigint,bigint)'),('pg_catalog.pg_read_binary_file(text,bigint,bigint,boolean)'),('pg_catalog.pg_stat_file(text)'),('pg_catalog.pg_stat_file(text,boolean)'),('pg_catalog.pg_ls_dir(text)'),('pg_catalog.pg_ls_dir(text,boolean,boolean)'),('pg_catalog.lo_import(text)'),('pg_catalog.lo_import(text,oid)'),('pg_catalog.lo_export(oid,text)'),('pg_catalog.pg_reload_conf()'),('pg_catalog.pg_rotate_logfile()'),('pg_catalog.pg_ls_logdir()'),('pg_catalog.pg_ls_waldir()'),('pg_catalog.pg_log_backend_memory_contexts(integer)')) SELECT w.signature,p.oid::bigint AS function_oid,pg_catalog.has_function_privilege(current_user,p.oid,'EXECUTE') AS can_execute FROM wanted w LEFT JOIN pg_catalog.pg_proc p ON p.oid=pg_catalog.to_regprocedure(w.signature) ORDER BY w.signature LIMIT 21",
}
SIGNATURES = (
    "pg_catalog.lo_export(oid,text)",
    "pg_catalog.lo_import(text)",
    "pg_catalog.lo_import(text,oid)",
    "pg_catalog.pg_log_backend_memory_contexts(integer)",
    "pg_catalog.pg_ls_dir(text)",
    "pg_catalog.pg_ls_dir(text,boolean,boolean)",
    "pg_catalog.pg_ls_logdir()",
    "pg_catalog.pg_ls_waldir()",
    "pg_catalog.pg_read_binary_file(text)",
    "pg_catalog.pg_read_binary_file(text,bigint,bigint)",
    "pg_catalog.pg_read_binary_file(text,bigint,bigint,boolean)",
    "pg_catalog.pg_read_binary_file(text,boolean)",
    "pg_catalog.pg_read_file(text)",
    "pg_catalog.pg_read_file(text,bigint,bigint)",
    "pg_catalog.pg_read_file(text,bigint,bigint,boolean)",
    "pg_catalog.pg_read_file(text,boolean)",
    "pg_catalog.pg_reload_conf()",
    "pg_catalog.pg_rotate_logfile()",
    "pg_catalog.pg_stat_file(text)",
    "pg_catalog.pg_stat_file(text,boolean)",
)
CAPS = (1, 3, 16, 1, 512, 2048, 1, 1, 20)

COLUMNS = {
    "Q0": [
        "session_user",
        "current_user",
        "database_name",
        "server_version_num",
        "backend_pid",
    ],
    "Q1": [
        "oid",
        "rolname",
        "rolsuper",
        "rolinherit",
        "rolcreaterole",
        "rolcreatedb",
        "rolcanlogin",
        "rolreplication",
        "rolbypassrls",
    ],
    "Q2": [
        "roleid",
        "member",
        "grantor",
        "admin_option",
        "inherit_option",
        "set_option",
    ],
    "Q3": [
        "oid",
        "datdba",
        "schema_oid",
        "nspowner",
        "database_owner_name",
        "schema_owner_name",
        "can_connect",
        "can_create_database_objects",
        "can_temp",
        "can_use_schema",
        "can_create_schema_objects",
    ],
    "Q4": [
        "oid",
        "relname",
        "relkind",
        "relowner",
        "can_select",
        "can_insert",
        "can_update",
        "can_delete",
        "can_truncate",
        "can_reference",
        "can_trigger",
    ],
    "Q5": [
        "oid",
        "nspname",
        "proname",
        "proowner",
        "prosecdef",
        "proleakproof",
        "prokind",
        "can_execute",
    ],
    "Q6": ["version_num"],
    "Q7": ["oid", "extname", "extversion", "extowner"],
    "Q8": ["signature", "function_oid", "can_execute"],
}
EMPTY_COLUMNS = COLUMNS
TYPES = {
    "Q0": (str, str, str, int, int),
    "Q1": (int, str, bool, bool, bool, bool, bool, bool, bool),
    "Q2": (int, int, int, bool, bool, bool),
    "Q3": (int, int, int, int, str, str, bool, bool, bool, bool, bool),
    "Q4": (int, str, str, int, bool, bool, bool, bool, bool, bool, bool),
    "Q5": (int, str, str, int, bool, bool, str, bool),
    "Q6": (str,),
    "Q7": (int, str, str, int),
    "Q8": (str, int, bool),
}


class PrimerFailure(Exception):
    """Static failure; never retain driver exception/DSN details."""


def require(condition):
    if not condition:
        raise PrimerFailure("primer admission failed")


def remaining(deadline):
    value = deadline - time.monotonic()
    require(value > 0)
    return value


async def connect(host, role, deadline, password=None, database=DATABASE):
    return await asyncpg.connect(
        host=host,
        port=5432,
        user=role,
        password=ROLES.get(role) if password is None else password,
        database=database,
        statement_cache_size=0,
        timeout=min(5, remaining(deadline)),
        command_timeout=5,
    )


async def close(connection, deadline):
    if connection is None:
        return
    try:
        async with asyncio.timeout(min(5, remaining(deadline))):
            await connection.close()
    finally:
        original = sys.exception()
        try:
            if not connection.is_closed():
                connection.terminate()
        except BaseException:
            if original is None:
                raise


def native(value):
    if type(value) is bool:
        return
    if type(value) is int:
        require(value > 0)
        return
    if type(value) is str:
        require(0 < len(value.encode("utf-8")) <= 128)
        return
    raise PrimerFailure("primer native field failed")


def validate(tables, role):
    identity = dict(zip(tables[0]["columns"], tables[0]["rows"][0], strict=True))
    require(identity["session_user"] == identity["current_user"] == role)
    require(identity["database_name"] == DATABASE)
    require(identity["server_version_num"] // 10000 == 16)
    rows = tables[1]["rows"]
    require(len(rows) == 3 and {r[1] for r in rows} == {"bifrost", *ROLES})
    owners = {r[1]: r[0] for r in rows}
    writer_oids = {owners[r] for r in ROLES}
    for r in rows:
        if r[1] in ROLES:
            require(r[6] is True and all(r[i] is False for i in (2, 4, 5, 7, 8)))
    require(not tables[2]["rows"])
    r = tables[3]["rows"][0]
    require(r[1] == owners["bifrost"] and r[4] == "bifrost")
    require(r[5] in ("bifrost", "pg_database_owner") and r[3] not in writer_oids)
    require(r[6:] == [True, False, False, True, False])
    require(tables[4]["rows"])
    for r in tables[4]["rows"]:
        require(r[2] in ("r", "p", "v", "m", "f") and r[3] == owners["bifrost"])
        require(r[4] is True and all(v is False for v in r[5:]))
    for r in tables[5]["rows"]:
        require(r[6] in ("f", "p", "a", "w"))
        require(r[3] not in writer_oids and not (r[4] and r[7]))
    require(tables[6]["rows"] == [[HEAD]])
    r = tables[7]["rows"][0]
    require(r[1] == "vector" and len(r[2].encode()) <= 64 and r[3] == owners["bifrost"])
    require(tuple(r[0] for r in tables[8]["rows"]) == SIGNATURES)
    require(all(r[2] is False for r in tables[8]["rows"]))
    return identity


async def snapshot(connection, role, deadline):
    tables = []
    for i, (name, sql) in enumerate(QUERIES.items()):
        async with asyncio.timeout(min(5, remaining(deadline))):
            records = await connection.fetch(sql)
        require(len(records) <= CAPS[i])
        if i in (0, 3, 6, 7):
            require(len(records) == 1)
        columns = list(records[0].keys()) if records else EMPTY_COLUMNS[name]
        require(columns == COLUMNS[name])
        rows = [list(r.values()) for r in records]
        for row in rows:
            require(len(row) == len(columns))
            for value, kind in zip(row, TYPES[name], strict=True):
                require(type(value) is kind)
                native(value)
        require(len({tuple(r) for r in rows}) == len(rows))
        tables.append({"query": name, "columns": columns, "rows": rows})
    del name  # Cross-query validation is not attributed to the last fetch.
    identity = validate(tables, role)
    digest = hashlib.sha256()
    used = 0
    for part in json.JSONEncoder(ensure_ascii=True, separators=(",", ":")).iterencode(
        tables[1:]
    ):
        chunk = part.encode()
        used += len(chunk)
        require(used <= OUTPUT_LIMIT)
        digest.update(chunk)
    return tables, identity, digest.hexdigest()


async def transaction_sample(connection, role, deadline, commit):
    tx = connection.transaction()
    async with asyncio.timeout(remaining(deadline)):
        await tx.start()
    try:
        sample = await snapshot(connection, role, deadline)
        if commit:
            async with asyncio.timeout(remaining(deadline)):
                await tx.commit()
        else:
            async with asyncio.timeout(remaining(deadline)):
                await tx.rollback()
        return sample
    except BaseException:
        # Terminating a failed transport closes its active transaction; no repair.
        try:
            connection.terminate()
        except BaseException:
            pass  # Preserve the original failure/control exception.
        raise


async def escape_negatives(connection, role, deadline):
    opposite = "wex_core" if role == "wex_incumbent" else "wex_incumbent"
    results = []
    for statement in (
        "SET LOCAL ROLE bifrost",
        f"SET LOCAL ROLE {opposite}",
        "SET SESSION AUTHORIZATION bifrost",
    ):
        tx = connection.transaction()
        async with asyncio.timeout(remaining(deadline)):
            await tx.start()
        denied = False
        try:
            async with asyncio.timeout(min(5, remaining(deadline))):
                await connection.execute(statement)
        except asyncpg.PostgresError as error:
            denied = error.sqlstate == "42501"
        finally:
            original = sys.exception()
            try:
                async with asyncio.timeout(remaining(deadline)):
                    await tx.rollback()
            except BaseException:
                if original is None:
                    raise
        require(denied)
        async with asyncio.timeout(min(5, remaining(deadline))):
            row = await connection.fetchrow(QUERIES["Q0"])
        require(row["session_user"] == row["current_user"] == role)
        require(
            row["database_name"] == DATABASE
            and row["server_version_num"] // 10000 == 16
        )
        results.append({"statement": statement, "sqlstate": "42501"})
    return results


NEGATIVE_CASES = {
    "postgres_wrong_password": (
        "postgres",
        "wex_incumbent",
        "synthetic_wrong_password",
        DATABASE,
    ),
    "postgres_unknown_user": (
        "postgres",
        "wex_unknown",
        "synthetic_unknown_password",
        DATABASE,
    ),
    "pool_wrong_password": (
        "pool",
        "wex_incumbent",
        "synthetic_wrong_password",
        DATABASE,
    ),
    "pool_unknown_user": (
        "pool",
        "wex_unknown",
        "synthetic_unknown_password",
        DATABASE,
    ),
    "pool_admin_user": ("pool", "bifrost", "synthetic_primer_admin", DATABASE),
    "pool_foreign_alias": (
        "pool",
        "wex_incumbent",
        ROLES["wex_incumbent"],
        "foreign_primer_alias",
    ),
}
POOL_REASONS = {
    "pool_wrong_password": "password authentication failed",
    "pool_unknown_user": "password authentication failed",
    "pool_admin_user": "password authentication failed",
    "pool_foreign_alias": "no such database: foreign_primer_alias",
}


def classify_rejection(case_id, parameters, error, *, returned=False):
    if (
        case_id not in NEGATIVE_CASES
        or parameters != NEGATIVE_CASES[case_id]
        or returned
    ):
        return "unmatched"
    if isinstance(error, asyncpg.PostgresError) and error.sqlstate in (
        "28P01",
        "28000",
        "3D000",
    ):
        return "postgres_rejection"  # Preserve the existing SQLSTATE path.
    if (
        case_id in POOL_REASONS
        and type(error) is asyncpg.ProtocolViolationError
        and error.sqlstate == "08P01"
        and error.severity == "FATAL"
        and error.message == POOL_REASONS[case_id]
    ):
        return (
            "pool_unknown_database"
            if case_id == "pool_foreign_alias"
            else "pool_password_rejection"
        )
    return "unmatched"


def negative_observation(case_id, outcome, error, signature):
    host, role, _, database = NEGATIVE_CASES[case_id]
    names = {
        "ProtocolViolationError",
        "PostgresError",
        "InvalidPasswordError",
        "InvalidAuthorizationSpecificationError",
        "InvalidCatalogNameError",
        "TimeoutError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "OSError",
        "EOFError",
    }
    observed = type(error).__name__ if error is not None else None
    state = getattr(error, "sqlstate", None)
    return {
        "case_id": case_id,
        "endpoint": host,
        "role": role,
        "database": database,
        "outcome": outcome,
        "exception_type": observed
        if observed in names
        else "other"
        if error is not None
        else None,
        "sqlstate": state if state in {"28P01", "28000", "3D000", "08P01"} else None,
        "reason_signature": signature,
    }


async def rejected_connection(case_id, host, role, password, database, deadline):
    parameters = (host, role, password, database)
    require(case_id in NEGATIVE_CASES and parameters == NEGATIVE_CASES[case_id])
    connection = None
    signature = "unmatched"
    observation = negative_observation(case_id, "not_dispatched", None, signature)
    try:
        try:
            observation = negative_observation(
                case_id, "connection_pending", None, signature
            )
            connection = await connect(host, role, deadline, password, database)
            observation = negative_observation(
                case_id, "returned_connection", None, signature
            )
        except asyncpg.PostgresError as error:
            signature = classify_rejection(case_id, parameters, error)
            observation = negative_observation(
                case_id, "postgres_error", error, signature
            )
        except BaseException as error:
            observation = negative_observation(
                case_id, "connection_failure", error, signature
            )
            raise
        finally:
            original = sys.exception()
            try:
                await close(connection, deadline)
            except BaseException:
                if original is None:
                    raise
        require(signature != "unmatched")
        return {**observation, "denied": True}
    except BaseException as error:
        error.primer_negative_observation = observation
        raise  # Preserve original failure/control; retain only owned closed facts.


def classifier_controls():
    # Actual driver exception classes, synthetic fields; no server/permission proof.
    results = []

    def protocol(
        message, *, state="08P01", severity="FATAL", cls=asyncpg.ProtocolViolationError
    ):
        error = cls(message)
        error.message, error.sqlstate, error.severity = message, state, severity
        return error

    def check(label, case_id, parameters, error, accepted, *, returned=False):
        actual = (
            classify_rejection(case_id, parameters, error, returned=returned)
            != "unmatched"
        )
        require(actual is accepted)
        results.append(
            {
                "control_id": label,
                "expected_rejection_recognized": accepted,
                "observed_rejection_recognized": actual,
            }
        )

    for case_id, message in POOL_REASONS.items():
        parameters = NEGATIVE_CASES[case_id]
        check(case_id + "_positive", case_id, parameters, protocol(message), True)
        for field, replacement in (
            ("sqlstate", "08006"),
            ("sqlstate", None),
            ("severity", "ERROR"),
            ("severity", None),
            ("message", None),
        ):
            error = protocol(message)
            setattr(error, field, replacement)
            check(
                case_id
                + "_wrong_"
                + field
                + ("_missing" if replacement is None else ""),
                case_id,
                parameters,
                error,
                False,
            )
        for index, replacement in enumerate(
            ("postgres", "foreign_role", "foreign_password", "foreign_database")
        ):
            changed = list(parameters)
            changed[index] = replacement
            check(
                case_id + "_tuple_" + str(index),
                case_id,
                tuple(changed),
                protocol(message),
                False,
            )
        for index, changed in enumerate(
            (
                "bad packet",
                "pooler is shutting down",
                "no memory for pool",
                message + "\n",
                message + " suffix",
                "prefix " + message,
            )
        ):
            check(
                case_id + "_message_" + str(index),
                case_id,
                parameters,
                protocol(changed),
                False,
            )
        swapped = (
            POOL_REASONS["pool_wrong_password"]
            if case_id == "pool_foreign_alias"
            else POOL_REASONS["pool_foreign_alias"]
        )
        check(case_id + "_swapped", case_id, parameters, protocol(swapped), False)
        check(
            case_id + "_returned",
            case_id,
            parameters,
            protocol(message),
            False,
            returned=True,
        )
        check(
            case_id + "_unknown_case",
            "unknown_case",
            parameters,
            protocol(message),
            False,
        )
        other_case = (
            "pool_foreign_alias"
            if case_id != "pool_foreign_alias"
            else "pool_wrong_password"
        )
        check(case_id + "_wrong_case", other_case, parameters, protocol(message), False)
        check(
            case_id + "_wrong_class",
            case_id,
            parameters,
            protocol(message, cls=asyncpg.PostgresError),
            False,
        )
        for index, cls in enumerate(
            (
                TimeoutError,
                ConnectionRefusedError,
                ConnectionResetError,
                EOFError,
                OSError,
            )
        ):
            check(
                case_id + "_network_" + str(index),
                case_id,
                parameters,
                cls(message),
                False,
            )
        fact = negative_observation(
            case_id, "postgres_error", protocol(message), "unmatched"
        )
        require(
            set(fact)
            == {
                "case_id",
                "endpoint",
                "role",
                "database",
                "outcome",
                "exception_type",
                "sqlstate",
                "reason_signature",
            }
        )
        require(fact["sqlstate"] == "08P01" and fact["reason_signature"] == "unmatched")
        require(
            message not in json.dumps(fact) and parameters[2] not in json.dumps(fact)
        )

    class DerivedProtocolError(asyncpg.ProtocolViolationError):
        pass

    case_id = "pool_wrong_password"
    parameters = NEGATIVE_CASES[case_id]
    check(
        "subclass",
        case_id,
        parameters,
        protocol(POOL_REASONS[case_id], cls=DerivedProtocolError),
        False,
    )
    for case_id in ("postgres_wrong_password", "postgres_unknown_user"):
        for state, cls in (
            ("28P01", asyncpg.InvalidPasswordError),
            ("28000", asyncpg.InvalidAuthorizationSpecificationError),
            ("3D000", asyncpg.InvalidCatalogNameError),
        ):
            check(
                case_id + "_" + state,
                case_id,
                NEGATIVE_CASES[case_id],
                protocol("synthetic control", state=state, cls=cls),
                True,
            )
    return {"kind": "synthetic_actual_driver_classifier_controls", "controls": results}


async def collect(deadline):
    records = []
    connections = []
    baselines = {}
    try:
        for host, role in PAIRS:
            connection = await connect(host, role, deadline)
            connections.append(connection)
            for cycle in range(10):
                tables, identity, digest = await transaction_sample(
                    connection, role, deadline, cycle % 2 == 0
                )
                if cycle == 0:
                    baselines[(host, role)] = digest
                require(digest == baselines[(host, role)])
                record = {
                    "endpoint": host,
                    "role": role,
                    "cycle": cycle,
                    "identity": identity,
                    "catalog_sha256": digest,
                }
                if cycle < 2:
                    record["tables"] = tables
                records.append(record)
        first = await connect("pool", "wex_incumbent", deadline)
        connections.append(first)
        second = await connect("pool", "wex_core", deadline)
        connections.append(second)
        for cycle in range(2):
            transactions = [first.transaction(), second.transaction()]
            for tx in transactions:
                async with asyncio.timeout(remaining(deadline)):
                    await tx.start()
            async with asyncio.TaskGroup() as group:
                first_sample = group.create_task(
                    snapshot(first, "wex_incumbent", deadline)
                )
                second_sample = group.create_task(
                    snapshot(second, "wex_core", deadline)
                )
            samples = (first_sample.result(), second_sample.result())
            for tx in transactions:
                operation = tx.commit if cycle == 0 else tx.rollback
                async with asyncio.timeout(remaining(deadline)):
                    await operation()
            for role, (_, identity, digest) in zip(ROLES, samples, strict=True):
                require(digest == baselines[("pool", role)])
                records.append(
                    {
                        "endpoint": "pool",
                        "role": role,
                        "concurrent_cycle": cycle,
                        "identity": identity,
                        "catalog_sha256": digest,
                    }
                )
        denials = []
        for connection, (host, role) in zip(connections[:4], PAIRS, strict=True):
            denials.append(
                {
                    "endpoint": host,
                    "role": role,
                    "checks": await escape_negatives(connection, role, deadline),
                }
            )
        failures = []
        for host in ("postgres", "pool"):
            failures.append(
                await rejected_connection(
                    host + "_wrong_password",
                    host,
                    "wex_incumbent",
                    "synthetic_wrong_password",
                    DATABASE,
                    deadline,
                )
            )
            failures.append(
                await rejected_connection(
                    host + "_unknown_user",
                    host,
                    "wex_unknown",
                    "synthetic_unknown_password",
                    DATABASE,
                    deadline,
                )
            )
        failures.append(
            await rejected_connection(
                "pool_admin_user",
                "pool",
                "bifrost",
                "synthetic_primer_admin",
                DATABASE,
                deadline,
            )
        )
        failures.append(
            await rejected_connection(
                "pool_foreign_alias",
                "pool",
                "wex_incumbent",
                ROLES["wex_incumbent"],
                "foreign_primer_alias",
                deadline,
            )
        )
        return {
            "schema": "bifrost.test.writer-primer/v1",
            "samples": records,
            "escapes": denials,
            "connections": failures,
        }
    finally:
        # Preserve original control/failure; attempt disposal of EVERY socket.
        original = sys.exception()
        disposal = None
        for connection in connections:
            try:
                await close(connection, deadline)
            except BaseException as error:
                if disposal is None or not isinstance(error, Exception):
                    disposal = error
        if original is None and disposal is not None:
            if not isinstance(disposal, Exception):
                raise disposal
            raise PrimerFailure("primer disposal failed") from None


def write_receipt(value, deadline):
    # Private evidence is owned by the same UID as the hosted conductor.
    destination = Path("/results/probe.json")
    remaining(deadline)
    fd = os.open(
        destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        remaining(deadline)
        used = 0
        for part in json.JSONEncoder(
            ensure_ascii=True, separators=(",", ":")
        ).iterencode(value):
            chunk = part.encode()
            used += len(chunk)
            require(used <= OUTPUT_LIMIT)
            view = memoryview(chunk)
            while view:
                remaining(deadline)
                count = os.write(fd, view)
                remaining(deadline)
                require(count > 0)
                view = view[count:]
        remaining(deadline)
        os.fsync(fd)
        remaining(deadline)
    finally:
        original = sys.exception()
        try:
            os.close(fd)
        except BaseException:
            if original is None:
                raise


async def main():
    deadline = time.monotonic() + 120
    async with asyncio.timeout(remaining(deadline)):
        controls = classifier_controls()
        result = await collect(deadline)
        result["classifier_controls"] = controls
        write_receipt(result, deadline)
        require(time.monotonic() < deadline)


def write_failure(error):
    # Inspect only closed source-frame labels, never messages or driver payloads.
    pending = [error]
    failures = []
    while pending and len(failures) < 8:
        current = pending.pop(0)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:8])
            continue
        names = {
            "PrimerFailure",
            "TimeoutError",
            "ConnectionRefusedError",
            "ConnectionResetError",
            "OSError",
            "PermissionError",
            "InvalidPasswordError",
            "InvalidAuthorizationSpecificationError",
            "InvalidCatalogNameError",
            "InsufficientPrivilegeError",
            "UndefinedTableError",
            "UndefinedFunctionError",
            "PostgresError",
        }
        observed = type(current).__name__
        fact = {"exception_type": observed if observed in names else "unclassified"}
        state = getattr(current, "sqlstate", None)
        if state in {
            "28P01",
            "28000",
            "3D000",
            "42501",
            "42P01",
            "42883",
            "08006",
            "08001",
            "57014",
        }:
            fact["sqlstate"] = state
        trace = current.__traceback__
        caller_line = None
        while trace is not None:
            frame = trace.tb_frame
            if frame.f_code.co_filename == __file__:
                local = frame.f_locals
                if frame.f_code.co_name == "require":
                    fact["assertion_code"] = f"require_call_line_{caller_line}"
                if frame.f_code.co_name == "snapshot" and local.get("name") in QUERIES:
                    fact["query"] = local["name"]
                    fact["task"] = "snapshot"
                if local.get("host") in {"postgres", "pool"}:
                    fact["endpoint"] = local["host"]
                if local.get("role") in ROLES:
                    fact["role"] = local["role"]
                if type(local.get("cycle")) is int and 0 <= local["cycle"] < 10:
                    fact["sample_cycle"] = local["cycle"]
            caller_line = trace.tb_lineno
            trace = trace.tb_next
        owned = getattr(current, "primer_negative_observation", None)
        if type(owned) is dict and owned.get("case_id") in NEGATIVE_CASES:
            fact.pop("sample_cycle", None)
            fact.pop("query", None)
            fact["task"] = "rejected_connection"
            fact.update(owned)
        failures.append(fact)
    data = json.dumps(
        {"schema": "bifrost.test.writer-primer-failure/v1", "failures": failures},
        sort_keys=True,
    ).encode()
    if len(data) > 4096:
        return
    fd = os.open(
        "/results/probe-failure.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                return
            view = view[count:]
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        try:
            write_failure(error)
        except Exception:
            pass  # Diagnostic failure never converts the failed probe to success.
        raise SystemExit("writer primer failed; sanitized evidence only") from None
