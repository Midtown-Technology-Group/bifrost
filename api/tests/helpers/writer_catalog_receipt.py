"""Pure contracts for the fixed isolated writer catalog receipt, not writer safety."""

import hashlib
import json
from datetime import datetime

ROOTS = (
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
REFERENCE_SOURCE_MAIN = "4abdf1a163986b6bd86aa7bafe6fa56b963acbb6"
MIGRATION_REVISION = "20261001_solution_src_account"
ROW_CAPS = (12, 1, 256, 256, 1024, 2048, 4096, 1024, 16)
SNAPSHOT_BYTES = 4 * 1024 * 1024
RECEIPT_BYTES = 8 * 1024 * 1024
MAX_ARRAY = 128
ENUMS = {
    "RK": frozenset(("r", "i", "S", "t", "v", "m", "c", "f", "p", "I")),
    "FKAction": frozenset(("a", "r", "c", "n", "d")),
    "FKMatch": frozenset(("f", "p", "s")),
    "PUC": frozenset(("p", "u", "c")),
    "CT": frozenset(("p", "u", "c", "f", "t", "x")),
    "Enabled": frozenset(("O", "D", "R", "A")),
    "Volatility": frozenset(("i", "s", "v")),
    "Parallel": frozenset(("s", "r", "u")),
    "PolicyCmd": frozenset(("*", "r", "a", "w", "d")),
    "Scope": frozenset(("incident_fk", "outside_incident_scope", "unbound")),
    "ReadOnly": frozenset(("on", "off")),
    "Isolation": frozenset(
        ("read uncommitted", "read committed", "repeatable read", "serializable")
    ),
}
INTEGER_DOMAINS = {
    "O": (1, 4294967295),
    "Z": (0, 4294967295),
    "I": (0, 2147483647),
    "H": (0, 32767),
    "P": (1, 2147483647),
    "K": (1, 32767),
}
# Exact SQL column order and native nullability; no product/dependency imports.
SCHEMAS = (
    (  # Q0
        ("name", "S"),
        ("nspname", "S?"),
        ("relation_oid", "O?"),
        ("relkind", "RK?"),
    ),
    (  # Q1
        ("database_name", "S"),
        ("session_user", "S"),
        ("current_user", "S"),
        ("backend_pid", "P"),
        ("postmaster_started_at", "T"),
        ("server_version_num", "P"),
        ("transaction_read_only", "ReadOnly"),
        ("transaction_isolation", "Isolation"),
    ),
    (  # Q2
        ("role_oid", "O"),
        ("rolname", "S"),
        ("rolsuper", "B"),
        ("rolinherit", "B"),
        ("rolcreaterole", "B"),
        ("rolcreatedb", "B"),
        ("rolcanlogin", "B"),
        ("rolreplication", "B"),
        ("rolbypassrls", "B"),
        ("session_member", "B"),
        ("session_usage", "B"),
        ("current_member", "B"),
        ("current_usage", "B"),
    ),
    (  # Q3
        ("relation_oid", "O"),
        ("nspname", "S"),
        ("relname", "S"),
        ("relkind", "RK"),
        ("owner_oid", "O"),
        ("owner", "S"),
        ("schema_owner_oid", "O"),
        ("schema_owner", "S"),
        ("relrowsecurity", "B"),
        ("relforcerowsecurity", "B"),
        ("relispartition", "B"),
        ("has_inheritance", "B"),
        ("is_root", "B"),
        ("is_ancestor", "B"),
        ("is_dependent", "B"),
        ("is_boundary", "B"),
        ("schema_usage", "B"),
        ("schema_create", "B"),
        ("can_select", "B"),
        ("can_insert", "B"),
        ("can_update", "B"),
        ("can_delete", "B"),
        ("can_truncate", "B"),
        ("can_reference", "B"),
        ("can_trigger", "B"),
    ),
    (  # Q4
        ("constraint_oid", "O"),
        ("conname", "S"),
        ("child_oid", "O"),
        ("parent_oid", "O"),
        ("conkey", "AK+"),
        ("confkey", "AK+"),
        ("child_columns", "AS+"),
        ("parent_columns", "AS+"),
        ("confdeltype", "FKAction"),
        ("confupdtype", "FKAction"),
        ("confmatchtype", "FKMatch"),
        ("condeferrable", "B"),
        ("condeferred", "B"),
        ("convalidated", "B"),
        ("conislocal", "B"),
        ("coninhcount", "I"),
        ("parent_constraint_oid", "Z"),
    ),
    (  # Q5
        ("constraint_oid", "O"),
        ("relation_oid", "O"),
        ("conname", "S"),
        ("contype", "PUC"),
        ("conkey", "AK?"),
        ("columns", "AS*"),
        ("index_oid", "Z"),
        ("condeferrable", "B"),
        ("condeferred", "B"),
        ("convalidated", "B"),
        ("conislocal", "B"),
        ("coninhcount", "I"),
        ("connoinherit", "B"),
        ("parent_constraint_oid", "Z"),
        ("index_valid", "B?"),
        ("nulls_not_distinct", "B?"),
    ),
    (  # Q6
        ("trigger_oid", "O"),
        ("relation_oid", "O"),
        ("tgname", "S"),
        ("tgenabled", "Enabled"),
        ("tgisinternal", "B"),
        ("tgtype", "H"),
        ("trigger_columns", "AK*"),
        ("constraint_oid", "Z"),
        ("related_oid", "Z"),
        ("parent_trigger_oid", "Z"),
        ("tgdeferrable", "B"),
        ("tginitdeferred", "B"),
        ("tgnargs", "H"),
        ("has_trigger_arguments", "B"),
        ("has_trigger_condition", "B"),
        ("trigger_constraint_type", "CT?"),
        ("constraint_relation_oid", "Z?"),
        ("referenced_relation_oid", "Z?"),
        ("reference_scope", "Scope"),
        ("function_oid", "O"),
        ("function_schema", "S"),
        ("proname", "S"),
        ("function_schema_owner_oid", "O"),
        ("function_schema_owner", "S"),
        ("function_owner_oid", "O"),
        ("function_owner", "S"),
        ("function_owner_super", "B"),
        ("function_owner_bypassrls", "B"),
        ("prosecdef", "B"),
        ("proleakproof", "B"),
        ("provolatile", "Volatility"),
        ("proparallel", "Parallel"),
        ("argument_type_oids", "AO*"),
        ("return_type_oid", "O"),
        ("has_function_configuration", "B"),
        ("has_configured_search_path", "B"),
        ("can_execute", "B"),
    ),
    (  # Q7
        ("policy_oid", "O"),
        ("relation_oid", "O"),
        ("polname", "S"),
        ("polcmd", "PolicyCmd"),
        ("polpermissive", "B"),
        ("role_oids", "AZ+"),
        ("has_using_expression", "B"),
        ("has_check_expression", "B"),
    ),
    (  # Q8
        ("version_num", "S"),
    ),
)


class CatalogContractError(ValueError):
    """A static contract failure; never include a driver error or row value."""


def require(condition, code="catalog-contract"):
    if not condition:
        raise CatalogContractError(code)


def validate_value(value, rule):
    if rule.endswith("?"):
        if value is None:
            return
        rule = rule[:-1]
        if rule == "AK":
            rule = "AK*"
    if rule.startswith("A"):
        require(type(value) is list and len(value) <= MAX_ARRAY, "array-type-or-cap")
        require(not rule.endswith("+") or bool(value), "empty-required-array")
        element_rule = {"AK": "K", "AS": "S", "AO": "O", "AZ": "Z"}[rule[:-1]]
        for element in value:
            validate_value(element, element_rule)
    elif rule == "B":
        require(type(value) is bool, "boolean-type")
    elif rule in INTEGER_DOMAINS:
        lower, upper = INTEGER_DOMAINS[rule]
        require(type(value) is int and lower <= value <= upper, "integer-domain")
    elif rule == "T":
        require(
            type(value) is datetime and value.utcoffset() is not None, "timestamp-type"
        )
    elif rule == "S":
        require(
            type(value) is str and 0 < len(value) <= 128 and value.isprintable(),
            "text-type",
        )
        require(len(value.encode("utf-8")) <= 128, "text-byte-cap")
    else:
        require(
            rule in ENUMS and type(value) is str and value in ENUMS[rule],
            "catalog-code",
        )


def validate_rows(query_index, rows):
    require(
        type(query_index) is int and 0 <= query_index < len(SCHEMAS), "query-identity"
    )
    require(type(rows) is list and len(rows) <= ROW_CAPS[query_index], "row-cap")
    schema = SCHEMAS[query_index]
    columns = tuple(name for name, _rule in schema)
    for row in rows:
        require(type(row) is dict and tuple(row) == columns, "ordered-row-schema")
        for name, rule in schema:
            validate_value(row[name], rule)


def _unique(rows, column):
    result = {row[column]: row for row in rows}
    require(len(result) == len(rows), "duplicate-identity")
    return result


def _closure(roots, edges, *, ancestors):
    reached = set(roots)
    while True:
        additions = {
            edge["parent_oid"] if ancestors else edge["child_oid"]
            for edge in edges
            if (edge["child_oid"] if ancestors else edge["parent_oid"]) in reached
        }
        if additions <= reached:
            return reached
        reached.update(additions)


def _local_constraint(row):
    require(
        row["parent_constraint_oid"] == 0
        and row["coninhcount"] == 0
        and row["conislocal"],
        "inherited-constraint-unsupported",
    )


def validate_snapshot(observations):
    require(
        type(observations) is list and len(observations) == 9, "snapshot-query-count"
    )
    for query_index, rows in enumerate(observations):
        validate_rows(query_index, rows)
    (
        root_rows,
        identities,
        roles,
        relations,
        edges,
        constraints,
        triggers,
        policies,
        revisions,
    ) = observations
    roots_by_name = _unique(root_rows, "name")
    require(set(roots_by_name) == set(ROOTS), "exact-root-set")
    require(
        all(
            row["nspname"] == "public" and row["relkind"] == "r" and row["relation_oid"]
            for row in root_rows
        ),
        "missing-or-nonordinary-root",
    )
    root_ids = set(_unique(root_rows, "relation_oid"))
    require(len(identities) == 1, "identity-count")
    identity = identities[0]
    require(
        identity["database_name"] == "bifrost_test"
        and 160000 <= identity["server_version_num"] < 170000
        and identity["transaction_read_only"] == "on"
        and identity["transaction_isolation"] == "repeatable read",
        "fixed-database-transaction",
    )
    _unique(roles, "role_oid")
    role_names = _unique(roles, "rolname")
    require(
        {identity["session_user"], identity["current_user"]} <= set(role_names),
        "identity-roles-missing",
    )
    relation_map = _unique(relations, "relation_oid")
    require(
        len({(row["nspname"], row["relname"]) for row in relations}) == len(relations),
        "duplicate-relation-name",
    )
    require(root_ids <= set(relation_map), "missing-root-relation")
    for row in root_rows:
        observed = relation_map[row["relation_oid"]]
        require(
            (observed["nspname"], observed["relname"]) == ("public", row["name"]),
            "root-identity-mismatch",
        )
    for row in relations:
        require(
            row["relkind"] == "r"
            and not row["relispartition"]
            and not row["has_inheritance"],
            "relation-kind-or-inheritance",
        )
    edge_map = _unique(edges, "constraint_oid")
    endpoints = set()
    for edge in edges:
        _local_constraint(edge)
        endpoints.update((edge["child_oid"], edge["parent_oid"]))
        require(
            len(edge["conkey"])
            == len(edge["confkey"])
            == len(edge["child_columns"])
            == len(edge["parent_columns"]),
            "fk-column-pair-mismatch",
        )
    require(endpoints <= set(relation_map), "fk-endpoint-missing")
    ancestors = _closure(root_ids, edges, ancestors=True)
    dependents = _closure(root_ids, edges, ancestors=False)
    relevant = ancestors | dependents
    require(
        all(
            edge["child_oid"] in relevant or edge["parent_oid"] in relevant
            for edge in edges
        ),
        "nonincident-edge",
    )
    require(set(relation_map) == relevant | endpoints, "relation-scope-mismatch")
    for row in relations:
        oid = row["relation_oid"]
        require(
            (
                row["is_root"],
                row["is_ancestor"],
                row["is_dependent"],
                row["is_boundary"],
            )
            == (
                oid in root_ids,
                oid in ancestors,
                oid in dependents,
                oid not in relevant,
            ),
            "directional-classification-mismatch",
        )
    constraint_map = _unique(constraints, "constraint_oid")
    require(not (set(edge_map) & set(constraint_map)), "constraint-kind-overlap")
    for row in constraints:
        _local_constraint(row)
        require(row["relation_oid"] in relation_map, "constraint-relation-missing")
        keys = row["conkey"] or []
        require(len(keys) == len(row["columns"]), "constraint-column-mismatch")
        if row["contype"] in {"p", "u"}:
            require(
                bool(keys)
                and row["index_oid"] > 0
                and row["index_valid"] is not None
                and row["nulls_not_distinct"] is not None,
                "index-identity-missing",
            )
        else:
            require(
                row["index_oid"] == 0
                and row["index_valid"] is None
                and row["nulls_not_distinct"] is None,
                "check-index-fields",
            )
    _unique(triggers, "trigger_oid")
    for trigger in triggers:
        require(trigger["relation_oid"] in relation_map, "trigger-relation-missing")
        require(
            trigger["parent_trigger_oid"] == 0 and 1 <= trigger["tgtype"] <= 127,
            "trigger-parent-or-mask",
        )
        require(
            trigger["has_trigger_arguments"] == (trigger["tgnargs"] > 0),
            "trigger-argument-presence",
        )
        require(
            not trigger["has_configured_search_path"]
            or trigger["has_function_configuration"],
            "function-configuration-presence",
        )
        constraint_id = trigger["constraint_oid"]
        joined = (
            trigger["trigger_constraint_type"],
            trigger["constraint_relation_oid"],
            trigger["referenced_relation_oid"],
        )
        if constraint_id == 0:
            require(
                all(value is None for value in joined), "unexpected-constraint-join"
            )
        else:
            require(
                all(value is not None for value in joined), "missing-constraint-join"
            )
        kind, child, parent = joined
        if kind == "f":
            require(child > 0 and parent > 0, "joined-fk-endpoint-zero")
        incident = kind == "f" and (child in relevant or parent in relevant)
        expected_scope = (
            "incident_fk"
            if incident
            else (
                "outside_incident_scope"
                if constraint_id or trigger["related_oid"]
                else "unbound"
            )
        )
        require(trigger["reference_scope"] == expected_scope, "trigger-reference-scope")
        if incident:
            require(constraint_id in edge_map, "incident-trigger-fk-missing")
            edge = edge_map[constraint_id]
            require(
                (child, parent) == (edge["child_oid"], edge["parent_oid"]),
                "trigger-fk-endpoint-mismatch",
            )
            require(
                trigger["relation_oid"] in {child, parent},
                "trigger-fk-relation-mismatch",
            )
            opposite = parent if trigger["relation_oid"] == child else child
            require(trigger["related_oid"] == opposite, "trigger-fk-related-mismatch")
        elif kind in {"p", "u", "c"} and child in relation_map:
            require(constraint_id in constraint_map, "trigger-local-constraint-missing")
            local = constraint_map[constraint_id]
            require(
                (kind, child) == (local["contype"], local["relation_oid"]),
                "trigger-local-constraint-mismatch",
            )
    _unique(policies, "policy_oid")
    require(
        all(row["relation_oid"] in relation_map for row in policies),
        "policy-relation-missing",
    )
    require(
        len(revisions) == 1 and revisions[0]["version_num"] == MIGRATION_REVISION,
        "fixed-migration-revision",
    )
    return {
        "roots": len(root_ids),
        "ancestors": len(ancestors),
        "dependents": len(dependents),
        "relevant": len(relevant),
        "boundary": len(set(relation_map) - relevant),
        "relations": len(relations),
        "incident_fks": len(edges),
    }


def compare_snapshots(direct, pooled):
    require(len(direct) == len(pooled) == 9, "snapshot-query-count")
    for index, (left, right) in enumerate(zip(direct, pooled)):
        if index == 1:
            left = [
                {name: value for name, value in row.items() if name != "backend_pid"}
                for row in left
            ]
            right = [
                {name: value for name, value in row.items() if name != "backend_pid"}
                for row in right
            ]
        require(left == right, "direct-pool-structural-mismatch")


def tabular_observations(observations):
    return [
        {
            "query": f"Q{index}",
            "columns": [name for name, _rule in SCHEMAS[index]],
            "rows": [[row[name] for name, _rule in SCHEMAS[index]] for row in rows],
            "row_count": len(rows),
            "observed_empty": not rows,
        }
        for index, rows in enumerate(observations)
    ]


def _json_default(value):
    require(
        type(value) is datetime and value.utcoffset() is not None, "serialization-type"
    )
    return value.isoformat()


def encode_bounded(value, limit):
    require(type(limit) is int and 0 < limit <= RECEIPT_BYTES, "serialization-limit")
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )
    chunks = []
    total = 0
    for fragment in encoder.iterencode(value):
        chunk = fragment.encode("utf-8")
        total += len(chunk)
        require(total <= limit, "serialization-byte-cap")
        chunks.append(chunk)
    require(total + 1 <= limit, "serialization-byte-cap")
    return b"".join(chunks) + b"\n"


def contract_sha256():
    contract = {
        "schemas": SCHEMAS,
        "row_caps": ROW_CAPS,
        "array_cap": MAX_ARRAY,
        "integer_domains": INTEGER_DOMAINS,
        "enums": {name: sorted(values) for name, values in ENUMS.items()},
        "roots": ROOTS,
        "revision": MIGRATION_REVISION,
        "snapshot_bytes": SNAPSHOT_BYTES,
        "receipt_bytes": RECEIPT_BYTES,
    }
    return hashlib.sha256(encode_bounded(contract, SNAPSHOT_BYTES)).hexdigest()
