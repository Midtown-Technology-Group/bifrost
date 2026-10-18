#!/usr/bin/env python3
"""Project three synthetic RBAC migration proofs from JUnit without raw diagnostics.

A complete receipt establishes only that all three strictly shaped proof records
were emitted. It is not an overall test, CI, release-fence, or deployment verdict.
Unknown JUnit properties are ignored; invalid or duplicate known proofs fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

PROPERTIES = (
    "mtg_rbac_bridge_only_rollback",
    "mtg_rbac_maintenance_fence_commit",
    "mtg_rbac_maintenance_fence_rollback",
)
TABLES = ("roles", "user_roles", "users")
STAGES = ("preflight", "r2b", "bridge", "cutover")
MAX_COUNT = 1_000_000
MAX_JUNIT_BYTES = 64 * 1024 * 1024
MAX_PROPERTY_BYTES = 16 * 1024


class InvalidProof(ValueError):
    """Carries no untrusted input or diagnostics."""


def _require(condition: bool) -> None:
    if not condition:
        raise InvalidProof("invalid_schema")


def _object(value: Any, keys: set[str]) -> dict:
    _require(type(value) is dict and set(value) == keys)
    return value


def _literal(value: Any, expected: Any) -> Any:
    _require(type(value) is type(expected) and value == expected)
    return expected


def _count(value: Any, minimum: int = 0) -> int:
    _require(type(value) is int and minimum <= value <= MAX_COUNT)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise InvalidProof("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise InvalidProof("invalid_json")


def _rollback(value: Any) -> dict:
    fields = {
        "head", "archive_retained", "rollback_window_open_before", "only_recorded_rows_removed",
        "r2b_rows_and_ids_unchanged", "unrelated_permission_rows_preserved",
        "unrelated_boundary_ids_preserved", "created_permission_rows_removed", "created_boundary_rows_removed",
    }
    item = _object(value, fields)
    return {
        "head": _literal(item["head"], "20260929_r2b_roles"),
        **{key: _literal(item[key], True) for key in (
            "archive_retained", "rollback_window_open_before", "only_recorded_rows_removed",
            "r2b_rows_and_ids_unchanged",
        )},
        "unrelated_permission_rows_preserved": _count(item["unrelated_permission_rows_preserved"], 1),
        "unrelated_boundary_ids_preserved": _count(item["unrelated_boundary_ids_preserved"], 1),
        # The fixture grants three permissions to both, plus manage and execute.
        "created_permission_rows_removed": _literal(item["created_permission_rows_removed"], 5),
        "created_boundary_rows_removed": _literal(item["created_boundary_rows_removed"], 1),
    }


def _fence(value: Any, outcome: str) -> dict:
    item = _object(value, {
        "outcome", "same_backend_and_transaction", "transaction_locks_released", "stages", "contenders",
        "original_authority_preserved", "external_full_graph_release_fence_proven",
    })
    stages = item["stages"]
    _require(type(stages) is list and len(stages) == len(STAGES))
    projected_stages = []
    for stage, expected_stage in zip(stages, STAGES, strict=True):
        stage = _object(stage, {"stage", "locks"})
        locks = stage["locks"]
        _require(type(locks) is list and len(locks) == len(TABLES))
        projected_locks = []
        for lock, table in zip(locks, TABLES, strict=True):
            lock = _object(lock, {"table_name", "mode"})
            projected_locks.append({
                "table_name": _literal(lock["table_name"], table),
                "mode": _literal(lock["mode"], "ShareRowExclusiveLock"),
            })
        projected_stages.append({"stage": _literal(stage["stage"], expected_stage), "locks": projected_locks})
    contenders = item["contenders"]
    _require(type(contenders) is list and len(contenders) == len(TABLES))
    projected_contenders = []
    for contender, table in zip(contenders, TABLES, strict=True):
        contender = _object(contender, {
            "table", "waiting_mode", "blocked_by_migrator", "write_attempts", "cancel_sqlstate",
            "transaction_rolled_back", "contender_session_closed", "abort_method",
        })
        method = contender["abort_method"]
        _require(type(method) is str and method in {"observer_cancel", "server_statement_deadline", "server_lock_deadline"})
        expected_code = "55P03" if method == "server_lock_deadline" else "57014"
        projected_contenders.append({
            "table": _literal(contender["table"], table),
            "waiting_mode": _literal(contender["waiting_mode"], "RowExclusiveLock"),
            "blocked_by_migrator": _literal(contender["blocked_by_migrator"], True),
            "write_attempts": _literal(contender["write_attempts"], 1),
            "cancel_sqlstate": _literal(contender["cancel_sqlstate"], expected_code),
            "transaction_rolled_back": _literal(contender["transaction_rolled_back"], True),
            "contender_session_closed": _literal(contender["contender_session_closed"], True),
            "abort_method": method,
        })
    return {
        "outcome": _literal(item["outcome"], outcome),
        "same_backend_and_transaction": _literal(item["same_backend_and_transaction"], True),
        "transaction_locks_released": _literal(item["transaction_locks_released"], True),
        "stages": projected_stages, "contenders": projected_contenders,
        "original_authority_preserved": _literal(item["original_authority_preserved"], True),
        "external_full_graph_release_fence_proven": _literal(item["external_full_graph_release_fence_proven"], False),
    }


def _project(name: str, raw: str) -> dict:
    _require(type(raw) is str and len(raw.encode("utf-8")) <= MAX_PROPERTY_BYTES)
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, InvalidProof):
            raise
        raise InvalidProof("invalid_json") from None
    return _rollback(value) if name == PROPERTIES[0] else _fence(value, name.rsplit("_", 1)[1])


def _provenance(source_sha: str | None, run_id: int | None, run_attempt: int | None,
                shard: int | None, lane: str | None) -> dict:
    result: dict = {}
    if source_sha is not None:
        _require(type(source_sha) is str and re.fullmatch(r"[0-9a-f]{40}", source_sha) is not None)
        result["source_sha"] = source_sha
    for key, value, maximum in (("run_id", run_id, 2**63 - 1), ("run_attempt", run_attempt, 10_000), ("shard", shard, 1_000)):
        if value is not None:
            _require(type(value) is int and 1 <= value <= maximum)
            result[key] = value
    if lane is not None:
        _require(type(lane) is str and lane in {"normal_e2e", "focused_diagnostic"})
        result["lane"] = lane
    return result


def extract_proof(junit: Path, *, source_sha: str | None = None, run_id: int | None = None,
                  run_attempt: int | None = None, shard: int | None = None, lane: str | None = None) -> dict:
    """Return a closed public receipt; status invalid is a fatal extraction failure."""
    result: dict = {
        "schema_version": 1, "evidence_scope": "synthetic_migration_tests_only",
        "status": "not_present", "proofs": {}, "missing_properties": list(PROPERTIES),
        "invalid_properties": [], "provenance": {},
    }
    try:
        result["provenance"] = _provenance(source_sha, run_id, run_attempt, shard, lane)
    except InvalidProof:
        return {**result, "status": "invalid", "input_error": "invalid_provenance"}
    if not junit.exists():
        return {**result, "input_status": "missing"}
    try:
        with junit.open("rb") as stream:
            raw = stream.read(MAX_JUNIT_BYTES + 1)
    except OSError:
        return {**result, "status": "invalid", "input_error": "junit_unreadable"}
    if len(raw) > MAX_JUNIT_BYTES:
        return {**result, "status": "invalid", "input_error": "junit_too_large"}
    result.update(junit_sha256=hashlib.sha256(raw).hexdigest(), junit_bytes=len(raw))
    # Pytest emits UTF-8 JUnit. Decode strictly before the declaration guard,
    # so UTF-16/32 cannot hide declarations behind NUL bytes or a different BOM.
    try:
        document = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return {**result, "status": "invalid", "input_error": "invalid_xml"}
    if "\x00" in document:
        return {**result, "status": "invalid", "input_error": "invalid_xml"}
    declaration = re.match(r"<\?xml\s+[^?]*\?>", document, re.IGNORECASE)
    if declaration is not None:
        encoding = re.search(r"encoding\s*=\s*(['\"])([^'\"]+)\1", declaration.group(0), re.IGNORECASE)
        if encoding is not None and encoding.group(2).lower() != "utf-8":
            return {**result, "status": "invalid", "input_error": "invalid_xml"}
    if "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
        return {**result, "status": "invalid", "input_error": "xml_declarations_forbidden"}
    try:
        root = ElementTree.fromstring(document)
    except (ElementTree.ParseError, ValueError, LookupError):
        return {**result, "status": "invalid", "input_error": "invalid_xml"}
    if root.tag not in {"testsuite", "testsuites"}:
        return {**result, "status": "invalid", "input_error": "invalid_junit_root"}
    result["input_status"] = "parsed"
    found: dict[str, list[ElementTree.Element]] = {name: [] for name in PROPERTIES}
    # Only direct suite properties may carry these reserved proof names.
    # Unknown properties and stdout/stderr/errors remain inert. Reserved names
    # elsewhere still count toward duplicate detection and fail closed.
    allowed_nodes = {
        id(node) for suite in root.iter("testsuite") for properties in suite.findall("properties")
        for node in properties.findall("property")
    }
    for node in root.iter("property"):
        name = node.get("name")
        if name in found:
            found[name].append(node)
    for name, occurrences in found.items():
        if not occurrences:
            continue
        if len(occurrences) != 1:
            result["invalid_properties"].append({"property": name, "category": "duplicate_property"})
            continue
        node = occurrences[0]
        try:
            if id(node) not in allowed_nodes:
                raise InvalidProof("invalid_property_location")
            _require(set(node.attrib) == {"name", "value"} and not list(node) and not node.text)
            result["proofs"][name] = _project(name, node.attrib["value"])
        except InvalidProof as exc:
            result["invalid_properties"].append({"property": name, "category": str(exc)})
    result["missing_properties"] = [name for name in PROPERTIES if name not in result["proofs"]]
    result["status"] = (
        "invalid" if result["invalid_properties"] else
        "complete" if not result["missing_properties"] else
        "incomplete" if result["proofs"] else "not_present"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--run-attempt", type=int)
    parser.add_argument("--shard", type=int)
    parser.add_argument("--lane", choices=("normal_e2e", "focused_diagnostic"))
    args = parser.parse_args()
    receipt = extract_proof(args.junit, source_sha=args.source_sha, run_id=args.run_id,
                            run_attempt=args.run_attempt, shard=args.shard, lane=args.lane)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        print("RBAC proof receipt could not be written", file=sys.stderr)
        return 1
    print("RBAC migration proof extraction: " + receipt["status"])
    return 1 if receipt["status"] == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
