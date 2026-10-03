"""Selected Result SQL differential; actual consumer remains the reference.

This integer-JSON fixture profile does not own arbitrary sanitizer inputs,
worker recovery, Rust events, transaction faults or runtime authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
import re
import signal
import stat
import struct
import sys
import time
import tomllib
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import delete, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.sql import visitors
from src.config import get_settings
from src.core import database, redis_client
from src.core.cache.keys import active_execution_key, execution_logs_stream_key, pending_changes_key
from src.core.execution_variable_safety import sanitize_execution_variables
from src.jobs.consumers import workflow_execution as consumer_module
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.repositories.executions import _make_json_safe
from src.services.execution import process_pool as pool_module

from tests.parity.workflow_domain_harness import (
    MISSING_FENCE,
    REFERENCE_HASHES,
    SEED_TIME,
    WorkflowCohort,
    dormant,
    unstarted_consumer,
)

FIXTURE = Path(__file__).parent / "fixtures/workflow-result-v1.json"
API_ROOT = Path(__file__).resolve().parents[2]
ROOT = API_ROOT.parent
EVIDENCE = Path("/tmp/bifrost/workflow-result-parity")
DRIVER = EVIDENCE / "driver"
RECEIPT = EVIDENCE / "receipt.json"
SUCCESS = ("status", "result", "error", "error_type", "duration_ms", "variables", "execution_context", "metrics", "roi")
FAILURE = ("error", "error_type", "duration_ms", "execution_context", "metrics")
METRICS = ("peak_memory_bytes", "process_rss_bytes", "cpu_user_seconds", "cpu_system_seconds", "cpu_total_seconds")
ROI = ("time_saved", "value")
PREPARATION = (
    "api/src/core/execution_variable_safety.py",
    "api/src/repositories/executions.py",
    "api/tests/parity/workflow_sql_harness.py",
)
ALLOW_CODES = {"23514", "23505", "23503", "22003", "57014", "55P03"}
REASONS = {
    "MissingExecution",
    "MissingAttempt",
    "InvalidAttemptFence",
    "InvalidAttemptState",
    "InvalidLogicalState",
    "MissingFence",
    "LegacyUnfencedOutsideTrackedPath",
    "RequiresCoordinatorPolicy",
    "InconsistentRows",
}
SQL_STAGES = {
    "Advisory",
    "ReadHistory",
    "ReadExecution",
    "ReadAttempt",
    "ReadContext",
    "Decode",
    "Clock",
    "WriteAttempt",
    "WriteExecution",
}
SETTLEMENT = {"Setup", "Acquire", "Begin", "Commit", "Rollback", "Close"}
CLASSES = {"Database", "InvalidRow", "ClockRange", "Cardinality", "Resource"}
EXEC_FIELDS = {
    "status",
    "duration_ms",
    "completed_at",
    "result",
    "result_type",
    "error_message",
    "time_saved",
    "value",
    "variables",
    "execution_context",
    "metrics",
    "logs",
}
ATTEMPT_FIELDS = {
    "status",
    "phase",
    "failure_phase",
    "failure_code",
    "started_at",
    "heartbeat_at",
    "completed_at",
    "duration_ms",
    "peak_memory_bytes",
    "cpu_total_seconds",
}
_HELD_CUSTODY = False
_CLOCK_ACTOR = ContextVar("result_clock_actor", default=None)
_CLOCK_REPOSITORY = ContextVar("result_clock_repository", default=None)
_CLOCK_LIMIT = 9007199254740991


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def closed(value: Any, fields: set[str]) -> None:
    check(type(value) is dict and set(value) == fields, "closed Result schema")


def decode(raw: bytes) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            check(key not in value, "duplicate Result key")
            value[key] = item
        return value

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise AssertionError("invalid Result JSON") from None


def read_file(path: Path, limit: int, *, executable: bool = False) -> bytes:
    fd = -1
    original = None
    data = bytearray()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        check(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, "Result file admission"
        )
        if executable:
            check(before.st_mode & 0o111 != 0, "Result executable admission")
        while block := os.read(fd, min(65536, limit + 1 - len(data))):
            data.extend(block)
            check(len(data) <= limit, "Result file bound")
        after = os.fstat(fd)
        check(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode),
            "Result file changed",
        )
    except BaseException as error:
        original = error
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    return bytes(data)


def load_fixture() -> dict:
    value = decode(read_file(FIXTURE, 1024 * 1024))
    closed(value, {"schema", "synthetic", "cases", "held_protocols", "controls"})
    check(
        value["schema"] == "bifrost.test.workflow-result-fixtures/v1" and value["synthetic"] is True,
        "Result fixture identity",
    )
    check(len(value["cases"]) == 233 and len(value["held_protocols"]) == 8, "Result protocol count")
    ids = []
    for case in value["cases"]:
        closed(case, {"case_id", "seed", "lane", "raw_fields_json"})
        check(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", case["case_id"]) is not None, "Result case label")
        check(case["lane"] in {"success", "failure"}, "Result lane")
        closed(case["seed"], {"status", "attempt", "phase", "token", "context", "tracking"})
        check(
            type(case["raw_fields_json"]) is str and len(case["raw_fields_json"].encode()) <= 65536,
            "Result raw fixture bound",
        )
        ids.append(case["case_id"])
    ids += [item["case_id"] for item in value["held_protocols"]]
    check(len(ids) == 241 and len(set(ids)) == 241, "Result complete identity roster")
    check([len(v) for v in value["controls"].values()] == [27, 9, 9, 20, 8], "Result control roster")
    return value


def materialize(fields: dict, lane: str) -> dict:
    closed(fields, set(SUCCESS if lane == "success" else FAILURE))

    def tag(value, members=None):
        check(type(value) is dict and value.get("kind") in {"absent", "null", "value"}, "Result presence")
        closed(value, {"kind", "value"} if value["kind"] == "value" else {"kind"})
        if value["kind"] == "absent":
            return False, None
        if value["kind"] == "null":
            return True, None
        check(value["value"] is not None, "Result value-null")
        if members is None:
            return True, deepcopy(value["value"])
        closed(value["value"], set(members))
        result = {}
        for name in members:
            supplied, item = tag(value["value"][name])
            if supplied:
                result[name] = item
        return True, result

    result = {}
    for name, value in fields.items():
        supplied, item = tag(value, METRICS if name == "metrics" else ROI if name == "roi" else None)
        if supplied:
            result[name] = item
    return result


def prepare_json_inputs(raw_fields: dict, lane: str) -> dict:
    """Only incumbent pure preparation; no row, numeric rounding or outcome."""
    original = materialize(raw_fields, lane)
    result = {}
    for source, target in (
        ("result", "prepared_result"),
        ("variables", "prepared_variables"),
        ("execution_context", "prepared_context"),
    ):
        if lane == "failure" and source != "execution_context":
            result[target] = {"kind": "absent"}
            continue
        tag = raw_fields[source]
        if tag["kind"] != "value":
            result[target] = {"kind": tag["kind"]}
        else:
            value = original[source]
            if source == "variables":
                value = sanitize_execution_variables(value)
            result[target] = {"kind": "value", "value": _make_json_safe(value)}
    lines = [
        path + " " + hashlib.sha256(read_file(ROOT / path, 2 * 1024 * 1024)).hexdigest() + "\n" for path in PREPARATION
    ]
    result["preparation_source_sha256"] = hashlib.sha256("".join(lines).encode("ascii")).hexdigest()
    return result


def source_lock_packages(raw: bytes) -> set[str]:
    try:
        lock = tomllib.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise AssertionError("Result source lock syntax") from None
    check(type(lock.get("version")) is int and lock["version"] == 4, "Result source lock version")
    packages = lock.get("package")
    check(type(packages) is list and 1 <= len(packages) <= 232, "Result source lock packages")
    identities = []
    for package in packages:
        check(type(package) is dict, "Result source lock package")
        name, version = package.get("name"), package.get("version")
        check(
            type(name) is str
            and re.fullmatch(r"[A-Za-z0-9_-]+", name) is not None
            and type(version) is str
            and re.fullmatch(r"[A-Za-z0-9.+_-]+", version) is not None,
            "Result source lock identity",
        )
        identity = f"{name}@{version}"
        check(len(identity) <= 256, "Result source lock identity bound")
        identities.append(identity)
    check(len(set(identities)) == len(identities), "Result source lock duplicate")
    return set(identities)


def source_graph_summary(graph, admitted_packages: set[str]):
    """Workspace crosscheck only; parent owns projection and manifest-domain admission."""
    closed(graph, {"sha256", "packages", "features"})
    check(
        type(graph["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", graph["sha256"]) is not None,
        "Result graph projection digest",
    )
    packages, features = graph["packages"], graph["features"]
    check(
        type(packages) is list
        and 1 <= len(packages) <= 232
        and all(type(package) is str and package.isascii() and len(package) <= 256 for package in packages),
        "Result graph packages",
    )
    check(packages == sorted(set(packages)) and set(packages) <= admitted_packages, "Result graph package membership")
    check(
        type(features) is list
        and all(type(feature) is str and feature.isascii() and len(feature) <= 512 for feature in features),
        "Result graph features",
    )
    check(features == sorted(set(features)), "Result graph feature order")
    for feature in features:
        check(feature.count("/") == 1, "Result graph feature shape")
        package, name = feature.split("/")
        check(
            package in packages and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) is not None,
            "Result graph feature membership",
        )
        check(
            package != "serde_json@1.0.151" or name not in {"float_roundtrip", "arbitrary_precision"},
            "Result graph forbidden feature",
        )


def source_admission(observer=None) -> dict:
    """Read-only external receipt contract; producer is a separately held gate."""
    value = decode(read_file(RECEIPT, 1024 * 1024))
    closed(value, {"schema", "candidate", "sources", "binary", "graphs"})
    check(value["schema"] == "bifrost.test.workflow-result-source/v2", "Result source receipt")
    closed(value["candidate"], {"head", "tree"})
    check(
        all(re.fullmatch(r"[0-9a-f]{40}", value["candidate"][k]) for k in ("head", "tree")), "Result candidate metadata"
    )
    check(
        type(value["sources"]) is dict
        and all(type(k) is str and re.fullmatch(r"[0-9a-f]{64}", v) for k, v in value["sources"].items()),
        "Result source map",
    )
    required = (
        set(REFERENCE_HASHES)
        | set(PREPARATION)
        | {
            "api/src/core/database.py",
            "api/src/config.py",
            "api/tests/parity/test_workflow_sql.py",
            "api/tests/parity/fixtures/workflow-result-v1.json",
            "core-rs/crates/bifrost-db/src/workflow_parity.rs",
            "core-rs/crates/bifrost-db/src/workflow_numeric.rs",
            "core-rs/crates/bifrost-db/examples/workflow_sql_vectors.rs",
            "core-rs/crates/bifrost-db/Cargo.toml",
            "core-rs/Cargo.lock",
        }
    )
    check(required <= set(value["sources"]), "Result source receipt incomplete")
    lock_bytes = None
    for path in required:
        source_bytes = read_file(ROOT / path, 4 * 1024 * 1024)
        check(
            hashlib.sha256(source_bytes).hexdigest() == value["sources"][path],
            "Result source readback",
        )
        if path == "core-rs/Cargo.lock":
            lock_bytes = source_bytes
    check(type(lock_bytes) is bytes, "Result source lock readback")
    admitted_packages = source_lock_packages(lock_bytes)
    for path, digest in REFERENCE_HASHES.items():
        check(value["sources"][path] == digest, "Result reference drift")
    closed(value["binary"], {"sha256", "source_paths", "build_head", "build_tree"})
    check(
        value["binary"]["build_head"] == value["candidate"]["head"]
        and value["binary"]["build_tree"] == value["candidate"]["tree"],
        "Result binary source binding",
    )
    check(
        set(value["binary"]["source_paths"]) <= set(value["sources"])
        and required - {x for x in required if x.startswith("api/")} <= set(value["binary"]["source_paths"]),
        "Result binary source roster",
    )
    check(
        hashlib.sha256(read_file(DRIVER, 64 * 1024 * 1024, executable=True)).hexdigest() == value["binary"]["sha256"],
        "Result binary readback",
    )
    check(
        type(value["graphs"]) is dict and set(value["graphs"]) == {"default", "selected", "all_features"},
        "Result feature evidence absent",
    )
    for graph in value["graphs"].values():
        source_graph_summary(graph, admitted_packages)
    if observer is not None:
        observer.session.loaded_source_admission(value)
    return value


def driver_dsn(engine) -> str:
    """Fail closed where constructor/TLS equivalence cannot be reconstructed."""
    original = make_url(get_settings().database_url)
    cleaned, options = database._prepare_asyncpg_url(str(original.render_as_string(hide_password=False)))
    check(make_url(cleaned) == engine.url == database.get_engine().url, "Result constructor endpoint mismatch")
    factory = database.get_session_factory()
    check(
        factory.kw["bind"] is database.get_engine()
        and factory.kw.get("autoflush") is False
        and factory.kw.get("expire_on_commit") is False,
        "Result production session settings",
    )
    # Authenticated SSLContext cannot be reproduced from a cleaned URL alone.
    check(
        not options and set(original.query) <= {"sslmode"} and original.query.get("sslmode") in {None, "disable"},
        "Result TLS association unsupported",
    )
    return original.set(drivername="postgresql").render_as_string(hide_password=False)


# Private source observers are test provenance, not runtime or security authority.
_OBSERVER_ENV = (
    "PGHOST",
    "PGPORT",
    "PGUSER",
    "PGPASSWORD",
    "PGDATABASE",
    "PGPASSFILE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGSSLMODE",
    "PGSSLNEGOTIATION",
    "PGSSLROOTCERT",
    "PGSSLCRL",
    "PGSSLKEY",
    "PGSSLCERT",
    "SSLKEYLOGFILE",
    "PGSSLMINPROTOCOLVERSION",
    "PGSSLMAXPROTOCOLVERSION",
    "PGTARGETSESSIONATTRS",
    "PGKRBSRVNAME",
    "PGGSSLIB",
)
_OBSERVER_MODULES = {
    "src.jobs.consumers.workflow_execution": "api/src/jobs/consumers/workflow_execution.py",
    "src.repositories.executions": "api/src/repositories/executions.py",
    "src.core.execution_variable_safety": "api/src/core/execution_variable_safety.py",
    "src.core.database": "api/src/core/database.py",
    "src.config": "api/src/config.py",
    "src.models.orm.executions": "api/src/models/orm/executions.py",
    "src.models.enums": "api/src/models/enums.py",
    "tests.parity.workflow_domain_harness": "api/tests/parity/workflow_domain_harness.py",
    "tests.parity.workflow_sql_harness": "api/tests/parity/workflow_sql_harness.py",
    "src.services.execution.attempts": "api/src/services/execution/attempts.py",
}


def observer_context(value):
    closed(value, {"schema", "candidate", "invocation_uuid", "parent_uid", "target_remaining_seconds", "private_input"})
    check(value["schema"] == "bifrost.private.result-observer-context/v2", "Result observer context schema")
    observer_candidate(value["candidate"])
    check(
        type(value["invocation_uuid"]) is str and str(UUID(value["invocation_uuid"])) == value["invocation_uuid"],
        "Result observer invocation",
    )
    check(type(value["parent_uid"]) is int and 0 <= value["parent_uid"] < 2**31, "Result observer parent UID")
    budget = value["target_remaining_seconds"]
    check(type(budget) in {int, float} and 0 < budget <= 900 and math.isfinite(budget), "Result observer launch bound")
    observer_private_descriptor(value["private_input"], value["parent_uid"])
    return value


def observer_private_descriptor(value, parent_uid):
    closed(value, {"schema", "path", "dev", "ino", "owner_uid", "gid", "mode", "size", "nlink", "user_namespace"})
    check(
        value["schema"] == "bifrost.private.result-dsn-input/v1"
        and value["path"] == "/bifrost-private/result-all-features.env",
        "Result private input profile",
    )
    for name, maximum in (("dev", 2**64 - 1), ("ino", 2**64 - 1), ("owner_uid", 2**31 - 1), ("gid", 2**31 - 1)):
        check(type(value[name]) is int and 0 <= value[name] <= maximum, "Result private input integer")
    check(value["ino"] > 0 and value["owner_uid"] == parent_uid, "Result private input owner")
    check(type(value["mode"]) is int and value["mode"] == 0o640, "Result private input mode")
    check(type(value["size"]) is int and 1 <= value["size"] <= 4096, "Result private input size")
    check(type(value["nlink"]) is int and value["nlink"] == 1, "Result private input links")
    namespace = value["user_namespace"]
    closed(namespace, {"dev", "ino"})
    check(
        type(namespace["dev"]) is int
        and 0 <= namespace["dev"] < 2**64
        and type(namespace["ino"]) is int
        and 0 < namespace["ino"] < 2**64,
        "Result private input namespace",
    )
    return value


def observer_private_file_fact(info, descriptor):
    check(
        stat.S_ISREG(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o640
        and (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_nlink, info.st_size)
        == tuple(descriptor[name] for name in ("dev", "ino", "owner_uid", "gid", "nlink", "size")),
        "Result private input inode",
    )
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
    )


def observer_private_target(uid, gid, namespace, descriptor):
    check(
        type(uid) is int and uid == 1000 and type(gid) is int and gid == descriptor["gid"],
        "Result private input target identity",
    )
    closed(namespace, {"dev", "ino"})
    check(
        type(namespace["dev"]) is int and type(namespace["ino"]) is int and namespace == descriptor["user_namespace"],
        "Result private input target namespace",
    )


def observer_private_bytes(raw):
    check(type(raw) is bytes and 1 <= len(raw) <= 4096, "Result private input bytes")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise AssertionError("Result private input UTF-8") from None
    prefix = "BIFROST_RUST_TEST_DATABASE_URL="
    check(
        value.startswith(prefix)
        and value.endswith("\n")
        and value.count("\n") == 1
        and not any(character in value for character in ("\ufeff", "\x00", "\r"))
        and len(value) > len(prefix) + 1,
        "Result private input grammar",
    )
    return value[len(prefix) : -1]


def observer_private_url(expected_native, original_url, actual_native=None):
    check(type(expected_native) is str, "Result private URL type")
    try:
        native = make_url(expected_native)
        check(
            native.set(drivername=original_url.drivername) == original_url
            and original_url.set(drivername="postgresql").render_as_string(hide_password=False) == expected_native,
            "Result private original URL association",
        )
    except (ArgumentError, ValueError, UnicodeError):
        raise AssertionError("Result private URL profile") from None
    if actual_native is not None:
        check(type(actual_native) is str and actual_native == expected_native, "Result private native URL association")


def observer_private_owner(owner, active):
    check(owner is active and owner.phase == "entered", "Result private live owner")


def observer_dsn_return(owner, active, expected_native, original_url, actual_native):
    observer_private_owner(owner, active)
    check(owner.dsn_return is None and not owner.dsn_used, "Result duplicate DSN return")
    check(type(actual_native) is str, "Result actual DSN return type")
    observer_private_url(expected_native, original_url, actual_native)
    owner.dsn_return = actual_native


def observer_dsn_use(owner, active, expected_native, actual_native):
    observer_private_owner(owner, active)
    check(
        type(actual_native) is str
        and owner.dsn_return is not None
        and actual_native == owner.dsn_return == expected_native
        and not owner.dsn_used,
        "Result actual DSN use association",
    )
    owner.dsn_used = True


def observer_dsn_binding(value):
    closed(value, {"fixture_match", "production_match", "driver_match", "complete"})
    for name in ("fixture_match", "production_match", "driver_match"):
        check(value[name] is None or type(value[name]) is bool, "Result DSN nullable observation")
    check(type(value["complete"]) is bool, "Result DSN completion type")
    check(
        not value["complete"]
        or all(value[name] is True for name in ("fixture_match", "production_match", "driver_match")),
        "Result DSN completion observations",
    )


def observer_candidate(value):
    closed(value, {"head", "tree"})
    check(
        all(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{40}", value[key]) for key in ("head", "tree")),
        "Result observer candidate",
    )


def observer_original(actual, original, origin, expected_origin, actual_digest, mirror_digest, expected_digest):
    check(actual is original and origin == expected_origin, "Result loaded original identity")
    check(actual_digest == mirror_digest == expected_digest, "Result loaded source association")


def observer_fixture(args, kwargs, original_url, null_pool, engine):
    check(type(args) is tuple and args == (original_url,), "Result fixture constructor arguments")
    check(type(kwargs) is dict and set(kwargs) == {"echo", "poolclass"}, "Result fixture constructor keys")
    check(kwargs["echo"] is False and kwargs["poolclass"] is null_pool, "Result fixture constructor options")
    check(engine is not None and engine.url == make_url(original_url), "Result fixture returned engine")


def observer_phase(phase, engine, actual_engine, factory, actual_factory, cleared_engine=None, cleared_factory=None):
    check(phase == "awaiting_source_close", "Result source close phase")
    check(engine is actual_engine and factory is actual_factory, "Result source close identity")
    check(cleared_engine is None and cleared_factory is None, "Result source close incomplete")


def observer_preconnect(cargs, cparams, url):
    check(type(cargs) is list and not cargs, "Result PRECONNECT positional arguments")
    check(
        type(cparams) is dict and set(cparams) == {"host", "port", "user", "password", "database"},
        "Result PRECONNECT parameter keys",
    )
    expected = {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }
    check(type(cparams["port"]) is int and 1 <= cparams["port"] <= 65535, "Result PRECONNECT port")
    check(
        all(type(cparams[key]) is str and cparams[key] for key in ("host", "user", "password", "database")),
        "Result PRECONNECT string types",
    )
    check(cparams == expected, "Result PRECONNECT original endpoint")


def observer_pair(pairs, record, dbapi, *, require=False):
    matches = [pair for pair in pairs if pair[0] is record and pair[1] is dbapi]
    check(len(matches) <= 1 and len(pairs) <= 16, "Result physical pair bound")
    if require:
        check(len(matches) == 1, "Result physical association absent")
    elif not matches:
        check(len(pairs) < 16, "Result physical association bound")
        pairs.append((record, dbapi, None, None))


def observer_actor(pairs, dbapi, connection, actor):
    matches = [index for index, pair in enumerate(pairs) if pair[1] is dbapi]
    check(len(matches) == 1, "Result actual actor physical connection")
    index = matches[0]
    record, physical, _previous_connection, _previous_actor = pairs[index]
    pairs[index] = (record, physical, connection, actor)


def observer_decisions(have_admit, have_failed):
    check(
        type(have_admit) is bool and type(have_failed) is bool and not (have_admit and have_failed),
        "Result duplicate endpoint decisions",
    )


def observer_decision(value, context, *, rejected=False):
    keys = {"schema", "invocation_uuid", "candidate", "phase", "decision"}
    closed(value, keys | ({"reason"} if rejected else set()))
    check(
        value["schema"] == "bifrost.private.result-endpoint-decision/v1"
        and value["invocation_uuid"] == context["invocation_uuid"]
        and value["candidate"] == context["candidate"]
        and value["phase"] == "frontend_observed_before_sql",
        "Result endpoint decision association",
    )
    check(value["decision"] == ("reject" if rejected else "admit"), "Result endpoint decision kind")
    if rejected:
        check(
            value["reason"] in {"frontend_unverified", "source_association", "deadline", "cleanup"},
            "Result endpoint reason",
        )


def observer_request(value, context, source_hash, url):
    closed(
        value,
        {
            "schema",
            "invocation_uuid",
            "candidate",
            "phase",
            "fixture_source_sha256",
            "provider",
            "construction_index",
            "hostname",
            "port",
            "drivername",
        },
    )
    check(
        value["schema"] == "bifrost.private.result-endpoint/v1"
        and value["invocation_uuid"] == context["invocation_uuid"]
        and value["candidate"] == context["candidate"]
        and value["phase"] == "fixture_constructed_before_sql",
        "Result endpoint request association",
    )
    check(
        value["fixture_source_sha256"] == source_hash
        and value["provider"] == "conftest_NullPool"
        and type(value["construction_index"]) is int
        and value["construction_index"] == 1,
        "Result endpoint fixture provenance",
    )
    check(
        type(value["hostname"]) is str
        and value["hostname"].isascii()
        and 1 <= len(value["hostname"]) <= 253
        and value["hostname"] == url.host
        and type(value["port"]) is int
        and 1 <= value["port"] <= 65535
        and value["port"] == url.port
        and value["drivername"] == url.drivername == "postgresql+asyncpg",
        "Result endpoint request fields",
    )


def observer_publication(info, temporary_info, *, uid, limit):
    observer_file_fact(info, uid=uid, limit=limit, linked=True)
    observer_file_fact(temporary_info, uid=uid, limit=limit, linked=True)
    check(
        (info.st_dev, info.st_ino) == (temporary_info.st_dev, temporary_info.st_ino),
        "Result publication temporary association",
    )


def observer_file_fact(info, *, uid, limit, linked=False):
    check(
        stat.S_ISREG(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o644
        and info.st_uid == uid
        and info.st_nlink == (2 if linked else 1)
        and 0 < info.st_size <= limit,
        "Result shared metadata inode",
    )
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_uid, info.st_nlink


def observer_settle(actions, original=None):
    """Bounded owned actions share one independent first-object settlement rule."""
    first = original
    failed = False
    for action in actions:
        try:
            action()
        except BaseException as error:
            failed = True
            if first is None:
                first = error
    return first, failed


def observer_independent(actions, original=None):
    first, _failed = observer_settle(actions, original)
    if first is not None:
        raise first


_OBSERVER_TESTS = {
    "test_queued_cancel_emitted_update_order",
    "test_result_nonfault_paired",
    "test_result_nonfault_control",
}
_OBSERVER_FINAL_KEYS = {
    "schema",
    "candidate",
    "invocation_uuid",
    "phase",
    "fixture_constructions",
    "functions_entered",
    "functions_completed",
    "production_lifetimes",
    "production_closed",
    "loaded_bindings_verified",
    "fixture_gate_removed",
    "session_wrappers_restored",
    "metadata_absent",
    "poisoned",
    "cleanup_failed",
    "complete",
    "export_item",
    "dsn_binding",
}
_OBSERVER_CHECKS = ("loaded_bindings_verified", "fixture_gate_removed", "session_wrappers_restored", "metadata_absent")


def observer_junit_identity(nodeid):
    check(type(nodeid) is str and nodeid.isascii() and 1 <= len(nodeid) <= 256, "Result export nodeid bound")
    path, bracket, parameters = nodeid.partition("[")
    names = path.split("::")
    check(
        len(names) == 2 and names[0] == "tests/parity/test_workflow_sql.py" and names[1] in _OBSERVER_TESTS,
        "Result export selected item",
    )
    check(not bracket or parameters.endswith("]"), "Result export parameter suffix")
    # Exact pytest9.0.3 mangle_test_address forward projection; no inverse normalization.
    return names[0].replace("/", ".")[:-3], names[1] + bracket + parameters


def observer_item(item, session, entered, *, require_last=False):
    check(
        item.session is session and type(session.items) is list and len(session.items) == 299,
        "Result export public session",
    )
    check(sum(existing is item for existing in session.items) == 1, "Result export Item membership")
    identities = [observer_junit_identity(existing.nodeid) for existing in session.items]
    check(len(set(identities)) == 299, "Result export selected collection duplicate")
    observer_junit_identity(item.nodeid)
    if require_last:
        check(
            entered and entered[-1].test_item_identity is item and session.items[-1] is item,
            "Result export last actual Item",
        )
    else:
        check(
            len(entered) < 299 and all(owner.test_item_identity is not item for owner in entered),
            "Result entered Item duplicate",
        )


def observer_final_record(value, context, nodeid, *, require_complete=False):
    closed(value, _OBSERVER_FINAL_KEYS)
    check(
        value["schema"] == "bifrost.private.result-source-observer-final/v2"
        and value["candidate"] == context["candidate"]
        and value["invocation_uuid"] == context["invocation_uuid"]
        and value["phase"] == "observer_session_finalizer_after_owned_cleanup"
        and value["export_item"] == nodeid,
        "Result final export association",
    )
    observer_junit_identity(value["export_item"])
    for name in (
        "fixture_constructions",
        "functions_entered",
        "functions_completed",
        "production_lifetimes",
        "production_closed",
    ):
        check(
            type(value[name]) is int and 0 <= value[name] <= (1 if name == "fixture_constructions" else 299),
            "Result final actual count",
        )
    check(
        value["functions_completed"] <= value["functions_entered"]
        and value["production_closed"] <= value["production_lifetimes"] <= value["functions_entered"],
        "Result final count relation",
    )
    for name in _OBSERVER_CHECKS:
        check(value[name] is None or type(value[name]) is bool, "Result final nullable observation")
    for name in ("poisoned", "cleanup_failed", "complete"):
        check(type(value[name]) is bool, "Result final outcome type")
    observer_dsn_binding(value["dsn_binding"])
    complete = (
        value["fixture_constructions"] == 1
        and value["functions_entered"] > 0
        and value["functions_completed"] == value["functions_entered"]
        and value["production_closed"] == value["production_lifetimes"]
        and all(value[name] is True for name in _OBSERVER_CHECKS)
        and not value["poisoned"]
        and not value["cleanup_failed"]
        and value["dsn_binding"]["complete"]
    )
    check(value["complete"] is complete, "Result final completion predicate")
    if require_complete:
        check(complete and value["functions_entered"] == 299, "Result final complete admission")
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    check(raw.isascii() and len(raw.encode("utf-8")) <= 2048, "Result final export bytes")
    return raw


def observer_append_final(item, raw):
    check(type(item.user_properties) is list, "Result export property list")
    check(all(type(pair) is tuple and len(pair) == 2 for pair in item.user_properties), "Result export property shape")
    check(
        not any(name == "result_source_observer_final" for name, _value in item.user_properties),
        "Result final property duplicate",
    )
    item.user_properties.append(("result_source_observer_final", raw))


class ResultSourceFunction:
    def __init__(self, session, item):
        self.session = session
        self.test_item_identity = item
        self.phase = "entered"
        self.constructor = None
        self.connections = []
        self.own_listeners = []
        self.closed = False
        self.primary = None
        self.dsn_return = None
        self.dsn_used = False

    def listen(self, target, name, callback):
        check(len(self.own_listeners) < 16, "Result own listener bound")
        # Register ownership before the fallible registration; removal checks actual membership.
        self.own_listeners.append((target, name, callback))
        event.listen(target, name, callback)

    def admit_fixture_engine(self, engine):
        check(self.session.active is self and self.phase == "entered", "Result function owner")
        check(
            self.session.fixture["engine"] is engine and self.session.fixture["endpoint_admitted"],
            "Result fixture gate",
        )

    def admit_production_engine(self, engine):
        check(self.constructor is not None and self.constructor["engine"] is engine, "Result production engine owner")
        check(database._engine is engine, "Result production global engine association")
        factory = database._async_session_factory
        if factory is not None:
            check(
                factory.kw["bind"] is engine
                and factory.kw.get("autoflush") is False
                and factory.kw.get("expire_on_commit") is False,
                "Result actual source factory association",
            )
            previous = self.constructor["factory"]
            check(previous is None or previous is factory, "Result source factory replacement")
            self.constructor["factory"] = factory

    def finish(self, original=None):
        self.primary = original
        self.phase = "awaiting_source_close"
        actions = []
        if database._engine is not None:
            actions.append(lambda: self.admit_production_engine(database._engine))
        actions.extend(lambda hook=hook: self.session.remove_hook(hook) for hook in self.own_listeners)
        try:
            observer_independent(actions, original)
        except BaseException as error:
            self.primary = error
            self.session.poison_with(error)
            raise


class ResultSourceObserver:
    def __init__(self, context, pytest_session):
        self.pytest_session = pytest_session
        self.entered = []
        self.context = observer_context(context)
        self.originals = []
        self.private_fd = None
        self.private_fact = None
        self.private_raw = None
        self.private_value = None
        self.private_closed = False
        self.private_verified = False
        self.dsn_matches = dict.fromkeys(("fixture_match", "production_match", "driver_match"))
        self.fixture = {
            "original_alias": None,
            "source_module": None,
            "source_function": None,
            "original_url": None,
            "engine": None,
            "construction_count": 0,
            "endpoint_published": False,
            "endpoint_admitted": False,
        }
        self.active = None
        self.completed = []
        self.poison = None
        self.owned_files = []
        self.created_at = time.monotonic()
        self.modules = {}
        self.bindings = []
        self.session_hooks = []
        self.environment = {key: key in os.environ for key in _OBSERVER_ENV}
        check(not any(self.environment.values()), "Result inherited connection environment unsupported")
        try:
            self.receipt = source_admission()
            check(self.receipt["candidate"] == self.context["candidate"], "Result observer receipt candidate")
            self.capture_loaded_sources()
            check(
                isinstance(pytest_session, sys.modules["pytest"].Session)
                and sys.modules["pytest"].__version__ == "9.0.3",
                "Result original pytest session",
            )
            check(
                pytest_session.config.getoption("junitprefix", None) in {None, ""}
                and pytest_session.config.getoption("numprocesses", None) in {None, 0}
                and pytest_session.config.getoption("reruns", None) in {None, 0},
                "Result unsupported pytest execution profile",
            )
            fixture = load_fixture()
            prefix = "tests/parity/test_workflow_sql.py::"
            self.dsn_items = {
                f"{prefix}test_result_nonfault_paired[{case['case_id']}]" for case in fixture["cases"]
            } | {
                f"{prefix}test_result_nonfault_control[Codec pre-admission negatives-{case_id}]"
                for case_id in fixture["controls"]["Codec pre-admission negatives"]
            }
            check(len(self.dsn_items) == 260, "Result DSN source roster")
            self.acquire_private()
            self.install()
        except BaseException as error:
            # The fixture owns nothing until this constructor actually returns.
            self.close(error)

    def poison_with(self, error):
        if self.poison is None:
            self.poison = error

    def require_live(self):
        if self.poison is not None:
            raise self.poison
        check(
            {key: key in os.environ for key in _OBSERVER_ENV} == self.environment, "Result inherited environment drift"
        )

    def acquire_private(self):
        descriptor = self.context["private_input"]
        namespace = os.stat("/proc/self/ns/user")
        observer_private_target(
            os.geteuid(), os.getegid(), {"dev": namespace.st_dev, "ino": namespace.st_ino}, descriptor
        )
        self.private_fd = os.open(descriptor["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        # Retain the returned handle before any fallible configuration/read.
        self.private_fact = observer_private_file_fact(os.fstat(self.private_fd), descriptor)
        self.private_raw = self.read_private()
        self.private_value = observer_private_bytes(self.private_raw)

    def private_live(self):
        check(self.private_fd is not None and not self.private_closed, "Result private input descriptor absent")
        descriptor = self.context["private_input"]
        check(
            observer_private_file_fact(os.fstat(self.private_fd), descriptor) == self.private_fact
            and observer_private_file_fact(os.stat(descriptor["path"], follow_symlinks=False), descriptor)
            == self.private_fact,
            "Result private input identity drift",
        )
        check(
            not any(
                name in {"system.posix_acl_access", "system.posix_acl_default"}
                for name in os.listxattr(self.private_fd)
            ),
            "Result private input ACL unsupported",
        )

    def read_private(self):
        self.private_live()
        data = bytearray()
        while block := os.pread(self.private_fd, min(1024, 4097 - len(data)), len(data)):
            data.extend(block)
            check(len(data) <= 4096, "Result private input read bound")
        self.private_live()
        check(len(data) == self.context["private_input"]["size"], "Result private input complete read")
        return bytes(data)

    def private_observe(self, kind, action):
        try:
            self.require_live()
            self.private_live()
            action()
            if self.dsn_matches[kind] is not False:
                self.dsn_matches[kind] = True
        except BaseException as error:
            self.dsn_matches[kind] = False
            self.poison_with(error)
            raise

    def observe_dsn_return(self, owner, actual_native):
        def observe():
            check(owner.test_item_identity.nodeid in self.dsn_items, "Result DSN selected Item")
            check(owner.constructor is not None, "Result DSN original constructor absent")
            observer_dsn_return(
                owner, self.active, self.private_value, owner.constructor["original_url"], actual_native
            )

        self.private_observe("driver_match", observe)

    def observe_dsn_use(self, owner, actual_native):
        self.private_observe(
            "driver_match", lambda: observer_dsn_use(owner, self.active, self.private_value, actual_native)
        )

    def finish_private(self):
        if self.private_fd is None:
            check(self.private_closed, "Result private input was not acquired")
            return
        first = None
        try:
            check(
                self.private_raw is not None and self.read_private() == self.private_raw,
                "Result private input final bytes drift",
            )
            self.private_verified = True
        except BaseException as error:
            first = error
        finally:
            try:
                os.close(self.private_fd)
                self.private_fd = None
                self.private_closed = True
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first

    def dsn_binding(self):
        returned = [owner for owner in self.entered if owner.dsn_return is not None]
        if returned and self.dsn_matches["driver_match"] is not False:
            self.dsn_matches["driver_match"] = all(owner.dsn_used for owner in returned)
        items = [owner.test_item_identity.nodeid for owner in returned]
        value = self.dsn_matches | {
            "complete": (
                all(match is True for match in self.dsn_matches.values())
                and self.private_verified
                and self.private_closed
                and len(items) == 260
                and set(items) == self.dsn_items
            )
        }
        observer_dsn_binding(value)
        return value

    @staticmethod
    def source_path(path):
        root = Path("/app").resolve(strict=True)
        check(root == Path("/app"), "Result source root relocation")
        expected = (root / Path(path).relative_to("api")).resolve(strict=True)
        check(expected.is_relative_to(root), "Result source namespace escape")
        return expected

    def find_module(self, path):
        expected = self.source_path(path)
        found = []
        for module in tuple(sys.modules.values()):
            if not isinstance(module, ModuleType):
                continue
            filename = vars(module).get("__file__")
            if type(filename) is not str or not filename.startswith("/app/"):
                continue
            try:
                resolved = Path(filename).resolve(strict=True)
            except FileNotFoundError:
                continue
            if resolved == expected and all(existing is not module for existing in found):
                found.append(module)
        check(len(found) == 1, "Result loaded module absent or ambiguous")
        return found[0]

    def capture_loaded_sources(self):
        for name, path in _OBSERVER_MODULES.items():
            module = sys.modules.get(name)
            check(module is not None and module is self.find_module(path), "Result normal loaded module")
            self.modules[path] = module
        for path in ("api/tests/conftest.py", "api/tests/parity/test_workflow_sql.py"):
            self.modules[path] = self.find_module(path)
        for path, module in self.modules.items():
            check(path in self.receipt["sources"], "Result observer source map incomplete")
            actual = hashlib.sha256(read_file(Path(module.__file__), 4 * 1024 * 1024)).hexdigest()
            mirror = hashlib.sha256(read_file(ROOT / path, 4 * 1024 * 1024)).hexdigest()
            observer_original(
                module,
                module,
                str(Path(module.__file__).resolve(strict=True)),
                str(self.source_path(path)),
                actual,
                mirror,
                self.receipt["sources"][path],
            )
        specs = {
            "src.jobs.consumers.workflow_execution": [
                ("WorkflowExecutionConsumer", "_process_success"),
                ("WorkflowExecutionConsumer", "_process_failure"),
                (None, "update_execution"),
            ],
            "src.repositories.executions": [(None, "update_execution"), (None, "_make_json_safe")],
            "src.core.execution_variable_safety": [(None, "sanitize_execution_variables")],
            "src.core.database": [
                (None, name) for name in ("get_engine", "get_session_factory", "_prepare_asyncpg_url", "close_db")
            ],
            "src.config": [(None, "get_settings"), (None, "Settings")],
            "src.models.orm.executions": [(None, "Execution"), (None, "WorkflowExecutionAttempt")],
            "src.models.enums": [(None, "ExecutionStatus")],
            "tests.parity.workflow_domain_harness": [
                ("WorkflowCohort", name) for name in ("__init__", "seed", "add_attempt", "close")
            ]
            + [(None, name) for name in ("get_session_factory", "has_recorded_attempt", "mark_attempt_running")],
            "tests.parity.workflow_sql_harness": [
                (None, name)
                for name in (
                    "materialize",
                    "prepare_json_inputs",
                    "decode",
                    "read_file",
                    "python_result",
                    "paired_result",
                    "observer_private_descriptor",
                    "observer_private_file_fact",
                    "observer_private_target",
                    "observer_private_bytes",
                    "observer_private_url",
                    "observer_private_owner",
                    "observer_dsn_return",
                    "observer_dsn_use",
                    "observer_dsn_binding",
                )
            ]
            + [("ResultCohort", name) for name in ("__init__", "seed", "empty_buffers", "snapshot", "close", "token")],
            "src.services.execution.attempts": [
                (None, name)
                for name in (
                    "has_recorded_attempt",
                    "mark_attempt_running",
                    "finalize_attempt",
                    "failure_attempt_status",
                )
            ],
        }
        for name, entries in specs.items():
            module = sys.modules[name]
            for owner, attribute in entries:
                target = module if owner is None else vars(module)[owner]
                original = vars(target)[attribute]
                defining = sys.modules.get(original.__module__)
                check(
                    defining is not None and defining.__file__ in {m.__file__ for m in self.modules.values()},
                    "Result defining module source",
                )
                unwrapped = inspect.unwrap(original)
                if hasattr(unwrapped, "__code__"):
                    check(
                        unwrapped.__code__.co_filename == defining.__file__ and unwrapped.__globals__ is vars(defining),
                        "Result original code and globals origin",
                    )
                self.bindings.append((target, attribute, original))
        tests = self.modules["api/tests/parity/test_workflow_sql.py"]
        for name in (
            "test_queued_cancel_emitted_update_order",
            "test_result_nonfault_paired",
            "test_result_nonfault_control",
        ):
            original = vars(tests)[name]
            check(
                original.__globals__ is vars(tests) and original.__code__.co_filename == tests.__file__,
                "Result loaded test origin",
            )
            self.bindings.append((tests, name, original))
        domain = sys.modules["tests.parity.workflow_domain_harness"]
        attempts = sys.modules["src.services.execution.attempts"]
        repository = sys.modules["src.repositories.executions"]
        check(consumer_module.update_execution is repository.update_execution, "Result original repository alias")
        check(
            _make_json_safe is repository._make_json_safe
            and sanitize_execution_variables
            is sys.modules["src.core.execution_variable_safety"].sanitize_execution_variables,
            "Result original sanitizer alias",
        )
        check(
            domain.get_session_factory is database.get_session_factory
            and WorkflowCohort is domain.WorkflowCohort
            and ResultCohort.__bases__ == (domain.WorkflowCohort,),
            "Result original factory and cohort aliases",
        )
        check(
            domain.has_recorded_attempt is attempts.has_recorded_attempt
            and domain.mark_attempt_running is attempts.mark_attempt_running,
            "Result normal attempts aliases",
        )
        check(
            database.get_settings is get_settings
            and database.Settings is sys.modules["src.config"].Settings
            and get_settings is sys.modules["src.config"].get_settings
            and Execution is sys.modules["src.models.orm.executions"].Execution
            and WorkflowExecutionAttempt is sys.modules["src.models.orm.executions"].WorkflowExecutionAttempt
            and ExecutionStatus is sys.modules["src.models.enums"].ExecutionStatus,
            "Result original harness aliases",
        )

        harness = sys.modules["tests.parity.workflow_sql_harness"]
        self.aliases = [
            (harness, name, vars(harness)[name])
            for name in (
                "consumer_module",
                "database",
                "get_settings",
                "WorkflowCohort",
                "Execution",
                "WorkflowExecutionAttempt",
                "ExecutionStatus",
                "_make_json_safe",
                "sanitize_execution_variables",
            )
        ]
        self.aliases.extend((database, name, vars(database)[name]) for name in ("get_settings", "Settings"))
        conftest = self.modules["api/tests/conftest.py"]
        self.aliases.extend((conftest, name, vars(conftest)[name]) for name in ("async_engine", "NullPool"))

    def loaded_source_admission(self, receipt, *, restored=False):
        if not restored:
            self.require_live()
        else:
            check(
                {key: key in os.environ for key in _OBSERVER_ENV} == self.environment,
                "Result final inherited environment drift",
            )
        check(
            receipt["candidate"] == self.context["candidate"] and receipt["sources"] == self.receipt["sources"],
            "Result observer source receipt drift",
        )
        replacements = (
            {} if restored else {(id(target), name): wrapper for target, name, _original, wrapper in self.originals}
        )
        for target, name, original in self.bindings:
            check(vars(target)[name] is replacements.get((id(target), name), original), "Result original binding drift")
        for target, name, original in self.aliases:
            check(vars(target)[name] is original, "Result original alias drift")
        for path, module in self.modules.items():
            expected = self.receipt["sources"][path]
            actual = hashlib.sha256(read_file(Path(module.__file__), 4 * 1024 * 1024)).hexdigest()
            mirror = hashlib.sha256(read_file(ROOT / path, 4 * 1024 * 1024)).hexdigest()
            observer_original(
                module,
                self.find_module(path),
                str(Path(module.__file__).resolve(strict=True)),
                str(self.source_path(path)),
                actual,
                mirror,
                expected,
            )

    def replace(self, target, name, wrapper):
        original = vars(target)[name]
        self.originals.append((target, name, original, wrapper))
        setattr(target, name, wrapper)

    @staticmethod
    def remove_hook(hook):
        target, name, callback = hook
        if event.contains(target, name, callback):
            event.remove(target, name, callback)
        check(not event.contains(target, name, callback), "Result own callback retained")

    def install(self):
        conftest = self.modules["api/tests/conftest.py"]
        fixture_function = inspect.unwrap(conftest.async_engine)
        check(
            inspect.isgeneratorfunction(fixture_function)
            and fixture_function.__globals__ is vars(conftest)
            and fixture_function.__code__.co_filename == conftest.__file__,
            "Result genuine fixture function",
        )
        self.fixture["source_module"] = conftest
        self.fixture["source_function"] = fixture_function
        self.fixture["original_alias"] = conftest.create_async_engine
        self.fixture["original_url"] = conftest.TEST_DATABASE_URL
        original_fixture = conftest.create_async_engine
        original_production = database.create_async_engine
        original_close = database.close_db
        check(
            original_fixture is original_production
            and original_fixture.__module__ == "sqlalchemy.ext.asyncio.engine"
            and conftest.NullPool is sys.modules["sqlalchemy.pool.impl"].NullPool
            and original_fixture is sys.modules["sqlalchemy.ext.asyncio.engine"].create_async_engine
            and sys.modules["sqlalchemy"].__version__ == "2.0.49",
            "Result original third-party constructor",
        )

        def fixture_constructor(*args, **kwargs):
            self.require_live()
            check(self.fixture["construction_count"] == 0, "Result duplicate fixture construction")
            # Observe the actual original return before associating it; no constructor replay.
            engine = original_fixture(*args, **kwargs)
            self.fixture["engine"] = engine
            self.fixture["construction_count"] = 1
            observer_fixture(args, kwargs, self.fixture["original_url"], conftest.NullPool, engine)
            self.private_observe(
                "fixture_match",
                lambda: observer_private_url(self.private_value, make_url(self.fixture["original_url"])),
            )
            gate = (engine.sync_engine, "before_cursor_execute", self.fixture_sql_gate)
            self.session_hooks.append(gate)
            event.listen(*gate)
            self.publish_fixture_endpoint(engine.url)
            return engine

        def production_constructor(*args, **kwargs):
            self.require_live()
            owner = self.active
            check(
                owner is not None and owner.phase == "entered" and owner.constructor is None,
                "Result production constructor lifetime",
            )
            frame = inspect.currentframe()
            caller = None
            try:
                caller = frame.f_back
                check(
                    caller is not None
                    and caller.f_code is self.original_get_engine.__code__
                    and caller.f_globals is vars(database)
                    and caller.f_code.co_filename == database.__file__,
                    "Result original constructor callsite",
                )
                settings = caller.f_locals["settings"]
                prepared = caller.f_locals["db_url"]
                options = caller.f_locals["connect_args"]
                check(
                    type(settings) is sys.modules["src.config"].Settings
                    and type(prepared) is str
                    and type(options) is dict,
                    "Result actual source constructor local types",
                )
            finally:
                del caller
                del frame
            check(
                args == (prepared,)
                and type(kwargs) is dict
                and set(kwargs)
                == {
                    "echo",
                    "pool_size",
                    "max_overflow",
                    "pool_pre_ping",
                    "pool_timeout",
                    "pool_recycle",
                    "connect_args",
                },
                "Result production source constructor",
            )
            check(
                kwargs["echo"] is settings.debug
                and kwargs["pool_size"] == settings.database_pool_size
                and kwargs["max_overflow"] == settings.database_max_overflow
                and kwargs["pool_pre_ping"] is True
                and kwargs["pool_timeout"] == 30
                and kwargs["pool_recycle"] == 1800
                and kwargs["connect_args"] is options,
                "Result production source options",
            )
            observer_original(
                database.get_engine,
                self.original_get_engine,
                self.original_get_engine.__code__.co_filename,
                database.__file__,
                hashlib.sha256(read_file(Path(database.__file__), 4 * 1024 * 1024)).hexdigest(),
                hashlib.sha256(read_file(ROOT / "api/src/core/database.py", 4 * 1024 * 1024)).hexdigest(),
                self.receipt["sources"]["api/src/core/database.py"],
            )
            original_url = make_url(settings.database_url)
            check(
                not options
                and set(original_url.query) <= {"sslmode"}
                and original_url.query.get("sslmode") in {None, "disable"},
                "Result observed TLS profile unsupported",
            )
            check(
                make_url(prepared).host == self.fixture["engine"].url.host
                and make_url(prepared).port == self.fixture["engine"].url.port
                and make_url(prepared).drivername == "postgresql+asyncpg",
                "Result fixture production endpoint association",
            )
            self.private_observe("production_match", lambda: observer_private_url(self.private_value, original_url))
            check(make_url(prepared) == self.fixture["engine"].url, "Result full fixture production URL")
            engine = original_production(*args, **kwargs)
            owner.constructor = {
                "settings": settings,
                "original_url": original_url,
                "prepared": prepared,
                "options": options,
                "kwargs": kwargs,
                "engine": engine,
                "factory": None,
            }
            self.install_physical(owner, engine, original_url)
            return engine

        async def close_observed():
            owner = self.active
            actual_engine, actual_factory = database._engine, database._async_session_factory
            try:
                await original_close()
            except BaseException as error:
                self.poison_with(error)
                raise
            if owner is not None:
                try:
                    record = owner.constructor
                    observer_phase(
                        owner.phase,
                        record["engine"] if record else None,
                        actual_engine,
                        record["factory"] if record else None,
                        actual_factory,
                        database._engine,
                        database._async_session_factory,
                    )
                    check(len(self.completed) < 299, "Result completed lifetime bound")
                    owner.closed = True
                    owner.phase = "completed"
                    self.completed.append(owner)
                    self.active = None
                except BaseException as error:
                    self.poison_with(error)
                    raise
            else:
                check(actual_engine is None and actual_factory is None, "Result unrelated source close lifetime")

        self.original_get_engine = database.get_engine
        self.replace(conftest, "create_async_engine", fixture_constructor)
        self.replace(database, "create_async_engine", production_constructor)
        self.replace(database, "close_db", close_observed)

    def fixture_sql_gate(self, *_args):
        check(self.fixture["endpoint_admitted"], "Result earliest SQL frontend gate")

    def begin_function(self, item):
        self.require_live()
        check(
            self.active is None and database._engine is None and database._async_session_factory is None,
            "Result source pre-close lifetime",
        )
        self.loaded_source_admission(source_admission())
        check(isinstance(item, sys.modules["pytest"].Item), "Result genuine public Item")
        observer_item(item, self.pytest_session, self.entered)
        owner = ResultSourceFunction(self, item)
        self.active = owner
        self.entered.append(owner)
        return owner

    def install_physical(self, owner, engine, original_url):
        preconnect = []

        def connecting(dialect, record, cargs, cparams):
            check(
                self.active is owner and owner.phase == "entered" and dialect is engine.sync_engine.dialect,
                "Result PRECONNECT lifetime",
            )
            observer_preconnect(cargs, cparams, original_url)
            if not any(existing is record for existing in preconnect):
                check(len(preconnect) < 16, "Result PRECONNECT record bound")
                preconnect.append(record)

        def connected(dbapi, record):
            check(any(existing is record for existing in preconnect), "Result physical source PRECONNECT association")
            observer_pair(owner.connections, record, dbapi)

        def checkout(dbapi, record, _proxy):
            observer_pair(owner.connections, record, dbapi, require=True)

        def executing(connection, *_args):
            check(self.active is owner and owner.phase == "entered", "Result actual SQL owner")
            owner.admit_production_engine(engine)
            physical = connection.connection.dbapi_connection
            # Actual source may open separate completion-metrics sessions. Record
            # their physical association without widening or replacing clock roles.
            observer_actor(owner.connections, physical, connection, _CLOCK_ACTOR.get())

        owner.listen(engine.sync_engine, "do_connect", connecting)
        owner.listen(engine.sync_engine.pool, "connect", connected)
        owner.listen(engine.sync_engine.pool, "checkout", checkout)
        owner.listen(engine.sync_engine, "before_cursor_execute", executing)

    def shared_path(self, suffix):
        return Path("/bifrost-results") / f".result-endpoint-{self.context['invocation_uuid']}-{suffix}.json"

    def publish_fixture_endpoint(self, url):
        # Actual constructor handshake origin; never child startup or parent-clock equality.
        end = time.monotonic() + min(5, self.context["target_remaining_seconds"])
        check(
            type(url.host) is str
            and url.host.isascii()
            and 1 <= len(url.host) <= 253
            and type(url.port) is int
            and 1 <= url.port <= 65535
            and url.drivername == "postgresql+asyncpg",
            "Result actual fixture endpoint types",
        )
        request = {
            "schema": "bifrost.private.result-endpoint/v1",
            "invocation_uuid": self.context["invocation_uuid"],
            "candidate": self.context["candidate"],
            "phase": "fixture_constructed_before_sql",
            "fixture_source_sha256": self.receipt["sources"]["api/tests/conftest.py"],
            "provider": "conftest_NullPool",
            "construction_index": 1,
            "hostname": url.host,
            "port": url.port,
            "drivername": url.drivername,
        }
        observer_request(request, self.context, self.receipt["sources"]["api/tests/conftest.py"], url)
        raw = json.dumps(request, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
        check(len(raw) <= 4096 and os.geteuid() == 1000, "Result endpoint publication bound")
        directory = os.stat("/bifrost-results", follow_symlinks=False)
        check(
            stat.S_ISDIR(directory.st_mode) and stat.S_IMODE(directory.st_mode) == 0o777 and directory.st_uid == 1000,
            "Result supported shared directory",
        )
        final = self.shared_path("request")
        temporary = self.shared_path("request.part")
        fd = None
        original = None
        try:
            temporary_index = len(self.owned_files)
            self.owned_files.append((temporary, None, None))
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
            info = os.fstat(fd)
            self.owned_files[temporary_index] = (temporary, info.st_dev, info.st_ino)
            os.fchmod(fd, 0o644)
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                check(written > 0, "Result metadata write incomplete")
                offset += written
            os.fsync(fd)
            info = os.fstat(fd)
            observer_file_fact(info, uid=1000, limit=4096)
            os.close(fd)
            fd = None
            final_index = len(self.owned_files)
            self.owned_files.append((final, None, None))
            os.link(temporary, final, follow_symlinks=False)
            self.owned_files[final_index] = (final, info.st_dev, info.st_ino)
            os.unlink(temporary)
            check(
                observer_file_fact(os.stat(final, follow_symlinks=False), uid=1000, limit=4096)
                == observer_file_fact(info, uid=1000, limit=4096)
                and not os.path.lexists(temporary),
                "Result endpoint publication readback",
            )
            self.fixture["endpoint_published"] = True
            self.read_endpoint_decision(end)
        except BaseException as error:
            original = error
            self.poison_with(error)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as error:
                    if original is None:
                        original = error
                        self.poison_with(error)
        if original is not None:
            raise original

    def read_shared(self, path, *, limit, uid, end):
        temporary = path.with_name(path.stem + ".part.json")
        while True:
            info = os.stat(path, follow_symlinks=False)
            if info.st_nlink != 2:
                break
            check(time.monotonic() < end, "Result metadata publication deadline")
            observer_publication(info, os.stat(temporary, follow_symlinks=False), uid=uid, limit=limit)
            time.sleep(0.01)
        before = observer_file_fact(info, uid=uid, limit=limit)
        fd = None
        original = None
        data = bytearray()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            check(observer_file_fact(os.fstat(fd), uid=uid, limit=limit) == before, "Result opened metadata identity")
            self.owned_files.append((path, info.st_dev, info.st_ino))
            while block := os.read(fd, min(1024, limit + 1 - len(data))):
                data.extend(block)
                check(len(data) <= limit, "Result metadata read bound")
            check(
                observer_file_fact(os.fstat(fd), uid=uid, limit=limit) == before and len(data) == info.st_size,
                "Result metadata complete stable read",
            )
        except BaseException as error:
            original = error
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as error:
                    if original is None:
                        original = error
        if original is not None:
            raise original
        after = observer_file_fact(os.stat(path, follow_symlinks=False), uid=uid, limit=limit)
        check(before == after and not os.path.lexists(temporary), "Result shared metadata stable publication")
        return decode(bytes(data))

    def read_endpoint_decision(self, end):
        admit, failed = self.shared_path("admit"), self.shared_path("failed")
        while True:
            have_admit, have_failed = os.path.lexists(admit), os.path.lexists(failed)
            observer_decisions(have_admit, have_failed)
            if have_admit or have_failed:
                value = self.read_shared(
                    failed if have_failed else admit, limit=1024, uid=self.context["parent_uid"], end=end
                )
                observer_decision(value, self.context, rejected=have_failed)
                check(time.monotonic() < end, "Result endpoint admission deadline")
                check(not have_failed, "Result frontend rejected")
                check(not os.path.lexists(failed), "Result late duplicate endpoint decision")
                self.fixture["endpoint_admitted"] = True
                return
            check(time.monotonic() < end, "Result endpoint decision deadline")
            time.sleep(0.01)

    def close(self, original=None):
        first = original if original is not None else self.poison
        cleanup_failed = False
        outcomes = dict.fromkeys(_OBSERVER_CHECKS)
        actions = []
        for hook in self.session_hooks:
            actions.append(lambda hook=hook: self.remove_hook(hook))
        # Function finalizers already attempted these hooks; independently verify/remove retained own hooks.
        for owner in self.entered:
            actions.extend(lambda hook=hook: self.remove_hook(hook) for hook in owner.own_listeners)
        for target, name, captured, wrapper in reversed(self.originals):

            def restore(target=target, name=name, captured=captured, wrapper=wrapper):
                check(vars(target)[name] is wrapper, "Result foreign observer alias replacement")
                setattr(target, name, captured)
                check(vars(target)[name] is captured, "Result observer alias restoration")

            actions.append(restore)
        for path, device, inode in self.owned_files:

            def remove(path=path, device=device, inode=inode):
                try:
                    info = os.stat(path, follow_symlinks=False)
                except FileNotFoundError:
                    return
                check(
                    device is not None and inode is not None and (info.st_dev, info.st_ino) == (device, inode),
                    "Result foreign metadata cleanup",
                )
                os.unlink(path)
                check(not os.path.lexists(path), "Result metadata retained")

            actions.append(remove)

        def lifetime_complete():
            check(self.active is None, "Result source close not completed")
            check(
                {key: key in os.environ for key in _OBSERVER_ENV} == self.environment,
                "Result final inherited environment drift",
            )

        actions.append(lifetime_complete)
        # This descriptor belongs to the observer; its mount/path belongs to the parent.
        actions.append(self.finish_private)
        first, cleanup_failed = observer_settle(actions, first)

        def hooks_removed():
            hooks = self.session_hooks + [hook for owner in self.entered for hook in owner.own_listeners]
            check(all(not event.contains(*hook) for hook in hooks), "Result observer hook readback")

        def wrappers_restored():
            check(
                all(vars(target)[name] is captured for target, name, captured, _wrapper in self.originals),
                "Result observer wrapper readback",
            )

        def metadata_absent():
            check(
                all(not os.path.lexists(path) for path, _device, _inode in self.owned_files),
                "Result observer metadata readback",
            )

        checks = {
            "loaded_bindings_verified": lambda: self.loaded_source_admission(source_admission(), restored=True),
            "fixture_gate_removed": hooks_removed,
            "session_wrappers_restored": wrappers_restored,
            "metadata_absent": metadata_absent,
        }
        for name, action in checks.items():
            try:
                action()
                outcomes[name] = True
            except BaseException as error:
                outcomes[name] = False
                cleanup_failed = True
                if first is None:
                    first = error
        # Protected publication does not claim later unrelated finalizers succeeded.
        try:
            check(self.entered, "Result final Item absent")
            item = self.entered[-1].test_item_identity
            observer_item(item, self.pytest_session, self.entered, require_last=True)
            production = [owner for owner in self.entered if owner.constructor is not None]
            value = {
                "schema": "bifrost.private.result-source-observer-final/v2",
                "candidate": self.context["candidate"],
                "invocation_uuid": self.context["invocation_uuid"],
                "phase": "observer_session_finalizer_after_owned_cleanup",
                "fixture_constructions": self.fixture["construction_count"],
                "functions_entered": len(self.entered),
                "functions_completed": len(self.completed),
                "production_lifetimes": len(production),
                "production_closed": sum(owner.closed for owner in production),
                **outcomes,
                "poisoned": self.poison is not None,
                "cleanup_failed": cleanup_failed,
                "complete": False,
                "export_item": item.nodeid,
                "dsn_binding": self.dsn_binding(),
            }
            value["complete"] = (
                value["fixture_constructions"] == 1
                and self.fixture["endpoint_admitted"]
                and value["functions_entered"] > 0
                and value["functions_completed"] == value["functions_entered"]
                and value["production_closed"] == value["production_lifetimes"]
                and all(value[name] is True for name in _OBSERVER_CHECKS)
                and not value["poisoned"]
                and not value["cleanup_failed"]
                and value["dsn_binding"]["complete"]
            )
            raw = observer_final_record(value, self.context, item.nodeid)
            observer_append_final(item, raw)
        except BaseException as error:
            if first is None:
                first = error
        if first is not None:
            raise first


def source_session(pytest_session):
    raw = os.environ.get("BIFROST_RESULT_OBSERVER_CONTEXT")
    if raw is None:
        return None
    check(raw.isascii() and len(raw.encode("ascii")) <= 4096, "Result observer context bound")
    return ResultSourceObserver(decode(raw.encode("ascii")), pytest_session)


def require_source_observer(case):
    owner = case.source_observer
    check(type(owner) is ResultSourceFunction and owner.session.active is owner, "Result observer input absent")
    owner.session.loaded_source_admission(source_admission())
    owner.admit_fixture_engine(case.fixture_engine)
    if database._engine is not None:
        owner.admit_production_engine(database._engine)
    return owner


def source_observer_controls():
    """Eight synthetic families exercise these exact runtime validators; no custody claim."""

    def negative(action):
        try:
            action()
        except AssertionError:
            return
        raise AssertionError("Result source observer negative control accepted")

    completed = 0
    original, foreign = object(), object()
    observer_original(original, original, "/app/a", "/app/a", "d", "d", "d")
    negative(lambda: observer_original(original, foreign, "/app/a", "/app/a", "d", "d", "d"))
    negative(lambda: observer_original(original, original, "/app/a", "/app/a", "d", "x", "d"))
    negative(lambda: observer_original(original, original, "/api/a", "/app/a", "d", "d", "d"))
    synthetic_lock = b'version = 4\n[[package]]\nname = "serde_json"\nversion = "1.0.151"\n'
    packages = source_lock_packages(synthetic_lock)
    graph = {
        "sha256": "a" * 64,
        "packages": ["serde_json@1.0.151"],
        "features": ["serde_json@1.0.151/raw_value", "serde_json@1.0.151/std"],
    }
    source_graph_summary(graph, packages)
    source_graph_summary(graph | {"features": []}, packages)
    for bad_lock in (
        b"\xff",
        b"version = [",
        synthetic_lock.replace(b"version = 4", b"version = 4.0"),
        synthetic_lock + b'[[package]]\nname = "serde_json"\nversion = "1.0.151"\n',
        synthetic_lock.replace(b'"serde_json"', b'"serde/json"'),
    ):
        negative(lambda bad_lock=bad_lock: source_lock_packages(bad_lock))
    for bad_graph in (
        graph | {"unknown": None},
        graph | {"sha256": True},
        graph | {"packages": []},
        graph | {"packages": ["serde_json@1.0.151"] * 2},
        graph | {"packages": ["foreign@1"]},
        graph | {"features": True},
        graph | {"features": [True]},
        graph | {"features": list(reversed(graph["features"]))},
        graph | {"features": graph["features"] * 2},
        graph | {"features": ["foreign@1/std"]},
        graph | {"features": ["serde_json@1.0.151/std/extra"]},
        graph | {"features": ["serde_json@1.0.151/é"]},
        graph | {"features": ["serde_json@1.0.151/" + "x" * 129]},
        graph | {"features": ["serde_json@1.0.151/float_roundtrip"]},
        graph | {"features": ["serde_json@1.0.151/arbitrary_precision"]},
    ):
        negative(lambda bad_graph=bad_graph: source_graph_summary(bad_graph, packages))
    completed += 1
    url = make_url("postgresql+asyncpg://synthetic:synthetic@localhost:5432/synthetic")
    engine, null_pool = SimpleNamespace(url=url), object()
    observer_fixture((url,), {"echo": False, "poolclass": null_pool}, url, null_pool, engine)
    negative(lambda: observer_fixture((url,), {"echo": False, "poolclass": foreign}, url, null_pool, engine))
    native = url.set(drivername="postgresql").render_as_string(hide_password=False)
    observer_private_url(native, url, native)
    unicode_url = url.set(database="synthétique")
    unicode_native = unicode_url.set(drivername="postgresql").render_as_string(hide_password=False)
    observer_private_url(unicode_native, unicode_url, unicode_native)
    for changed in (url.set(database="other"), url.set(username="other"), url.set(query={"sslmode": "disable"})):
        negative(lambda changed=changed: observer_private_url(native, changed))
    for changed_native in (f'"{native}"', "${RESULT_SYNTHETIC_URL}", native + " "):
        negative(lambda changed_native=changed_native: observer_private_url(changed_native, url))
    negative(lambda: observer_private_url(native, url, unicode_native))
    completed += 1
    observer_phase("awaiting_source_close", engine, engine, original, original)
    negative(lambda: observer_phase("entered", engine, engine, original, original))
    negative(lambda: observer_phase("awaiting_source_close", engine, foreign, original, original))
    second_engine = object()
    observer_phase("awaiting_source_close", second_engine, second_engine, None, None)
    negative(lambda: observer_phase("awaiting_source_close", engine, engine, original, original, engine, None))
    private_owner = SimpleNamespace(phase="entered", dsn_return=None, dsn_used=False)
    observer_private_owner(private_owner, private_owner)
    negative(lambda: observer_private_owner(private_owner, foreign))
    private_owner.phase = "completed"
    negative(lambda: observer_private_owner(private_owner, private_owner))
    private_owner.phase = "entered"
    completed += 1
    parameters = {
        "host": "localhost",
        "port": 5432,
        "user": "synthetic",
        "password": "synthetic",
        "database": "synthetic",
    }
    observer_preconnect([], parameters, url)
    negative(lambda: observer_preconnect([], parameters | {"ssl": False}, url))
    negative(lambda: observer_preconnect([], parameters | {"port": True}, url))
    negative(lambda: observer_private_url(unicode_native, url))
    completed += 1
    pairs = []
    observer_pair(pairs, original, engine)
    observer_pair(pairs, original, engine, require=True)
    observer_actor(pairs, engine, foreign, original)
    check(pairs[0][2] is foreign and pairs[0][3] is original, "Result actor reference control")
    negative(lambda: observer_actor(pairs, original, foreign, original))
    negative(lambda: observer_pair(pairs, foreign, engine, require=True))
    observer_dsn_return(private_owner, private_owner, native, url, native)
    negative(lambda: observer_dsn_return(private_owner, private_owner, native, url, native))
    negative(lambda: observer_dsn_use(private_owner, foreign, native, native))
    negative(lambda: observer_dsn_use(private_owner, private_owner, native, unicode_native))
    unreturned = SimpleNamespace(phase="entered", dsn_return=None, dsn_used=False)
    negative(lambda: observer_dsn_return(unreturned, unreturned, native, url, None))
    negative(lambda: observer_dsn_use(unreturned, unreturned, native, native))
    observer_dsn_use(private_owner, private_owner, native, native)
    negative(lambda: observer_dsn_use(private_owner, private_owner, native, native))
    completed += 1
    context = {
        "schema": "bifrost.private.result-observer-context/v2",
        "candidate": {"head": "a" * 40, "tree": "b" * 40},
        "invocation_uuid": "00000000-0000-4000-8000-000000000001",
        "parent_uid": 1001,
        "target_remaining_seconds": 900,
        "private_input": {
            "schema": "bifrost.private.result-dsn-input/v1",
            "path": "/bifrost-private/result-all-features.env",
            "dev": 1,
            "ino": 2,
            "owner_uid": 1001,
            "gid": 1000,
            "mode": 0o640,
            "size": len(("BIFROST_RUST_TEST_DATABASE_URL=" + native + "\n").encode()),
            "nlink": 1,
            "user_namespace": {"dev": 3, "ino": 4},
        },
    }
    observer_context(context)
    decision = {
        "schema": "bifrost.private.result-endpoint-decision/v1",
        "candidate": context["candidate"],
        "invocation_uuid": context["invocation_uuid"],
        "phase": "frontend_observed_before_sql",
        "decision": "admit",
    }
    request = {
        "schema": "bifrost.private.result-endpoint/v1",
        "invocation_uuid": context["invocation_uuid"],
        "candidate": context["candidate"],
        "phase": "fixture_constructed_before_sql",
        "fixture_source_sha256": "c" * 64,
        "provider": "conftest_NullPool",
        "construction_index": 1,
        "hostname": url.host,
        "port": url.port,
        "drivername": url.drivername,
    }
    observer_request(request, context, "c" * 64, url)
    negative(lambda: observer_request(request | {"construction_index": True}, context, "c" * 64, url))
    observer_decisions(True, False)
    negative(lambda: observer_decisions(True, True))
    observer_decision(decision, context)
    negative(lambda: observer_decision(decision | {"decision": "reject"}, context))
    negative(lambda: observer_context(context | {"parent_uid": True}))
    negative(lambda: observer_context({key: value for key, value in context.items() if key != "private_input"}))
    negative(lambda: observer_context(context | {"unknown": None}))
    descriptor = context["private_input"]
    for changed in (
        descriptor | {"unknown": None},
        descriptor | {"dev": True},
        descriptor | {"ino": 0},
        descriptor | {"path": "/other"},
        descriptor | {"owner_uid": 1000},
        descriptor | {"gid": -1},
        descriptor | {"mode": 0o644},
        descriptor | {"mode": True},
        descriptor | {"size": 4097},
        descriptor | {"nlink": 2},
        descriptor | {"user_namespace": {"dev": True, "ino": 4}},
    ):
        negative(lambda changed=changed: observer_context(context | {"private_input": changed}))
    completed += 1
    info = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o644, st_uid=1000, st_nlink=1, st_size=100, st_dev=1, st_ino=2, st_mtime_ns=3
    )
    observer_file_fact(info, uid=1000, limit=4096)
    negative(lambda: observer_file_fact(info, uid=1001, limit=4096))
    negative(lambda: observer_file_fact(info, uid=1000, limit=99))
    linked = SimpleNamespace(**(vars(info) | {"st_nlink": 2}))
    observer_publication(linked, linked, uid=1000, limit=4096)
    negative(
        lambda: observer_publication(linked, SimpleNamespace(**(vars(linked) | {"st_ino": 9})), uid=1000, limit=4096)
    )
    private_info = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o640,
        st_uid=1001,
        st_gid=1000,
        st_nlink=1,
        st_size=descriptor["size"],
        st_dev=1,
        st_ino=2,
        st_mtime_ns=3,
    )
    fact = observer_private_file_fact(private_info, descriptor)
    observer_private_target(1000, 1000, descriptor["user_namespace"], descriptor)
    negative(lambda: observer_private_target(1001, 1000, descriptor["user_namespace"], descriptor))
    negative(lambda: observer_private_target(1000, 1001, descriptor["user_namespace"], descriptor))
    negative(lambda: observer_private_target(1000, 1000, {"dev": 3, "ino": 5}, descriptor))
    negative(lambda: observer_private_target(1000, 1000, {"dev": True, "ino": 4}, descriptor))
    for name, value in (
        ("st_mode", stat.S_IFIFO | 0o640),
        ("st_mode", stat.S_IFREG | 0o644),
        ("st_uid", 1000),
        ("st_gid", 1001),
        ("st_nlink", 2),
        ("st_size", 0),
        ("st_dev", 2),
        ("st_ino", 3),
    ):
        changed = SimpleNamespace(**(vars(private_info) | {name: value}))
        negative(lambda changed=changed: observer_private_file_fact(changed, descriptor))
    changed = SimpleNamespace(**(vars(private_info) | {"st_mtime_ns": 4}))
    negative(lambda: check(observer_private_file_fact(changed, descriptor) == fact, "Result private fact control"))
    prefix = b"BIFROST_RUST_TEST_DATABASE_URL="
    raw_input = prefix + native.encode() + b"\n"
    check(observer_private_bytes(raw_input) == native, "Result private grammar positive")
    check(
        observer_private_bytes(prefix + unicode_native.encode() + b"\n") == unicode_native,
        "Result private UTF-8 grammar positive",
    )
    for bad in (
        b"",
        prefix + b"x" * 4096 + b"\n",
        prefix + b"\xff\n",
        b"\xef\xbb\xbf" + raw_input,
        prefix + b"\x00\n",
        raw_input[:-1] + b"\r\n",
        raw_input[:-1],
        raw_input + b"\n",
        prefix + b"\n",
        b"OTHER=" + native.encode() + b"\n",
        prefix[:-1] + b"\n",
        raw_input + raw_input,
        b" " + raw_input,
    ):
        negative(lambda bad=bad: observer_private_bytes(bad))
    for text_value in (f'"{native}"', "${RESULT_SYNTHETIC_URL}"):
        parsed = observer_private_bytes(prefix + text_value.encode() + b"\n")
        negative(lambda parsed=parsed: observer_private_url(parsed, url))
    completed += 1
    sentinel = KeyboardInterrupt()
    settled = []
    try:
        observer_independent(
            [lambda: settled.append(1), lambda: (_ for _ in ()).throw(ValueError()), lambda: settled.append(2)],
            sentinel,
        )
    except BaseException as error:
        check(error is sentinel and settled == [1, 2], "Result first error cleanup identity")
    else:
        raise AssertionError("Result first error control swallowed")
    settled.clear()
    try:
        observer_independent([lambda: (_ for _ in ()).throw(sentinel), lambda: settled.append(3)])
    except BaseException as error:
        check(error is sentinel and settled == [3], "Result acquired cleanup first-object control")
    else:
        raise AssertionError("Result acquired control swallowed")
    settled.clear()
    close_error = ValueError("synthetic close failure")
    try:
        observer_independent([lambda: (_ for _ in ()).throw(close_error), lambda: settled.append(4)], sentinel)
    except BaseException as error:
        check(error is sentinel and settled == [4], "Result private close original identity")
    else:
        raise AssertionError("Result private close control swallowed")
    session = SimpleNamespace(items=[])
    session.items = [
        SimpleNamespace(
            session=session,
            nodeid=f"tests/parity/test_workflow_sql.py::test_result_nonfault_control[p-synthetic-{index}]",
            user_properties=[],
        )
        for index in range(299)
    ]
    item = session.items[-1]
    entered = [SimpleNamespace(test_item_identity=item)]
    observer_item(item, session, [], require_last=False)
    observer_item(item, session, entered, require_last=True)
    negative(lambda: observer_item(session.items[0], session, entered, require_last=True))
    final = {
        "schema": "bifrost.private.result-source-observer-final/v2",
        "candidate": context["candidate"],
        "invocation_uuid": context["invocation_uuid"],
        "phase": "observer_session_finalizer_after_owned_cleanup",
        "fixture_constructions": 1,
        "functions_entered": 299,
        "functions_completed": 299,
        "production_lifetimes": 2,
        "production_closed": 2,
        **dict.fromkeys(_OBSERVER_CHECKS, True),
        "poisoned": False,
        "cleanup_failed": False,
        "complete": True,
        "export_item": item.nodeid,
        "dsn_binding": {"fixture_match": True, "production_match": True, "driver_match": True, "complete": True},
    }
    raw = observer_final_record(final, context, item.nodeid, require_complete=True)
    negative(
        lambda: observer_final_record(final | {"metadata_absent": None}, context, item.nodeid, require_complete=True)
    )
    negative(lambda: observer_final_record(final | {"functions_completed": True}, context, item.nodeid))
    binding = final["dsn_binding"]
    negative(
        lambda: observer_final_record(
            {key: value for key, value in final.items() if key != "dsn_binding"}, context, item.nodeid
        )
    )
    for bad in (binding | {"unknown": None}, binding | {"fixture_match": 1}, binding | {"driver_match": None}):
        negative(lambda bad=bad: observer_final_record(final | {"dsn_binding": bad}, context, item.nodeid))
    for changed_binding in (
        binding | {"driver_match": False, "complete": False},
        binding | {"fixture_match": False, "complete": False},
        binding | {"complete": False},
    ):
        negative(
            lambda changed_binding=changed_binding: observer_final_record(
                final | {"complete": False, "dsn_binding": changed_binding}, context, item.nodeid, require_complete=True
            )
        )
    partial = final | {
        "complete": False,
        "dsn_binding": {
            "fixture_match": None,
            "production_match": None,
            "driver_match": None,
            "complete": False,
        },
    }
    observer_final_record(partial, context, item.nodeid)
    observer_append_final(item, raw)
    negative(lambda: observer_append_final(item, raw))
    check(item.user_properties == [("result_source_observer_final", raw)], "Result final publication control")
    completed += 1
    return completed


class CaseLifetime:
    def __init__(self, request):
        check(not _HELD_CUSTODY, "Result prior custody retained")
        self.request = request
        self.start = asyncio.get_running_loop().time()
        self.work_end = self.start + 75
        self.end = self.start + 90
        self.tasks = set()
        self.processes = []
        self.cohorts = []
        self.retained = False
        self.retain_dependencies = False

    def task(self, coroutine):
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            with suppress(BaseException):
                coroutine.close()
            raise
        self.tasks.add(task)
        return task

    async def cleanup(self):
        global _HELD_CUSTODY
        process_error = None
        for process in self.processes:
            try:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
            except BaseException as error:
                if process_error is None:
                    process_error = error
            try:
                async with asyncio.timeout_at(self.end):
                    await process.wait()
            except BaseException as error:
                if process_error is None:
                    process_error = error
        for task in self.tasks:
            try:
                if not task.done():
                    task.cancel()
            except BaseException as error:
                if process_error is None:
                    process_error = error
        pending = [task for task in self.tasks if not task.done()]
        if pending:
            try:
                left = self.end - asyncio.get_running_loop().time()
                if left > 0:
                    await asyncio.wait(pending, timeout=left)
            except BaseException as error:
                if process_error is None:
                    process_error = error
        if (
            self.retain_dependencies
            or process_error is not None
            or any(process.returncode is None for process in self.processes)
            or any(not task.done() for task in self.tasks)
        ):
            self.retained = _HELD_CUSTODY = True
            if not self.request.session.shouldstop:
                self.request.session.shouldstop = "Result owned task custody retained"
            if process_error is not None:
                raise process_error
            raise AssertionError("Result owned task remains pending")
        original = None
        for cohort in reversed(self.cohorts):
            try:
                async with asyncio.timeout_at(self.end):
                    await cohort.close(self.end)
            except BaseException as error:
                if original is None:
                    original = error
        if original is not None:
            self.retained = _HELD_CUSTODY = True
            if not self.request.session.shouldstop:
                self.request.session.shouldstop = "Result resource custody retained"
            raise original


@asynccontextmanager
async def lifetime(request, observer=None, fixture_engine=None):
    check(type(observer) is ResultSourceFunction, "Result observer input absent")
    case = CaseLifetime(request)
    case.source_observer = observer
    case.fixture_engine = fixture_engine
    require_source_observer(case)
    original = None
    try:
        async with asyncio.timeout_at(case.work_end):
            yield case
    except BaseException as error:
        original = error
    finally:
        try:
            await case.cleanup()
        except BaseException as error:
            if original is None:
                original = error
    if original is not None:
        raise original


async def invoke(case: CaseLifetime, payload: bytes, mode: str, *, database_url: str | None = None):
    owner = require_source_observer(case)
    check(
        mode in {"apply-result", "decode-number", "observe-feature-marker"} and len(payload) <= 65537,
        "Result invocation admission",
    )
    if mode == "apply-result":
        owner.session.observe_dsn_use(owner, database_url)
    else:
        check(database_url is None, "Result non-DB mode environment")
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL") if key in os.environ}
    if database_url is not None:
        env["BIFROST_RUST_TEST_DATABASE_URL"] = database_url
    proc = None
    original = None
    tasks = []
    stdout = stderr = b""
    end = min(case.work_end, asyncio.get_running_loop().time() + 30)

    async def read(stream):
        value = bytearray()
        while chunk := await stream.read(4097 - len(value)):
            value.extend(chunk)
            check(len(value) <= 4096, "Result child output bound")
        return bytes(value)

    try:
        async with asyncio.timeout_at(end):
            proc = await asyncio.create_subprocess_exec(
                str(DRIVER),
                mode,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
                limit=4096,
            )
            case.processes.append(proc)
            check(proc.stdin is not None and proc.stdout is not None and proc.stderr is not None, "Result child pipes")
            tasks = [case.task(read(proc.stdout)), case.task(read(proc.stderr))]
            proc.stdin.write(payload)
            await proc.stdin.drain()
            proc.stdin.close()
            await proc.stdin.wait_closed()
            stdout, stderr = await asyncio.gather(*tasks)
            await proc.wait()
    except BaseException as error:
        original = error
    finally:
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except BaseException as error:
                if original is None:
                    original = error
            try:
                if proc.returncode is None:
                    os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as error:
                if original is None:
                    original = error
            try:
                async with asyncio.timeout_at(min(case.work_end, end + 2)):
                    await proc.wait()
            except BaseException as error:
                case.retained = True
                if original is None:
                    original = error
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            try:
                async with asyncio.timeout_at(min(case.work_end, end + 2)):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    check(proc is not None and proc.returncode is not None and not stderr, "Result child completion")
    require_source_observer(case)
    return proc.returncode, stdout


class ResultCohort(WorkflowCohort):
    def __init__(self, engine, fixture):
        seed = fixture["seed"]
        super().__init__(
            engine,
            {
                "case_id": fixture["case_id"],
                "arrangement": {
                    "status": seed["status"],
                    "attempt": seed["attempt"],
                    "tracking": seed["tracking"],
                },
            },
        )
        self.fixture = fixture
        self.sessions = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        self.sql_labels = []

    async def seed(self):
        await super().seed()
        async with self.sessions() as db:
            for role in ("execution", "foreign"):
                row = await db.get(Execution, self.ids[role])
                check(row is not None, "Result seed row")
                row.created_at = SEED_TIME
                row.started_at = SEED_TIME
                row.completed_at = SEED_TIME
                row.result = {"old": 1}
                row.result_type = "json"
                row.error_message = "old-error"
                row.variables = {"old": 1}
                row.duration_ms = 19
                row.time_saved = 19
                row.value = 12.34
                row.peak_memory_bytes = 41
                row.process_rss_bytes = 43
                row.cpu_user_seconds = 1.25
                row.cpu_system_seconds = 2.25
                row.cpu_total_seconds = 3.25
            attempts = (
                await db.scalars(
                    select(WorkflowExecutionAttempt).where(
                        WorkflowExecutionAttempt.execution_id.in_([self.ids["execution"], self.ids["foreign"]])
                    )
                )
            ).all()
            for attempt in attempts:
                attempt.created_at = SEED_TIME
                attempt.published_at = SEED_TIME if attempt.published_at is not None else None
                attempt.claimed_at = SEED_TIME if attempt.claimed_at is not None else None
                attempt.started_at = SEED_TIME if attempt.started_at is not None else None
                attempt.completed_at = SEED_TIME if attempt.completed_at is not None else None
                attempt.heartbeat_at = SEED_TIME
                attempt.worker_incarnation_id = self.ids["org"]
                attempt.duration_ms = 23
                attempt.peak_memory_bytes = 31
                attempt.cpu_total_seconds = 3.25
                if attempt.execution_id == self.ids["execution"]:
                    attempt.phase = self.fixture["seed"]["phase"]
            await db.commit()
        context = self.fixture["seed"]["context"]
        values = {
            "old": '{"old":1}',
            "sql-null": None,
            "json-null": "null",
            "empty": "{}",
            "server-present": '{"old":1,"teams_action_completion":{"server":1}}',
            "server-null": '{"old":1,"teams_action_completion":null}',
        }
        check(context in values, "Result context seed directive")
        async with self.sessions() as db:
            await db.execute(
                text("UPDATE executions SET execution_context = CAST(:value AS jsonb) WHERE id = :id"),
                {"value": values[context], "id": self.ids["execution"]},
            )
            await db.commit()
        await self.topic_isolation()
        await self.empty_buffers()

    async def empty_buffers(self):
        for role in ("execution", "foreign"):
            identity = str(self.ids[role])
            check(await self.redis.hlen(pending_changes_key(identity)) == 0, "Result sync buffer not empty")
            check(await self.redis.xlen(execution_logs_stream_key(identity)) == 0, "Result log buffer not empty")

    async def snapshot(self):
        from src.models.orm.executions import ExecutionLog

        snapshot = {}
        async with self.sessions() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            for model, name in (
                (Execution, "executions"),
                (WorkflowExecutionAttempt, "attempts"),
                (ExecutionLog, "logs"),
            ):
                predicate = (
                    model.id.in_([self.ids["execution"], self.ids["foreign"]])
                    if model is Execution
                    else model.execution_id.in_([self.ids["execution"], self.ids["foreign"]])
                )
                rows = (await db.scalars(select(model).where(predicate).order_by(model.id))).all()
                snapshot[name] = [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns} for row in rows
                ]
            rows = (
                (
                    await db.execute(
                        text(
                            "SELECT id, execution_context IS NULL AS sql_null, execution_context::text AS json_text "
                            "FROM executions WHERE id = :owned OR id = :foreign ORDER BY id"
                        ),
                        {"owned": self.ids["execution"], "foreign": self.ids["foreign"]},
                    )
                )
                .mappings()
                .all()
            )
            snapshot["context_storage"] = {
                row["id"]: {"sql_null": row["sql_null"], "json_text": row["json_text"]} for row in rows
            }
            await db.rollback()
        return snapshot

    async def close(self, end):
        from src.models.orm.events import Event
        from src.models.orm.executions import ExecutionLog
        from src.models.orm.organizations import Organization
        from src.models.orm.users import User

        original = None
        db = None
        try:
            async with asyncio.timeout_at(end):
                for role in ("execution", "foreign"):
                    await self.redis.delete(active_execution_key(str(self.ids[role])))
                db = self.sessions()
                ids = [self.ids["execution"], self.ids["foreign"]]
                await db.execute(delete(ExecutionLog).where(ExecutionLog.execution_id.in_(ids)))
                await db.execute(delete(Execution).where(Execution.id.in_(ids)))
                await db.execute(delete(Event).where(Event.organization_id == self.ids["org"]))
                await db.execute(delete(User).where(User.id == self.ids["user"]))
                await db.execute(delete(Organization).where(Organization.id == self.ids["org"]))
                await db.commit()
        except BaseException as error:
            original = error
        # Each owned close is independently attempted under the SAME deadline;
        # a suppressed cancellation cannot turn expired cleanup into new time.
        for handle in (db, self.pubsub, self.redis):
            if handle is None:
                continue
            try:
                async with asyncio.timeout_at(end):
                    if handle is db:
                        await handle.close()
                    else:
                        await handle.aclose()
            except BaseException as error:
                if original is None:
                    original = error
        if original is not None:
            raise original

    def token(self):
        selector = self.fixture["seed"]["token"]
        return None if selector == "missing" else self.ids[selector + "_token"]


@contextmanager
def repository_observer(identity: str):
    original = consumer_module.update_execution
    calls = []

    async def forward(*args, **kwargs):
        if kwargs.get("execution_id") == identity:
            calls.append({name: deepcopy(value) for name, value in kwargs.items() if name != "session"})
        actor = _CLOCK_ACTOR.get()
        if kwargs.get("execution_id") != identity or actor is None or not actor.collect_roles:
            return await original(*args, **kwargs)
        actor.logical_selected = kwargs.get("duration_ms") is not None
        with clock_context(_CLOCK_REPOSITORY, actor):
            return await original(*args, **kwargs)

    consumer_module.update_execution = forward
    pending = None
    try:
        yield calls
    except BaseException as error:
        pending = error
    finally:
        try:
            check(consumer_module.update_execution is forward, "Result repository observer replaced")
            consumer_module.update_execution = original
        except BaseException as error:
            if pending is None:
                pending = error
    if pending is not None:
        raise pending


@asynccontextmanager
async def actual_consumer():
    """No start/stop/loader override; retain exact incumbent constructor."""
    previous_pool = pool_module._pool
    previous_redis = redis_client._redis_client
    pool = None
    consumer = None
    previous_callback = None
    original = None
    try:
        pool = pool_module.get_process_pool()
        previous_callback = pool.on_result
        dormant(pool)
        consumer = consumer_module.WorkflowExecutionConsumer()
        unstarted_consumer(consumer, pool)
        yield consumer
    except BaseException as error:
        original = error
    finally:
        actions = []
        if pool is not None:

            def restore_pool():
                dormant(pool)
                if consumer is not None:
                    unstarted_consumer(consumer, pool)
                    check(pool.on_result == consumer._handle_result, "Result callback replaced")
                check(pool_module._pool is pool, "Result pool replaced")
                pool.on_result = previous_callback
                if previous_pool is None:
                    pool_module._pool = None

            actions.append(restore_pool)
        for action in actions:
            try:
                action()
            except BaseException as error:
                if original is None:
                    original = error
        try:
            if previous_redis is None and redis_client._redis_client is not None:
                await redis_client.close_redis_client()
            elif previous_redis is not None:
                check(redis_client._redis_client is previous_redis, "Result Redis singleton replaced")
        except BaseException as error:
            if original is None:
                original = error
    if original is not None:
        raise original


@contextmanager
def clock_context(variable, value):
    check(variable is _CLOCK_ACTOR or variable is _CLOCK_REPOSITORY, "Result clock context role")
    token = variable.set(value)
    pending = None
    try:
        yield
    except BaseException as error:
        pending = error
    finally:
        try:
            variable.reset(token)
        except BaseException as error:
            if pending is None:
                pending = error
    if pending is not None:
        raise pending


def utc_microseconds(value):
    check(isinstance(value, datetime) and value.tzinfo is not None, "Result clock datetime")
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    result = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    check(0 <= result <= _CLOCK_LIMIT, "Result clock UTC bound")
    return result


class SourceClockObserver:
    """Private actual actor/connection association; no SQL text or binds retained."""

    def __init__(self):
        self.collect_roles = True
        self.connection = None
        self.transaction = None
        self.attempt = {}
        self.logical = {}
        self.logical_selected = None
        self.commit_dispatch = None
        self.commit_returned = False

    def mark(self, slot, name):
        check(name not in slot, "Result duplicate source clock role")
        slot[name] = utc_microseconds(datetime.now(UTC))

    def observe(self, connection, context, before):
        if not self.collect_roles or _CLOCK_ACTOR.get() is not self:
            return
        compiled = context.compiled
        if compiled is None:
            return
        statement = compiled.statement
        repository = _CLOCK_REPOSITORY.get() is self
        is_select = getattr(statement, "is_select", False)
        is_update = getattr(statement, "is_update", False)
        table = getattr(getattr(statement, "table", None), "name", None)
        columns = set()
        where_columns = set()
        locking = False
        if is_select:
            columns = {
                (getattr(getattr(column, "table", None), "name", None), getattr(column, "name", None))
                for column in statement.selected_columns
            }
            for criterion in statement._where_criteria:
                where_columns.update(
                    (getattr(getattr(node, "table", None), "name", None), getattr(node, "name", None))
                    for node in visitors.iterate(criterion)
                    if getattr(node, "table", None) is not None
                )
            locking = statement._for_update_arg is not None
        if not repository and is_select and locking and ("executions", "id") in columns:
            check(where_columns == {("executions", "id")}, "Result owned execution query association")
            if not before:
                check(self.connection is None, "Result duplicate owned connection")
                self.connection = connection
                self.transaction = connection.get_transaction()
                check(self.transaction is not None, "Result owned transaction absent")
            return
        attempt_read = (
            not repository
            and is_select
            and locking
            and ("workflow_execution_attempts", "id") in columns
            and where_columns
            == {
                ("workflow_execution_attempts", "claim_token"),
                ("workflow_execution_attempts", "execution_id"),
                ("workflow_execution_attempts", "completed_at"),
            }
        )
        attempt_write = is_update and table == "workflow_execution_attempts" and not repository
        status_read = repository and is_select and columns == {("executions", "status")}
        context_read = repository and is_select and columns == {("executions", "execution_context")}
        logical_write = repository and is_update and table == "executions"
        if not any((attempt_read, attempt_write, status_read, context_read, logical_write)):
            return
        check(
            connection is self.connection
            and connection.get_transaction() is self.transaction
            and self.transaction is not None,
            "Result source transaction association",
        )
        if status_read or context_read:
            check(not locking and where_columns == {("executions", "id")}, "Result repository query association")
        if attempt_read and not before:
            self.mark(self.attempt, "read_ack")
        elif attempt_write:
            self.mark(self.attempt, "upper" if before else "write_ack")
        elif status_read and not before:
            self.mark(self.logical, "read_ack")
        elif context_read and before:
            self.mark(self.logical, "upper")
            self.logical["upper_kind"] = "context_read_dispatch"
        elif logical_write:
            if before and "upper" not in self.logical:
                self.mark(self.logical, "upper")
                self.logical["upper_kind"] = "write_execution_dispatch"
            elif not before:
                self.mark(self.logical, "write_ack")


@contextmanager
def sql_observer(engine, labels, clock):
    def before(connection, _cursor, _sql, _parameters, context, _many):
        clock.observe(connection, context, True)

    def after(connection, _cursor, _sql, _parameters, context, _many):
        clock.observe(connection, context, False)
        if _CLOCK_ACTOR.get() is not clock or context.compiled is None:
            return
        statement = context.compiled.statement
        if getattr(statement, "is_update", False):
            table = getattr(getattr(statement, "table", None), "name", None)
            if table in {"executions", "workflow_execution_attempts"}:
                check(len(labels) < 8, "Result SQL label bound")
                labels.append(table)

    def commit(connection):
        if _CLOCK_ACTOR.get() is clock and connection is clock.connection:
            check(connection.get_transaction() is clock.transaction, "Result source commit transaction")
            check(clock.commit_dispatch is None, "Result duplicate source commit")
            clock.commit_dispatch = utc_microseconds(datetime.now(UTC))

    owned = []
    pending = None
    try:
        for name, callback in (("before_cursor_execute", before), ("after_cursor_execute", after), ("commit", commit)):
            owned.append((name, callback))
            event.listen(engine.sync_engine, name, callback)
        yield
    except BaseException as error:
        pending = error
    finally:
        for name, callback in reversed(owned):
            try:
                check(event.contains(engine.sync_engine, name, callback), "Result SQL observer replaced")
                event.remove(engine.sync_engine, name, callback)
            except BaseException as error:
                if pending is None:
                    pending = error
    if pending is not None:
        raise pending


def sql_error(error: DBAPIError):
    actual = error.orig
    code = getattr(actual, "sqlstate", None)
    cause = getattr(actual, "__cause__", None)
    if code is None and cause is not None:
        code = getattr(cause, "sqlstate", None)
    constraint = getattr(actual, "constraint_name", None)
    if constraint is None and cause is not None:
        constraint = getattr(cause, "constraint_name", None)
    return code if code in ALLOW_CODES else None, constraint


def clock_roles_selected(payload):
    # Exact incumbent success/failure branch: result.get("attempt_token") truthiness.
    # Legacy forwarding/SQL labels stay genuine, without fenced-role admission.
    return bool(payload.get("attempt_token"))


async def python_result(cohort, fields, case, *, source_width=False):
    require_source_observer(case)
    payload = materialize(fields, cohort.fixture["lane"])
    payload.update(sync=False, execution_id=str(cohort.ids["execution"]))
    token = cohort.token()
    if token is not None:
        payload["attempt_token"] = str(token)
    reference = "returned"
    source_code = None
    calls = []
    clock = SourceClockObserver()
    clock.collect_roles = clock_roles_selected(payload)
    async with asyncio.timeout_at(min(case.work_end, asyncio.get_running_loop().time() + 30)):
        async with actual_consumer() as consumer:
            with repository_observer(str(cohort.ids["execution"])) as calls:
                with sql_observer(database.get_engine(), cohort.sql_labels, clock):
                    try:
                        method = (
                            consumer._process_success
                            if cohort.fixture["lane"] == "success"
                            else consumer._process_failure
                        )
                        with clock_context(_CLOCK_ACTOR, clock):
                            await method(str(cohort.ids["execution"]), payload)
                            clock.commit_returned = clock.commit_dispatch is not None
                    except RuntimeError as error:
                        if str(error) != MISSING_FENCE:
                            raise
                        reference = "missing_fence"
                    except DBAPIError as error:
                        code, _ = sql_error(error)
                        source_code = code
                        check(source_width or code == "22003", "Result unexpected reference SQL failure")
                        reference = "source_width_failure" if source_width else "numeric_range"
    require_source_observer(case)
    await cohort.empty_buffers()
    events = await cohort.events()
    case.request.node.user_properties.append(("result_source_sqlstate", source_code or "none"))
    return reference, calls, events, clock


def request_bytes(cohort, fields_json):
    fields = decode(fields_json.encode())
    projection = prepare_json_inputs(fields, cohort.fixture["lane"])
    # Preserve literal raw_fields bytes; only the freshly owned envelope is encoded.
    prefix = {
        "schema": "bifrost.test.workflow-sql/v1",
        "case_id": cohort.fixture["case_id"],
        "cohort": {
            "execution_id": str(cohort.ids["execution"]),
            "submitted_token": str(cohort.token()) if cohort.token() is not None else None,
        },
        "projection": projection,
    }
    encoded = json.dumps(prefix, allow_nan=False, ensure_ascii=True, separators=(",", ":")).encode()
    operation = (
        b',"operation":{"kind":"result","lane":'
        + json.dumps(cohort.fixture["lane"]).encode()
        + b',"raw_fields":'
        + fields_json.encode()
        + b"}}"
    )
    result = encoded[:-1] + operation
    check(len(result) <= 65536, "Result request bound")
    return result


def response(raw, case_id):
    value = decode(raw)
    closed(value, {"schema", "case_id", "decision", "transaction", "clock_witness"})
    check(
        value["schema"] == "bifrost.test.workflow-sql-result/v2" and value["case_id"] == case_id,
        "Result response identity",
    )
    decision = value["decision"]
    check(
        type(decision) is dict and decision.get("kind") in {"applied", "rejected", "infrastructure_failure"},
        "Result decision",
    )
    if decision["kind"] == "applied":
        closed(decision, {"kind", "plan"})
        closed(decision["plan"], {"execution", "attempt"})
        closed(decision["plan"]["execution"], EXEC_FIELDS)
        closed(decision["plan"]["attempt"], ATTEMPT_FIELDS)
    elif decision["kind"] == "rejected":
        closed(decision, {"kind", "reason"})
        check(decision["reason"] in REASONS, "Result domain rejection")
    else:
        closed(decision, {"kind", "stage", "class", "sqlstate"})
        check(
            decision["stage"] in SQL_STAGES | SETTLEMENT and decision["class"] in CLASSES, "Result failure vocabulary"
        )
        check(not (decision["stage"] in SQL_STAGES and decision["class"] == "Resource"), "Result invalid failure pair")
        check(decision["sqlstate"] is None or decision["sqlstate"] in ALLOW_CODES, "Result SQLSTATE allowlist")
    tx = value["transaction"]
    closed(tx, {"status", "affected_execution_rows", "affected_attempt_rows"})
    check(tx["status"] in {"committed", "rolled_back", "unknown"}, "Result transaction status")
    for key in ("affected_execution_rows", "affected_attempt_rows"):
        check(tx[key] is None or (type(tx[key]) is int and 0 <= tx[key] < 2**64), "Result affected row count")
    check(decision["kind"] != "applied" or tx["status"] == "committed", "Result applied commit admission")
    check(
        tx["status"] != "committed" or decision["kind"] in {"applied", "infrastructure_failure"},
        "Result committed disposition",
    )
    validate_clock_witness(value["clock_witness"], applied=decision["kind"] == "applied")
    if decision["kind"] == "rejected":
        check(
            value["clock_witness"] == {"complete": True, "attempt": None, "logical": None, "commit": None},
            "Result rejected clock roles",
        )
    return value


def validate_clock_witness(value, *, applied=False):
    closed(value, {"complete", "attempt", "logical", "commit"})
    check(type(value["complete"]) is bool, "Result clock complete type")
    events = []

    def role(name, fields, first):
        slot = value[name]
        if slot is None:
            return
        closed(slot, set(fields))
        for offset, key in enumerate(fields):
            if key == "upper_kind":
                continue
            item = slot[key]
            if item is None:
                continue
            closed(item, {"ordinal", "utc_us", "elapsed_ns"})
            expected = first + offset
            if name == "logical" and key == "write_ack":
                expected -= 1
            check(type(item["ordinal"]) is int and item["ordinal"] == expected, "Result clock role ordinal")
            for field in ("utc_us", "elapsed_ns"):
                check(type(item[field]) is int and 0 <= item[field] <= _CLOCK_LIMIT, "Result clock event bound")
            events.append(item)

    role("attempt", ("read_ack", "sample", "write_dispatch", "write_ack"), 1)
    role("logical", ("status_read_ack", "sample", "upper", "upper_kind", "write_ack"), 5)
    role("commit", ("dispatch", "ack"), 9 if value["logical"] is not None else 5)
    if value["logical"] is not None:
        check(
            type(value["logical"]["upper_kind"]) is str
            and value["logical"]["upper_kind"] in {"context_read_dispatch", "write_execution_dispatch"},
            "Result clock upper kind",
        )
    for previous, current in pairwise(events):
        check(
            previous["ordinal"] < current["ordinal"]
            and previous["elapsed_ns"] <= current["elapsed_ns"]
            and previous["utc_us"] <= current["utc_us"],
            "Result clock event order",
        )
    if applied:
        check(
            value["complete"] and value["attempt"] is not None and value["commit"] is not None,
            "Result clock incomplete",
        )
        for name in ("attempt", "logical", "commit"):
            slot = value[name]
            if slot is not None:
                check(all(item is not None for item in slot.values()), "Result clock incomplete")
    return value


def source_clock_rows(clock, attempt, execution, before):
    check(clock.commit_returned and clock.commit_dispatch is not None, "Result source commit acknowledgment")
    check(set(clock.attempt) == {"read_ack", "upper", "write_ack"}, "Result source attempt roles")
    sample = utc_microseconds(attempt["completed_at"])
    check(attempt["heartbeat_at"] == attempt["completed_at"], "Result attempt clock equality")
    check(
        clock.attempt["read_ack"] <= sample <= clock.attempt["upper"] <= clock.attempt["write_ack"],
        "Result source attempt interval",
    )
    check(type(clock.logical_selected) is bool, "Result source logical selection")
    check(set(clock.logical) == {"read_ack", "upper", "upper_kind", "write_ack"}, "Result source logical roles")
    check(
        clock.attempt["write_ack"]
        <= clock.logical["read_ack"]
        <= clock.logical["upper"]
        <= clock.logical["write_ack"]
        <= clock.commit_dispatch,
        "Result source role order",
    )
    if clock.logical_selected:
        logical = utc_microseconds(execution["completed_at"])
        check(clock.logical["read_ack"] <= logical <= clock.logical["upper"], "Result source logical interval")
    else:
        check(execution["completed_at"] == before["completed_at"], "Result logical clock Keep")


def native_clock_rows(witness, attempt, execution, before, logical_selected):
    validate_clock_witness(witness, applied=True)
    selected = witness["logical"] is not None
    check(type(logical_selected) is bool and selected == logical_selected, "Result actual logical role selection")
    sample = witness["attempt"]["sample"]["utc_us"]
    check(
        utc_microseconds(attempt["completed_at"]) == sample and utc_microseconds(attempt["heartbeat_at"]) == sample,
        "Result native attempt sample association",
    )
    if selected:
        check(
            utc_microseconds(execution["completed_at"]) == witness["logical"]["sample"]["utc_us"],
            "Result native logical sample association",
        )
    else:
        check(execution["completed_at"] == before["completed_at"], "Result logical clock Keep")


def clock_comparison_controls():
    # Synthetic bounded role/row views exercise the SAME actual admission helpers.
    base = 1700000000000000

    def event_value(ordinal):
        return {"ordinal": ordinal, "utc_us": base + ordinal, "elapsed_ns": ordinal}

    witness = {
        "complete": True,
        "attempt": {
            key: event_value(i) for i, key in enumerate(("read_ack", "sample", "write_dispatch", "write_ack"), 1)
        },
        "logical": {
            **{key: event_value(i) for i, key in enumerate(("status_read_ack", "sample", "upper", "write_ack"), 5)},
            "upper_kind": "context_read_dispatch",
        },
        "commit": {"dispatch": event_value(9), "ack": event_value(10)},
    }

    def stamp(offset):
        from datetime import timedelta

        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=base + offset)

    attempt = {"completed_at": stamp(2), "heartbeat_at": stamp(2)}
    execution = {"completed_at": stamp(6)}
    before = {"completed_at": stamp(0)}
    source = SourceClockObserver()
    source.attempt = {"read_ack": base + 1, "upper": base + 3, "write_ack": base + 4}
    source.logical = {
        "read_ack": base + 5,
        "upper": base + 7,
        "write_ack": base + 8,
        "upper_kind": "context_read_dispatch",
    }
    source.logical_selected = source.commit_returned = True
    source.commit_dispatch = base + 9
    source_clock_rows(source, attempt, execution, before)
    native_clock_rows(witness, attempt, execution, before, True)
    keep = deepcopy(witness)
    keep["logical"] = None
    keep["commit"] = {"dispatch": event_value(5), "ack": event_value(6)}
    native_clock_rows(keep, attempt, before, before, False)
    source.logical_selected = False
    source_clock_rows(source, attempt, before, before)
    source.logical_selected = True

    def rejects(callback, label):
        try:
            callback()
        except AssertionError as error:
            check(str(error) == label, "Result clock control wrong rejection")
        else:
            raise AssertionError("Result clock drift admitted")

    # Same scoping helper uses actual payload truthiness, not case/expected outcome.
    check(clock_roles_selected({"attempt_token": "owned-fence"}), "Result fenced clock scope")
    for payload in ({}, {"attempt_token": None}, {"attempt_token": ""}):
        check(not clock_roles_selected(payload), "Result unfenced clock scope")
    inactive = SourceClockObserver()
    inactive.collect_roles = clock_roles_selected({})
    with clock_context(_CLOCK_ACTOR, inactive):
        inactive.observe(None, None, True)
    check(
        inactive.connection is None and not inactive.attempt and not inactive.logical,
        "Result unfenced roles collected",
    )

    # Confined before/after snapshots exercise actual foreign preservation, not
    # ledger ordinal errors. Empty attempts prevent irrelevant sample admission.
    class SnapshotCohort:
        def __init__(self):
            self.ids = {
                "execution": UUID("00000000-0000-0000-0000-000000000001"),
                "foreign": UUID("00000000-0000-0000-0000-000000000002"),
                "attempt": UUID("00000000-0000-0000-0000-000000000003"),
            }

    cohort = SnapshotCohort()
    snapshot = {
        "executions": [
            {"id": cohort.ids[role], "completed_at": stamp(0), "result": {"sentinel": 1}}
            for role in ("execution", "foreign")
        ],
        "attempts": [],
        "logs": [],
        "context_storage": {},
    }
    compare_rows(snapshot, snapshot, snapshot, snapshot, cohort, cohort, source, witness)
    foreign_clock = deepcopy(snapshot)
    foreign_clock["executions"][1]["completed_at"] = stamp(1)
    rejects(
        lambda: compare_rows(foreign_clock, foreign_clock, snapshot, snapshot, cohort, cohort, source, witness),
        "Result foreign clock changed",
    )
    foreign_sentinel = deepcopy(snapshot)
    foreign_sentinel["executions"][1]["result"] = {"sentinel": 2}
    rejects(
        lambda: compare_rows(foreign_sentinel, foreign_sentinel, snapshot, snapshot, cohort, cohort, source, witness),
        "Result collateral or unlisted field changed",
    )

    swapped = deepcopy(witness)
    # Equal UTC values cannot rescue exchanged semantic ordinal roles.
    for slot in (swapped["attempt"], swapped["logical"], swapped["commit"]):
        for item in slot.values():
            if type(item) is dict:
                item["utc_us"] = base
    swapped["attempt"]["sample"], swapped["logical"]["sample"] = (
        swapped["logical"]["sample"],
        swapped["attempt"]["sample"],
    )
    rejects(lambda: validate_clock_witness(swapped, applied=True), "Result clock role ordinal")
    role_objects = {**witness, "attempt": witness["logical"], "logical": witness["attempt"]}
    rejects(lambda: validate_clock_witness(role_objects, applied=True), "closed Result schema")
    early = deepcopy(witness)
    early["logical"]["sample"]["ordinal"] = 4
    rejects(lambda: validate_clock_witness(early, applied=True), "Result clock role ordinal")
    rejects(
        lambda: native_clock_rows(
            witness, {**attempt, "completed_at": stamp(6), "heartbeat_at": stamp(6)}, execution, before, True
        ),
        "Result native attempt sample association",
    )
    rejects(
        lambda: native_clock_rows(witness, {**attempt, "heartbeat_at": stamp(3)}, execution, before, True),
        "Result native attempt sample association",
    )
    rejects(lambda: native_clock_rows(keep, attempt, execution, before, False), "Result logical clock Keep")
    rejects(
        lambda: native_clock_rows(witness, attempt, {"completed_at": stamp(2)}, before, True),
        "Result native logical sample association",
    )
    rejects(
        lambda: source_clock_rows(source, {**attempt, "heartbeat_at": stamp(3)}, execution, before),
        "Result attempt clock equality",
    )
    foreign = deepcopy(witness)
    foreign["commit"]["ack"]["ordinal"] = 11
    rejects(lambda: validate_clock_witness(foreign, applied=True), "Result clock role ordinal")
    for bad, label in (
        ({**witness, "extra": None}, "closed Result schema"),
        ({key: value for key, value in witness.items() if key != "attempt"}, "closed Result schema"),
        ({**witness, "attempt": None}, "Result clock incomplete"),
        ({**witness, "complete": False}, "Result clock incomplete"),
    ):
        rejects(lambda bad=bad: validate_clock_witness(bad, applied=True), label)
    rejects(
        lambda: source_clock_rows(source, attempt, {"completed_at": stamp(4)}, before), "Result source logical interval"
    )
    pending = KeyboardInterrupt()
    try:
        with clock_context(_CLOCK_ACTOR, source):
            raise pending
    except BaseException as caught:
        check(caught is pending, "Result clock control original identity")
    else:
        raise AssertionError("Result clock control swallowed original")
    bad_bound = deepcopy(witness)
    bad_bound["attempt"]["sample"]["utc_us"] = _CLOCK_LIMIT + 1
    rejects(lambda: validate_clock_witness(bad_bound, applied=True), "Result clock event bound")
    bad_type = deepcopy(witness)
    bad_type["attempt"]["sample"]["ordinal"] = True
    rejects(lambda: validate_clock_witness(bad_type, applied=True), "Result clock role ordinal")
    bad_upper = deepcopy(witness)
    bad_upper["logical"]["upper_kind"] = "unknown"
    rejects(lambda: validate_clock_witness(bad_upper, applied=True), "Result clock upper kind")
    bad_null = deepcopy(witness)
    bad_null["attempt"]["sample"] = None
    rejects(lambda: validate_clock_witness(bad_null, applied=True), "Result clock incomplete")
    bad_elapsed = deepcopy(witness)
    bad_elapsed["attempt"]["sample"]["elapsed_ns"] = 0
    rejects(lambda: validate_clock_witness(bad_elapsed, applied=True), "Result clock event order")
    rejects(lambda: decode(b'{"complete":true,"complete":false}'), "duplicate Result key")
    rejects(
        lambda: response(
            json.dumps(
                {
                    "schema": "bifrost.test.workflow-sql-result/v1",
                    "case_id": "synthetic",
                    "decision": {},
                    "transaction": {},
                    "clock_witness": witness,
                }
            ).encode(),
            "synthetic",
        ),
        "Result response identity",
    )


def mapped(value, cohort):
    if isinstance(value, UUID):
        names = [name for name, identity in cohort.ids.items() if value == identity]
        check(len(names) == 1, "Result undeclared UUID normalization")
        return names[0]
    if isinstance(value, ExecutionStatus):
        return value.value
    if isinstance(value, float):
        return ("float_bits", struct.pack(">d", value).hex())
    if isinstance(value, list):
        return [mapped(v, cohort) for v in value]
    if isinstance(value, dict):
        return {mapped(k, cohort): mapped(v, cohort) for k, v in value.items()}
    return value


def compare_rows(left, right, left_before, right_before, left_cohort, right_cohort, left_clock, right_clock):
    clocks = {"executions": {"completed_at"}, "attempts": {"completed_at", "heartbeat_at"}}
    for table in ("executions", "attempts", "logs"):
        check(len(left[table]) == len(right[table]), "Result committed row cardinality")

        def keyed(snapshot, cohort, selected_table=table):
            return {mapped(row["id"], cohort): row for row in snapshot[selected_table]}

        la, ra = keyed(left, left_cohort), keyed(right, right_cohort)
        lb, rb = keyed(left_before, left_cohort), keyed(right_before, right_cohort)
        check(set(la) == set(ra) == set(lb) == set(rb), "Result committed identities")
        for identity in la:
            check(set(la[identity]) == set(ra[identity]), "Result column roster")
            for key in la[identity]:
                x, y = la[identity][key], ra[identity][key]
                xold, yold = lb[identity][key], rb[identity][key]
                if key in clocks.get(table, set()) and (x != xold or y != yold):
                    check(x != xold and y != yold, "Result clock Keep mismatch")
                    check(identity not in {"foreign", "foreign_attempt"}, "Result foreign clock changed")
                else:
                    check(mapped(x, left_cohort) == mapped(y, right_cohort), "Result committed field mismatch")
                allowed = (
                    {
                        "status",
                        "duration_ms",
                        "completed_at",
                        "result",
                        "result_type",
                        "error_message",
                        "time_saved",
                        "value",
                        "variables",
                        "execution_context",
                        *METRICS,
                    }
                    if table == "executions"
                    else ATTEMPT_FIELDS - {"started_at"}
                    if table == "attempts"
                    else set()
                )
                if identity in {"foreign", "foreign_attempt"} or table == "logs" or key not in allowed:
                    check(x == xold and y == yold, "Result collateral or unlisted field changed")
    left_execution = next(row for row in left["executions"] if row["id"] == left_cohort.ids["execution"])
    right_execution = next(row for row in right["executions"] if row["id"] == right_cohort.ids["execution"])
    left_attempt = next((row for row in left["attempts"] if row["id"] == left_cohort.ids["attempt"]), None)
    right_attempt = next((row for row in right["attempts"] if row["id"] == right_cohort.ids["attempt"]), None)
    if (
        left_attempt is not None
        and right_attempt is not None
        and left_attempt["completed_at"]
        != next(row["completed_at"] for row in left_before["attempts"] if row["id"] == left_cohort.ids["attempt"])
    ):
        left_old = next(row for row in left_before["executions"] if row["id"] == left_cohort.ids["execution"])
        right_old = next(row for row in right_before["executions"] if row["id"] == right_cohort.ids["execution"])
        source_clock_rows(left_clock, left_attempt, left_execution, left_old)
        native_clock_rows(right_clock, right_attempt, right_execution, right_old, left_clock.logical_selected)
        if left_clock.logical_selected:
            check(
                right_clock["logical"]["upper_kind"] == left_clock.logical["upper_kind"],
                "Result actual logical upper branch",
            )
    check(
        mapped(left["context_storage"], left_cohort) == mapped(right["context_storage"], right_cohort),
        "Result SQLNULL JSONnull distinction",
    )
    for snapshot, cohort in ((left, left_cohort), (right, right_cohort)):
        for row in snapshot["attempts"]:
            if (
                row["id"] == cohort.ids["attempt"]
                and row["completed_at"] != SEED_TIME
                and row["completed_at"] is not None
            ):
                check(row["heartbeat_at"] == row["completed_at"], "Result attempt clock equality")
                owned = next(r for r in snapshot["executions"] if r["id"] == cohort.ids["execution"])
                if owned["completed_at"] != SEED_TIME:
                    check(row["completed_at"] <= owned["completed_at"], "Result source clock ordering")


async def paired_result(engine, fixture, case):
    require_source_observer(case)
    dsn = driver_dsn(engine)
    case.source_observer.session.observe_dsn_return(case.source_observer, dsn)
    source_admission(case.source_observer)
    cohorts = []
    for _ in range(2):
        cohort = ResultCohort(engine, fixture)
        case.cohorts.append(cohort)
        cohorts.append(cohort)
        await cohort.seed()
    python, rust = cohorts
    before_python, before_rust = await python.snapshot(), await rust.snapshot()
    check(
        {mapped(r["id"], python): mapped(r, python) for r in before_python["executions"]}
        == {mapped(r["id"], rust): mapped(r, rust) for r in before_rust["executions"]},
        "Result seed execution mismatch",
    )
    check(
        {mapped(r["id"], python): mapped(r, python) for r in before_python["attempts"]}
        == {mapped(r["id"], rust): mapped(r, rust) for r in before_rust["attempts"]},
        "Result seed attempt mismatch",
    )
    fields = decode(fixture["raw_fields_json"].encode())
    first_error = None
    reference = None
    calls = []
    observed_events = []
    clock_python = None
    result = None
    try:
        reference, calls, observed_events, clock_python = await python_result(python, fields, case)
    except BaseException as error:
        first_error = error
    if first_error is None:
        try:
            code, raw = await invoke(
                case, request_bytes(rust, fixture["raw_fields_json"]), "apply-result", database_url=dsn
            )
            check(code in {0, 1}, "Result native apply exit")
            result = response(raw, fixture["case_id"])
            check(
                (code == 0) == (result["decision"]["kind"] != "infrastructure_failure"),
                "Result native exit disposition",
            )
        except BaseException as error:
            first_error = error
    # A failed pipe/unknown reply never establishes rollback. Read actual rows
    # independently after native actor settlement even on malformed output.
    if any(process.returncode is None for process in case.processes):
        case.retain_dependencies = True
        if first_error is not None:
            raise first_error
        raise AssertionError("Result actor settlement unavailable")
    after_python = after_rust = None
    try:
        async with asyncio.timeout_at(case.work_end):
            after_python, after_rust = await python.snapshot(), await rust.snapshot()
        case.request.node.user_properties.append(("result_independent_readback", "complete"))
        if result is not None:
            case.request.node.user_properties.append(("result_measured_transaction", result["transaction"]["status"]))
    except BaseException as error:
        case.retain_dependencies = True
        if first_error is None:
            first_error = error
    if first_error is not None:
        raise first_error
    check(result is not None and result["transaction"]["status"] != "unknown", "Result commit outcome unknown")
    check(
        after_python is not None and after_rust is not None and clock_python is not None,
        "Result readback admission",
    )
    await rust.empty_buffers()
    compare_rows(
        after_python, after_rust, before_python, before_rust, python, rust, clock_python, result["clock_witness"]
    )
    changed = (
        after_python["executions"] != before_python["executions"]
        or after_python["attempts"] != before_python["attempts"]
    )
    if result["decision"]["kind"] == "rejected":
        check(result["transaction"]["status"] == "rolled_back", "Result rejected rollback acknowledgment")
    if reference == "numeric_range":
        check(result["transaction"]["status"] == "rolled_back", "Result numeric rollback acknowledgment")
        check(
            result["decision"]["kind"] == "infrastructure_failure"
            and result["decision"]["class"] == "Database"
            and result["decision"]["sqlstate"] == "22003",
            "Result actual numeric error parity",
        )
        check(
            after_python["executions"] == before_python["executions"]
            and after_python["attempts"] == before_python["attempts"],
            "Result reference overflow rollback",
        )
        check(
            after_rust["executions"] == before_rust["executions"] and after_rust["attempts"] == before_rust["attempts"],
            "Result native overflow rollback",
        )
    elif reference == "missing_fence":
        check(result["decision"] == {"kind": "rejected", "reason": "MissingFence"}, "Result missing fence precedence")
    else:
        check((result["decision"]["kind"] == "applied") == changed, "Result actual write eligibility")
    if changed:
        check(
            result["transaction"]["affected_execution_rows"] == 1
            and result["transaction"]["affected_attempt_rows"] == 1,
            "Result actual affected rows",
        )
        check(python.sql_labels[:2] == ["workflow_execution_attempts", "executions"], "Result emitted update order")
    return {
        "case_id": fixture["case_id"],
        "reference": reference,
        "native_kind": result["decision"]["kind"],
        "python_projection_calls": len(calls),
        "python_event_count": len(observed_events),
        "rust_events": "absent-held",
        "rust_logs": "absent-empty-buffer-profile",
    }


def number_vector(case_id):
    midpoint = "0.500000000000000055511151231257827021181583404541015625"
    values = {
        "p-005": "0.005",
        "p-015": "0.015",
        "p-neg-int-zero": "-0",
        "p-neg-float-zero": "-0.0",
        "p-exp-zero": "0e0",
        "p-exp-one": "1e0",
        "p-int-above53": "9007199254740993",
        "p-u64-max": "18446744073709551615",
        "p-long-midpoint-exact": midpoint,
        "p-long-midpoint-below": "0.500000000000000055511151231257827021181583404541015624",
        "p-long-midpoint-above": "0.500000000000000055511151231257827021181583404541015626",
        "p-minsubnormal": "5e-324",
        "p-overflow": "1e309",
        "p-underflow": "1e-9999",
        "p-cpu-int-i64-min": "-9223372036854775808",
        "p-cpu-int-i64-max": "9223372036854775807",
        "p-cpu-int-u64-max": "18446744073709551615",
        "p-cpu-int-below53": "9007199254740991",
        "p-cpu-int-above53": "9007199254740993",
        "p-cpu-int-neg-above53": "-9007199254740993",
    }
    check(case_id in values, "Result number vector")
    return values[case_id], "cpu" if case_id.startswith("p-cpu-") else "roi"


async def decode_control(case, case_id):
    if case_id == "p-005":
        clock_comparison_controls()
        case.request.node.user_properties.append(("result_clock_controls", 6))
        completed = source_observer_controls()
        check(completed == 8, "Result observer control completion")
        case.request.node.user_properties.append(("result_source_observer_controls", completed))
        check(
            case.request.node.nodeid
            == "tests/parity/test_workflow_sql.py::test_result_nonfault_control[Decode-parity gate-p-005]",
            "Result feature fixture Item identity",
        )
        check(
            not any(key == "result_feature_fixture" for key, _ in case.request.node.user_properties),
            "Result feature fixture duplicate property",
        )
        source_admission(case.source_observer)
        fixture_case = next(value for value in load_fixture()["cases"] if value["case_id"] == "s-result-absent")
        fields = decode(fixture_case["raw_fields_json"].encode())
        marker = {"$serde_json::private::RawValue": '{"hidden":1}', "visible": 2}
        for key in ("result", "variables", "execution_context"):
            fields[key] = {"kind": "value", "value": deepcopy(marker)}
        prepared = prepare_json_inputs(fields, "success")
        for key in ("prepared_result", "prepared_variables", "prepared_context"):
            check(prepared[key]["kind"] == "value", "Result feature Python presence")
            ordinary = json.loads(json.dumps(prepared[key]["value"], allow_nan=False))
            check(
                type(ordinary) is dict
                and set(ordinary) == {"$serde_json::private::RawValue", "visible"}
                and ordinary["$serde_json::private::RawValue"] == '{"hidden":1}'
                and type(ordinary["visible"]) is int
                and ordinary["visible"] == 2,
                "Result feature Python ordinary preservation",
            )
        marker_request = {
            "schema": "bifrost.test.workflow-sql/v1",
            "case_id": "syntheticFeatureMarker",
            "operation": {"kind": "result", "lane": "success", "raw_fields": fields},
            "cohort": {"execution_id": "00000000-0000-0000-0000-000000000001", "submitted_token": None},
            "projection": prepared,
        }
        code, raw = await invoke(case, json.dumps(marker_request, allow_nan=False).encode(), "observe-feature-marker")
        check(code == 0, "Result feature native exit")
        observed = decode(raw)
        closed(observed, {"schema", "case_id", "result", "variables", "context"})
        check(
            observed["schema"] == "bifrost.test.workflow-result-feature-marker/v1"
            and observed["case_id"] == "syntheticFeatureMarker",
            "Result feature native identity",
        )
        for key in ("result", "variables", "context"):
            check(type(observed[key]) is bool and observed[key], "Result feature native preservation")
        property_value = {
            "schema": "bifrost.test.workflow-result-feature-fixture/v1",
            "case_id": "syntheticFeatureMarker",
            "python_completed": True,
            "native_completed": True,
            "result": observed["result"],
            "variables": observed["variables"],
            "context": observed["context"],
        }
        encoded = json.dumps(property_value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        check(encoded.isascii() and len(encoded.encode("ascii")) <= 512, "Result feature property bound")
        case.request.node.user_properties.append(("result_feature_fixture", encoded))
    lexeme, role = number_vector(case_id)
    request = {"schema": "bifrost.test.workflow-sql-number/v1", "case_id": case_id, "role": role, "lexeme": lexeme}
    code, raw = await invoke(case, json.dumps(request).encode(), "decode-number")
    check(code == 0, "Result number decoder exit")
    value = decode(raw)
    closed(value, {"schema", "case_id", "kind", "integer", "float_bits", "admitted"})
    check(
        value["schema"] == "bifrost.test.workflow-sql-number-result/v1" and value["case_id"] == case_id,
        "Result number identity",
    )
    check(type(value["admitted"]) is bool, "Result number admitted type")
    # Genuine Python decoder and float(int), never a prepared Decimal/bits oracle.
    python = json.loads(lexeme)
    if isinstance(python, float) and not math.isfinite(python):
        check(
            value["kind"] == "invalid"
            and value["integer"] is None
            and value["float_bits"] is None
            and value["admitted"] is False,
            "Result nonfinite exact invalid disposition",
        )
        return
    kind = "float" if isinstance(python, float) else "signed" if lexeme.startswith("-") else "unsigned"
    check(value["kind"] == kind, "Result number lexical kind")
    check(value["integer"] == (str(python) if isinstance(python, int) else None), "Result number integer width")
    floating = float(python) if role == "cpu" else python if isinstance(python, float) else None
    if floating is None:
        check(value["float_bits"] is None, "Result integer unsolicited float")
    elif math.isfinite(floating):
        check(value["float_bits"] == struct.pack(">d", floating).hex(), "Result decoder actual float bits")
    else:
        check(value["admitted"] is False, "Result finite profile boundary")
    check(
        value["admitted"] == (not isinstance(python, float) or math.isfinite(python)), "Result number profile admission"
    )


def mutate_codec(payload: bytes, case_id: str) -> bytes:
    value = decode(payload)
    fields = value["operation"]["raw_fields"]

    def scalar(name, item):
        fields[name] = {"kind": "value", "value": item}

    if case_id == "c-duplicate-envelope":
        return b'{"schema":"bifrost.test.workflow-sql/v1",' + payload[1:]
    if case_id == "c-duplicate-nested":
        return payload.replace(b'"status":{"kind":"absent"}', b'"status":{"kind":"absent","kind":"absent"}', 1)
    if case_id == "c-unknown-field":
        value["unknown"] = 1
    elif case_id == "c-missing-required":
        del value["cohort"]
    elif case_id == "c-invalid-tag":
        fields["duration_ms"] = {"kind": "unknown"}
    elif case_id == "c-tag-extra-value":
        fields["duration_ms"] = {"kind": "absent", "value": 0}
    elif case_id == "c-value-null-loophole":
        scalar("duration_ms", None)
    elif case_id == "c-numeric-bool":
        scalar("duration_ms", True)
    elif case_id == "c-numeric-string":
        scalar("duration_ms", "7")
    elif case_id == "c-duration-over-i32":
        scalar("duration_ms", 2147483648)
    elif case_id in {"c-metric-over-i64", "c-cpu-over-u64", "c-cpu-below-i64"}:
        name = "peak_memory_bytes" if case_id == "c-metric-over-i64" else "cpu_total_seconds"
        number = (
            9223372036854775808
            if name == "peak_memory_bytes"
            else 18446744073709551616
            if case_id.endswith("over-u64")
            else -9223372036854775809
        )
        fields["metrics"] = {
            "kind": "value",
            "value": {k: {"kind": "value", "value": number} if k == name else {"kind": "absent"} for k in METRICS},
        }
    elif case_id in {"c-roi-over-u64", "c-float-overflow", "c-raw-roi-nan"}:
        fields["roi"] = {
            "kind": "value",
            "value": {
                "time_saved": {"kind": "null"},
                "value": {
                    "kind": "value",
                    "value": 18446744073709551616 if case_id == "c-roi-over-u64" else "RAW_NUMBER",
                },
            },
        }
    elif case_id == "c-surrogate":
        scalar("result", "\ud800")
        value["projection"]["prepared_result"] = fields["result"]
    elif case_id == "c-depth65":
        nested = 1
        for _ in range(65):
            nested = [nested]
        scalar("result", nested)
        value["projection"]["prepared_result"] = fields["result"]
    elif case_id == "c-request65537":
        return payload + b" " * (65537 - len(payload))
    elif case_id == "c-trailing-json":
        return payload + b"{}"
    elif case_id == "c-wrong-branch-field":
        value["operation"]["lane"] = "failure"
    elif case_id == "c-projection-class-mismatch":
        scalar("result", {"x": 1})
        value["projection"]["prepared_result"] = {"kind": "value", "value": []}
    elif case_id in {
        "c-json-data-float",
        "c-json-data-over-u64",
        "c-json-data-below-i64",
        "c-json-string-nul",
        "c-json-key-nul",
    }:
        item = {
            "c-json-data-float": 1.0,
            "c-json-data-over-u64": 18446744073709551616,
            "c-json-data-below-i64": -9223372036854775809,
            "c-json-string-nul": "x\x00y",
            "c-json-key-nul": {"x\x00y": 1},
        }[case_id]
        scalar("result", item)
        value["projection"]["prepared_result"] = fields["result"]
    else:
        check(case_id == "c-duplicate-nested", "Result unknown codec control")
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if case_id in {"c-float-overflow", "c-raw-roi-nan"}:
        raw = raw.replace(b'"RAW_NUMBER"', b"1e309" if case_id == "c-float-overflow" else b"NaN")
    return raw


async def codec_control(engine, case, case_id, fixture):
    cohort = ResultCohort(engine, fixture)
    case.cohorts.append(cohort)
    await cohort.seed()
    before = await cohort.snapshot()
    good = request_bytes(cohort, fixture["raw_fields_json"])
    bad = mutate_codec(good, case_id)
    dsn = driver_dsn(engine)
    case.source_observer.session.observe_dsn_return(case.source_observer, dsn)
    code, raw = await invoke(case, bad, "apply-result", database_url=dsn)
    check(code == 2 and raw == b"", "Result malformed input pre-admission")
    after = await cohort.snapshot()
    check(before == after, "Result malformed input touched owned rows")
    await cohort.empty_buffers()


SCHEMA_ERRORS = {
    "q-duplicate-token": ("23505", "uq_workflow_execution_attempt_claim_token"),
    "q-two-active": ("23505", "uq_workflow_execution_attempt_active"),
    "q-duplicate-attempt-number": ("23505", "uq_workflow_execution_attempt_number"),
    "q-running-with-completion": ("23514", "ck_workflow_execution_attempt_terminal_time"),
    "q-running-without-start": ("23514", "ck_workflow_execution_attempt_state_shape"),
    "q-claimed-without-token": ("23514", "ck_workflow_execution_attempt_state_shape"),
    "q-dispatching-with-token": ("23514", "ck_workflow_execution_attempt_state_shape"),
    "q-unknown-phase": ("23514", "ck_workflow_execution_attempt_phase"),
    "q-foreign-execution-fk": ("23503", "workflow_execution_attempts_execution_id_fkey"),
}


async def schema_control(engine, case, case_id, fixture):
    from uuid import uuid4

    cohort = ResultCohort(engine, fixture)
    case.cohorts.append(cohort)
    await cohort.seed()
    # Each offending transaction uses otherwise legal shape and isolated conflict.
    before = await cohort.snapshot()
    attempt = dict(before["attempts"][0])
    attempt.update(
        id=uuid4(),
        execution_id=cohort.ids["execution"],
        attempt_number=2,
        claim_token=uuid4(),
        status="succeeded",
        phase="terminal",
        completed_at=SEED_TIME,
        started_at=SEED_TIME,
        published_at=SEED_TIME,
        claimed_at=SEED_TIME,
    )
    if case_id == "q-duplicate-token":
        attempt["execution_id"] = cohort.ids["foreign"]
        attempt["claim_token"] = cohort.ids["current_token"]
    elif case_id == "q-two-active":
        attempt.update(status="running", phase="execution", completed_at=None)
    elif case_id == "q-duplicate-attempt-number":
        attempt["attempt_number"] = 1
    elif case_id == "q-running-with-completion":
        attempt.update(status="running", phase="execution", completed_at=SEED_TIME)
    elif case_id == "q-running-without-start":
        attempt.update(status="running", phase="execution", completed_at=None, started_at=None)
    elif case_id == "q-claimed-without-token":
        attempt.update(status="claimed", phase="claim", completed_at=None, claim_token=None)
    elif case_id == "q-dispatching-with-token":
        attempt.update(
            status="dispatching",
            phase="dispatch",
            completed_at=None,
            published_at=None,
            claimed_at=None,
            started_at=None,
        )
    elif case_id == "q-unknown-phase":
        attempt["phase"] = "synthetic_unknown"
    elif case_id == "q-foreign-execution-fk":
        attempt["execution_id"] = cohort.ids["missing"]
    expected = SCHEMA_ERRORS[case_id]
    observed = None
    async with cohort.sessions() as db:
        try:
            db.add(WorkflowExecutionAttempt(**attempt))
            await db.flush()
        except DBAPIError as error:
            observed = sql_error(error)
        finally:
            await db.rollback()
    check(observed == expected, "Result actual named schema diagnostic")
    check(await cohort.snapshot() == before, "Result schema rollback changed rows")
    # Genuine valid continuation after rollback, independent session/row admission.
    fields = decode(fixture["raw_fields_json"].encode())
    reference, calls, _, _ = await python_result(cohort, fields, case)
    check(reference == "returned" and bool(calls), "Result schema genuine admission after rollback")
    continued = await cohort.snapshot()
    check(continued["attempts"] != before["attempts"], "Result schema valid continuation did not write")


async def source_control(engine, case, case_id, fixture):
    selected = deepcopy(fixture)
    selected["lane"] = (
        "failure" if case_id in {"x-OrphanedExecution", "x-ProcessCrashError", "x-WorkerShutdownError"} else "success"
    )
    selected["seed"].update(
        tracking=case_id != "x-legacy-unfenced",
        attempt="none" if case_id == "x-legacy-unfenced" else "running",
        token="missing" if case_id == "x-legacy-unfenced" else "current",
    )
    cohort = ResultCohort(engine, selected)
    case.cohorts.append(cohort)
    await cohort.seed()
    fields = {name: {"kind": "absent"} for name in (FAILURE if selected["lane"] == "failure" else SUCCESS)}
    if selected["lane"] == "failure":
        fields["error_type"] = {"kind": "value", "value": case_id[2:]}
    if case_id == "x-duration-outside-i32-source":
        fields["duration_ms"] = {"kind": "value", "value": 2147483648}
    if case_id == "x-metric-outside-i64-source":
        fields["metrics"] = {
            "kind": "value",
            "value": {
                k: {"kind": "value", "value": 9223372036854775808} if k == "peak_memory_bytes" else {"kind": "absent"}
                for k in METRICS
            },
        }
    if case_id in {"x-missing-logical-active-metadata", "x-missing-logical-no-metadata"}:
        async with cohort.sessions() as db:
            await db.execute(delete(Execution).where(Execution.id == cohort.ids["execution"]))
            await db.commit()
        if case_id.endswith("no-metadata"):
            await cohort.redis.delete(active_execution_key(str(cohort.ids["execution"])))
    if case_id == "x-unsupported-buffer":
        key = pending_changes_key(str(cohort.ids["execution"]))
        await cohort.redis.hset(key, "synthetic", "{}")
        rejected = False
        try:
            await cohort.empty_buffers()
        except AssertionError:
            rejected = True
        finally:
            await cohort.redis.delete(key)
        check(rejected, "Result unexpected buffer not rejected")
        await cohort.empty_buffers()
        return {"reference": "profile-excluded", "native": "not-invoked"}
    before = await cohort.snapshot()
    reference, calls, observed_events, _ = await python_result(
        cohort, fields, case, source_width=case_id in {"x-duration-outside-i32-source", "x-metric-outside-i64-source"}
    )
    after = await cohort.snapshot()
    if case_id in {"x-duration-outside-i32-source", "x-metric-outside-i64-source"}:
        check(
            reference == "source_width_failure"
            and before["executions"] == after["executions"]
            and before["attempts"] == after["attempts"],
            "Result source width/rollback characterization",
        )
    elif case_id.startswith("x-missing-logical"):
        check(
            before["executions"] == after["executions"] and not calls, "Result missing logical source characterization"
        )
    elif case_id == "x-legacy-unfenced":
        check(bool(calls) and before["executions"] != after["executions"], "Result actual legacy branch")
    else:
        check(bool(calls) and before["attempts"] != after["attempts"], "Result actual worker-loss source branch")
    return {"reference": reference, "native": "outside-private-profile", "event_count": len(observed_events)}
