#!/usr/bin/env python3
"""Check one named behavioral red/green pair; never credit collection errors."""

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

HARNESS_FAILURES = (
    "ImportError",
    "ModuleNotFoundError",
    "SyntaxError",
    "ConftestImportFailure",
    "collected 0 items",
)


def named_case(path: Path, name: str) -> ET.Element:
    cases = [
        case
        for case in ET.parse(path).iter("testcase")
        if f"{case.get('classname')}::{case.get('name')}" == name
    ]
    if len(cases) != 1:
        raise ValueError(
            f"{path}: expected exactly one case named {name}, found {len(cases)}"
        )
    return cases[0]


def check(red: Path, green: Path, name: str, expected: str) -> None:
    old, fixed = named_case(red, name), named_case(green, name)
    failure = old.find("failure")
    if (
        old.find("error") is not None
        or old.find("skipped") is not None
        or failure is None
    ):
        raise ValueError(
            "old implementation must have a behavioral failure, not an error/skip/pass"
        )
    diagnostic = failure.get("message", "") + "\n" + (failure.text or "")
    if not expected.strip() or expected not in diagnostic:
        raise ValueError(
            "old failure does not contain the declared behavioral signature"
        )
    if any(marker in diagnostic for marker in HARNESS_FAILURES):
        raise ValueError(
            "import/compilation/collection failure is not behavioral red evidence"
        )
    if any(fixed.find(tag) is not None for tag in ("failure", "error", "skipped")):
        raise ValueError("fixed implementation must pass the same named case")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--red", type=Path, required=True)
    parser.add_argument("--green", type=Path, required=True)
    parser.add_argument(
        "--test",
        required=True,
        help="JUnit classname::name (including parameter suffix)",
    )
    parser.add_argument(
        "--expected", required=True, help="distinct expected behavioral diagnostic"
    )
    args = parser.parse_args()
    try:
        check(args.red, args.green, args.test, args.expected)
    except (ValueError, ET.ParseError, OSError) as exc:
        parser.exit(1, f"Regression evidence rejected: {exc}\n")
    print(
        f"Behavioral red/green observed: {args.test}; inspect full reports and source receipts before acceptance"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
