"""The accounting decision cannot infer delivery from one successful run."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.solution_source_accountability import SourceConsumer, completion_for_source, supersession_for_source, has_untracked_root_reads

NOW = datetime(2026, 10, 1, tzinfo=UTC)
SHA, TREE, HASH = "a" * 40, "b" * 40, "c" * 64
PATH = "features/fixture.py"
RECIPE = "config/solution-delivery/fixture.json"
REGISTRY = "config/solution-delivery/installations.json"


def record(paths=None, **changes):
    return SimpleNamespace(id=uuid4(), source_commit_sha=SHA, source_tree_sha=TREE,
        paths={PATH: HASH} if paths is None else paths, disposition=changes.pop("disposition", "pending"),
        declaration_actor=changes.pop("declaration_actor", "github_actions_oidc"),
        **changes)


def consumer(**changes):
    return replace(SourceConsumer("deployment", "install", None, "sha256:" + "d" * 64,
        {PATH: {"aliased/fixture.py": HASH}}, {"commit_sha": SHA, "tree_sha": TREE,
            "receipt_id": "receipt", "artifact_digest": "sha256:" + "e" * 64,
            "recipe_path": RECIPE, "control_hashes": {RECIPE: HASH}}), **changes)


def decide(declaration, consumers=None, **changes):
    return completion_for_source(declaration, [consumer()] if consumers is None else consumers,
        loose_hashes=changes.pop("loose_hashes", {}), uncertain_loose=changes.pop("uncertain_loose", False),
        verified_at=NOW, **changes)


def test_exact_aliased_source_with_protected_commit_closes_every_path():
    evidence = decide(record())
    assert evidence["source_commit_sha"] == SHA
    assert evidence["paths"][PATH]["consumers"][0]["runtime_sha256"] == {"aliased/fixture.py": HASH}
    assert evidence["paths"][PATH]["protected_git_anchors"][0]["receipt_id"] == "receipt"
    assert evidence["evidence_id"].startswith("sha256:")


@pytest.mark.parametrize("disposition", ["pending", "attention_required", "deferred"])
def test_deadline_diagnostics_do_not_change_completion_identity(disposition):
    assert decide(record(disposition=disposition, reason="deadline elapsed")) is not None


@pytest.mark.parametrize("disposition", ["released", "superseded", "non_production"])
def test_completed_and_nonproduction_records_are_not_reopened(disposition):
    assert decide(record(disposition=disposition)) is None


@pytest.mark.parametrize("paths", [{}, {PATH: None}, {PATH: HASH, "unmapped.py": HASH}])
def test_one_delivery_cannot_close_deleted_unmapped_or_empty_source(paths):
    assert decide(record(paths)) is None


@pytest.mark.parametrize("admission", [True, False])
def test_stale_sibling_dependency_or_accepted_pin_blocks_completion(admission):
    stale = consumer(deployment_id="old-pin", sources={PATH: {"other-alias.py": "f" * 64}}, admission=admission)
    assert decide(record(), [consumer(), stale]) is None


def test_one_stale_runtime_alias_blocks_completion():
    assert decide(record(), [consumer(sources={PATH: {"one.py": HASH, "two.py": "f" * 64}})]) is None


def test_same_shared_bytes_in_another_commit_are_consumers_but_not_anchors():
    sibling = consumer(deployment_id="sibling", proof={"commit_sha": "f" * 40, "tree_sha": TREE})
    assert decide(record(), [consumer(), sibling]) is not None
    assert decide(record(), [sibling]) is None


def test_loose_runtime_must_match_and_dynamic_or_unpinned_consumers_fail_closed():
    assert decide(record(), loose_hashes={PATH: HASH}) is not None
    assert decide(record(), loose_hashes={PATH: "f" * 64}) is None
    assert decide(record(), uncertain_loose=True) is None


@pytest.mark.parametrize("source", [b"from bifrost import files as storage\n", b"from bifrost.files import read as fetch\n",
    b"import bifrost.files as storage\n", b"from bifrost import *\n", b"endpoint = '/api/files/read'\n",
    b"endpoint = '/api/sdk/modules/path'\n", b"import bifrost as bf\nbf.files.read('shared.py')\n",
    b"import bifrost\nbifrost.files.read('shared.py')\n", b"import bifrost as bf\ngetattr(bf, 'files')\n",
    b"import importlib\nimportlib.import_module('bifrost.files')\n", b"__import__('bifrost')\n",
    b"endpoint = '/api/files/editor/content'\n", b"from bifrost.client import get_client\n"])
def test_root_read_aliases_cannot_disappear_from_loose_consumer_evidence(source):
    assert has_untracked_root_reads(source)


def test_reviewed_resource_reads_are_not_root_file_reads():
    assert not has_untracked_root_reads(b"from bifrost import resources, tables\n")


def test_aliased_loose_read_cannot_settle_from_only_the_solution_anchor():
    loose_source = b"import bifrost as bf\nbf.files.read('features/fixture.py', location='workspace')\n"
    assert decide(record(), uncertain_loose=has_untracked_root_reads(loose_source)) is None


def test_matching_already_active_source_still_needs_exact_tree_and_admission():
    assert decide(record(), [consumer(proof={**consumer().proof, "tree_sha": "f" * 40})]) is None
    assert decide(record(), [consumer(admission=False)]) is None


def test_rapid_merge_and_revert_are_separate_declarations_even_for_equal_bytes():
    old, merged, reverted = record(), record(), record()
    merged.source_commit_sha, reverted.source_commit_sha = "d" * 40, "e" * 40
    latest = consumer(proof={**consumer().proof, "commit_sha": reverted.source_commit_sha})
    assert decide(old, [latest]) is None
    assert decide(merged, [latest]) is None
    assert decide(reverted, [latest]) is not None


def test_rapid_merges_and_new_revert_can_supersede_only_verified_ancestors():
    old = record()
    reverted_sha = "e" * 40
    latest = consumer(proof={**consumer().proof, "commit_sha": reverted_sha, "ancestor_commit_shas": [SHA]})
    evidence = supersession_for_source(old, [latest], loose_hashes={}, uncertain_loose=False, verified_at=NOW)
    assert evidence["source_commit_sha"] == SHA
    assert evidence["superseding_source_commit_sha"] == reverted_sha
    assert evidence["original_sha256"] == old.paths
    assert evidence["readback"]["paths"][PATH]["sha256"] == HASH
    unknown = replace(latest, proof={**latest.proof, "ancestor_commit_shas": []})
    assert supersession_for_source(old, [unknown], loose_hashes={}, uncertain_loose=False, verified_at=NOW) is None


@pytest.mark.parametrize("fault", ["manual", "missing_path", "stale_pin", "uncertain"])
def test_supersession_cannot_guess_away_old_manual_unmapped_or_live_debt(fault):
    old = record()
    latest = consumer(proof={**consumer().proof, "commit_sha": "e" * 40, "ancestor_commit_shas": [SHA]})
    consumers = [latest]
    if fault == "manual":
        old.declaration_actor = "platform_admin"
    elif fault == "missing_path":
        old.paths["unmapped.py"] = HASH
    elif fault == "stale_pin":
        consumers.append(consumer(deployment_id="accepted", sources={PATH: {"old.py": "f" * 64}}, admission=False))
    assert supersession_for_source(old, consumers, loose_hashes={}, uncertain_loose=fault == "uncertain", verified_at=NOW) is None


def test_recipe_is_production_control_evidence_not_an_unmapped_runtime_file():
    assert decide(record({PATH: HASH, RECIPE: HASH})) is not None
    assert decide(record({PATH: HASH, RECIPE: "f" * 64})) is None


def test_registry_requires_each_exact_install_scope_recipe_and_current_commit():
    registry = {"path": REGISTRY, "target": "production", "installations": {
        "install": {"recipe_path": RECIPE, "organization_id": None},
        "second": {"recipe_path": "config/solution-delivery/second.json", "organization_id": "org"}}}
    proof = {**consumer().proof, "control_hashes": {RECIPE: HASH, REGISTRY: HASH},
        "installation_registry": registry}
    first = consumer(proof=proof)
    second = consumer(solution_id="second", deployment_id="second", organization_id="org",
        proof={**proof, "recipe_path": registry["installations"]["second"]["recipe_path"]})
    declaration = record({REGISTRY: HASH})
    assert decide(declaration, [first]) is None
    assert decide(declaration, [first, second]) is not None
    assert decide(declaration, [first, replace(second, organization_id=None)]) is None
    assert decide(declaration, [first, replace(second, proof={**second.proof, "commit_sha": "f" * 40})]) is None
