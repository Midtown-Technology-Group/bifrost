"""Offline proposal checks; never runtime, durable-owner or production-codec proof."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator, validators
from referencing import Registry, Resource

from boundary import verify
from oracle import Rejection, Session, decode, wire

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validator():
    # JSON Schema's mathematical integer permits 1.0; the wire oracle does not.
    integer_checker = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int
    )
    cls = validators.extend(Draft202012Validator, type_checker=integer_checker)
    schemas = [load(path) for path in (ROOT.parent / "language-neutral").glob("*.schema.json")]
    schema = load(ROOT / "profile.schema.json")
    schemas.append(schema)
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    # Reuse the foundation's strict UTC checker without importing platform code.
    import importlib.util
    spec = importlib.util.spec_from_file_location("foundation_check", ROOT.parent / "language-neutral/check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for s in schemas:
        cls.check_schema(s)
    return cls(schema, registry=registry, format_checker=module.FORMATS)


def expect_rejection(call, expected, name):
    try:
        call()
        actual = None
    except Rejection as exc:
        actual = str(exc)
    if actual != expected:
        raise ValueError(f"{name}: expected {expected}, got {actual}")


def main():
    validate = validator()
    structural = load(ROOT / "structural-vectors.json")
    wire_cases = load(ROOT / "wire-vectors.json")
    corpus = load(ROOT / "session-vectors.json")
    sessions = corpus["cases"]
    for vectors in (structural, wire_cases, sessions):
        names = [v["name"] for v in vectors]
        if len(names) != len(set(names)):
            raise ValueError("duplicate vector name")
    for vector in structural:
        if validate.is_valid(vector["frame"]) != vector["valid"]:
            raise ValueError("structural expectation: " + vector["name"])
    for vector in wire_cases:
        if "recipe" in vector:
            recipe = vector["recipe"]
            data = (bytes.fromhex(recipe["prefix_hex"])
                    + bytes.fromhex(recipe["repeat_byte_hex"]) * recipe["count"]
                    + bytes.fromhex(recipe["suffix_hex"]))
        else:
            data = bytes.fromhex(vector["hex"])
        expect_rejection(lambda: decode(data, validate),
                         vector["error"], vector["name"])
    for vector in sessions:
        session = Session(**corpus["environment"])
        setup = [] if vector["setup"] is None else corpus["fixtures"][vector["setup"]]
        for step in setup + vector["steps"]:
            event = step["input"]
            def apply():
                if event["event"] == "receive":
                    data = bytes.fromhex(event["hex"]) if "hex" in event else wire(event["frame"])
                    frame = decode(data, validate)
                    session.receive(event["direction"], frame, data[4:])
                else:
                    session.event(event)
            expect_rejection(apply, step["error"], vector["name"])
            if session.snapshot() != step["after"]:
                raise ValueError("transition expectation: " + vector["name"])
    baseline = load(ROOT / "upstream-boundary.json")
    verify(REPO, baseline["fingerprint"])
    print(f"PASS proposal only: {len(structural)} structural, {len(wire_cases)} wire specimens, "
          f"{len(sessions)} synthetic session vectors; Python source tripwire unchanged")
    print("Production Rust/Python codecs, processes, authorization and durable transactions NOT exercised.")


if __name__ == "__main__":
    main()
