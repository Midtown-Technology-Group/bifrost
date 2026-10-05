"""Offline structural checks. Requires the repository's jsonschema==4.26.0."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def utc_timestamp(value):
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z", value):
        return False
    datetime.fromisoformat(value)
    return True


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main():
    validators = {}
    for path in sorted(ROOT.glob("*.schema.json")):
        schema = load(path.name)
        Draft202012Validator.check_schema(schema)
        validators[path.name.removesuffix(".schema.json")] = Draft202012Validator(
            schema, format_checker=FORMATS
        )
    if load("workload.schema.json")["$defs"]["binding"] != load(
        "provision-binding.schema.json"
    )["$defs"]["binding"]:
        raise ValueError("workload/provision binding schema drift")
    vectors = load("structural-vectors.json")
    names = set()
    for vector in vectors:
        key = (vector["schema"], vector["name"])
        if key in names:
            raise ValueError("duplicate structural vector")
        names.add(key)
        actual = validators[vector["schema"]].is_valid(vector["value"])
        if actual != vector["valid"]:
            raise ValueError(f"structural expectation failed: {key}")
    provenance = load("fixtures/provenance.json")
    for record in provenance["files"]:
        path = ROOT / "fixtures" / Path(record["path"]).name
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("workspace fixture byte drift")
        validators["compatibility"].validate(load(str(path.relative_to(ROOT))))
    print(f"PASS: {len(validators)} schemas; {len(vectors)} structural vectors; "
          f"{len(provenance['files'])} unchanged workspace fixture documents")
    print("No runtime, authorization, lifecycle or behavioral conformance exercised.")


if __name__ == "__main__":
    main()
