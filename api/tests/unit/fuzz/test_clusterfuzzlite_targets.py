from __future__ import annotations

import runpy
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import fuzz.harnesses as harnesses

from fuzz.harnesses import HARNESS_TARGETS


ATHERIS_TARGET_DIR = Path(__file__).resolve().parents[3] / "fuzz" / "atheris_targets"
TARGET_TO_FUZZER = {
    "cron-parser": "cron_parser_fuzzer.py",
    "editor-search": "editor_search_fuzzer.py",
    "webhook-request": "webhook_request_fuzzer.py",
}


def test_clusterfuzzlite_targets_cover_registered_harnesses():
    assert set(TARGET_TO_FUZZER) == set(HARNESS_TARGETS)

    for fuzzer_file in TARGET_TO_FUZZER.values():
        assert (ATHERIS_TARGET_DIR / fuzzer_file).is_file()


@pytest.mark.parametrize(
    ("target", "payload"),
    [("cron-parser", b"*/5 * * * *"), ("editor-search", b"needle"), ("webhook-request", b"4")],
)
def test_native_target_forwards_options_and_mutated_input(monkeypatch, target, payload):
    events = []
    callback = None
    original = HARNESS_TARGETS[target]
    function_name = original.__name__

    def consume(data):
        events.append(("input", data))
        original(data)

    @contextmanager
    def instrument_imports(*, include):
        events.append(("instrument", include))
        yield
        events.append(("imports-loaded",))

    argv = [TARGET_TO_FUZZER[target], "-runs=1", "corpus path"]

    def setup(options, test_one_input):
        nonlocal callback
        assert options is argv
        callback = test_one_input
        events.append(("setup",))

    def fuzz():
        assert callback is not None
        events.append(("fuzz",))
        callback(payload)

    monkeypatch.setattr(harnesses, function_name, consume)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setitem(sys.modules, "atheris", SimpleNamespace(
        instrument_imports=instrument_imports, Setup=setup, Fuzz=fuzz,
    ))
    runpy.run_path(str(ATHERIS_TARGET_DIR / TARGET_TO_FUZZER[target]), run_name="__main__")

    assert events == [
        ("instrument", ["fuzz", "src"]), ("imports-loaded",),
        ("setup",), ("fuzz",), ("input", payload),
    ]
