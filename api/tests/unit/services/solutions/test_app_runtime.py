"""Compiled App pins bind the exact package, App pointer and full output set."""

from copy import deepcopy
from uuid import uuid4

import pytest

from src.services.solutions.app_runtime import (
    compiled_app_runtime_pin,
    source_archive_sha256,
    verify_compiled_app_runtime_pin,
)


def inputs():
    return {
        "solution_id": uuid4(),
        "application_id": uuid4(),
        "deployment_id": uuid4(),
        "source_sha256": source_archive_sha256(b"exact reviewed package"),
        "source_built": True,
        "outputs": {
            "index.html": b'<html><script type="module" src="assets/main.js"></script></html>',
            "assets/main.js": b"const v = 1;",
        },
    }


def test_complete_app_runtime_pin_matches_actual_outputs():
    identity = inputs()
    proof = compiled_app_runtime_pin(**identity)
    assert verify_compiled_app_runtime_pin(proof, **identity) == proof


@pytest.mark.parametrize(
    "field",
    ["solution_id", "application_id", "deployment_id", "source_sha256", "source_built"],
)
def test_another_package_owner_or_pointer_cannot_reuse_pin(field):
    identity = inputs()
    proof = compiled_app_runtime_pin(**identity)
    identity[field] = (
        False
        if field == "source_built"
        else "f" * 64
        if field == "source_sha256"
        else uuid4()
    )
    with pytest.raises(ValueError, match="differs"):
        verify_compiled_app_runtime_pin(proof, **identity)


@pytest.mark.parametrize("change", ["missing", "extra", "modified"])
def test_entire_compiled_output_inventory_must_match(change):
    identity = inputs()
    proof = compiled_app_runtime_pin(**identity)
    if change == "missing":
        identity["outputs"].pop("assets/main.js")
    elif change == "extra":
        identity["outputs"]["assets/stale.js"] = b"old"
    else:
        identity["outputs"]["assets/main.js"] = b"const v = 2;"
    with pytest.raises(ValueError, match="differs|dependency"):
        verify_compiled_app_runtime_pin(proof, **identity)


@pytest.mark.parametrize(
    "proof", [None, {}, {"runtime_pin_hash": "sha256:" + "f" * 64}]
)
def test_missing_or_partial_evidence_cannot_complete_accounting(proof):
    with pytest.raises(ValueError, match="differs"):
        verify_compiled_app_runtime_pin(proof, **inputs())


def test_runtime_pin_digest_and_fields_cannot_be_changed():
    identity = inputs()
    proof = deepcopy(compiled_app_runtime_pin(**identity))
    proof["runtime_pin_hash"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="differs"):
        verify_compiled_app_runtime_pin(proof, **identity)


def test_incomplete_or_unsafe_compiled_bundle_is_rejected_before_activation():
    identity = inputs()
    identity["outputs"].pop("index.html")
    with pytest.raises(ValueError, match="index.html"):
        compiled_app_runtime_pin(**identity)
    identity["outputs"]["index.html"] = (
        b'<script type="module" src="assets/main.js"></script>'
    )
    identity["outputs"]["../escape.js"] = b"bad"
    with pytest.raises(ValueError):
        compiled_app_runtime_pin(**identity)


def test_streamed_and_in_memory_source_archives_have_same_digest(tmp_path):
    source = b"same reviewed source bytes"
    path = tmp_path / "source.zip"
    path.write_bytes(source)
    assert source_archive_sha256(path) == source_archive_sha256(source)


@pytest.mark.parametrize(
    "html",
    [
        b'<script type="module" src="assets/missing.js"></script>',
        b'<script type="module" src="assets/main.js"></script><link rel="stylesheet" href="assets/missing.css">',
    ],
)
def test_broken_entry_or_css_dependency_cannot_become_runtime_evidence(html):
    identity = inputs()
    identity["outputs"]["index.html"] = html
    with pytest.raises(ValueError, match="dependency"):
        compiled_app_runtime_pin(**identity)


def test_static_html_without_module_entry_retains_existing_deploy_support():
    identity = inputs()
    identity["outputs"] = {"index.html": b"<html>Static document</html>"}
    proof = compiled_app_runtime_pin(**identity)
    assert verify_compiled_app_runtime_pin(proof, **identity) == proof
