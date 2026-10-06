"""Compiled App pins bind the exact package, App pointer and full output set."""

from copy import deepcopy
from uuid import uuid4
from urllib.parse import urlsplit

import pytest

from src.services.solutions.app_runtime import (
    compiled_index_assets,
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


def test_vite_relative_urls_reference_the_same_compiled_files():
    identity = inputs()
    identity["outputs"]["index.html"] = (
        b'<script type="module" src="./assets/main.js"></script>'
        b'<link rel="stylesheet" href="./assets/main.css">'
    )
    identity["outputs"]["assets/main.css"] = b"body { color: blue; }"
    proof = compiled_app_runtime_pin(**identity)
    assert verify_compiled_app_runtime_pin(proof, **identity) == proof


@pytest.mark.parametrize(
    "entry,css",
    [
        ("assets/main.js?v=1", "assets/main.css#theme"),
        ("./assets/main.js?v=1#entry", "./assets/main.css?v=2#theme"),
        (
            "/api/applications/app/dist/assets/main.js?v=1",
            "/api/applications/app/dist/assets/main.css#theme",
        ),
        (
            "assets/main.js?return=/dist/other.js",
            "assets/main.css?return=/dist/other.css",
        ),
    ],
)
def test_asset_pathnames_are_verified_without_changing_loading_url_semantics(
    entry, css
):
    identity = inputs()
    html = f'<script type="module" src="{entry}"></script><link rel="stylesheet" href="{css}">'
    identity["outputs"]["index.html"] = html.encode()
    identity["outputs"]["assets/main.css"] = b"body { color: blue; }"
    proof = compiled_app_runtime_pin(**identity)
    assert verify_compiled_app_runtime_pin(proof, **identity) == proof
    loaded_entry, loaded_css = compiled_index_assets(html)
    assert loaded_entry is not None and loaded_css is not None
    assert urlsplit(loaded_entry).path == "assets/main.js"
    assert urlsplit(loaded_css).path == "assets/main.css"
    assert urlsplit(loaded_entry).query == urlsplit(entry).query
    assert urlsplit(loaded_entry).fragment == urlsplit(entry).fragment
    assert urlsplit(loaded_css).query == urlsplit(css).query
    assert urlsplit(loaded_css).fragment == urlsplit(css).fragment


@pytest.mark.parametrize(
    "earlier",
    [
        '<script type="module" src="assets/missing.js"></script>',
        '<link rel="stylesheet" href="assets/missing.css">',
    ],
)
def test_earlier_references_cannot_hide_a_missing_compiled_dependency(earlier):
    identity = inputs()
    identity["outputs"]["index.html"] = (
        earlier + '<script type="module" src="assets/main.js"></script>'
        '<link rel="stylesheet" href="assets/main.css">'
    ).encode()
    identity["outputs"]["assets/main.css"] = b"body {}"
    with pytest.raises(ValueError, match="dependency"):
        compiled_app_runtime_pin(**identity)


def test_multiple_references_with_all_bytes_keep_existing_last_entry_selection():
    identity = inputs()
    html = (
        '<script type="module" src="assets/earlier.js"></script>'
        '<link rel="stylesheet" href="assets/earlier.css">'
        '<script type="module" src="assets/main.js"></script>'
        '<link rel="stylesheet" href="assets/main.css">'
    )
    identity["outputs"].update(
        {
            "index.html": html.encode(),
            "assets/earlier.js": b"const earlier = 1;",
            "assets/earlier.css": b"body {}",
            "assets/main.css": b"body { color: blue; }",
        }
    )
    assert compiled_index_assets(html) == ("assets/main.js", "assets/main.css")
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
