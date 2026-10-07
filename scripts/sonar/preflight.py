#!/usr/bin/env python3
"""Fail-closed, stdlib-only Sonar report validation and evidence binding.

Run ``stamp`` in each report-producing job immediately after successful tests.
Run the default command before scanning, and ``verify`` immediately before and
after scanning. A stamp binds bytes to a checkout; it is not a signature or proof
that a test command ran. CI must generate reports afresh in the stamped checkout.
No coverage percentages or success statuses are manufactured for missing files.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path, PurePosixPath


class EvidenceError(ValueError):
    """Input cannot be used as coverage evidence."""


SCHEMA = 1
MAX_REPORT_BYTES = 100 * 1024 * 1024
JS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
UNSUPPORTED_SUFFIXES = {".rs", ".sh", ".bash", ".ps1", ".sql", ".go", ".java", ".c", ".h", ".cpp", ".j2"}
CONFIG_SUFFIXES = {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".properties"}
TEST_PATTERNS = (
    "**/tests/**", "**/test/**", "**/*.test.*", "**/*.spec.*",
    "**/test_*.py", "**/*_test.py", "**/conftest.py", "client/e2e/**",
)
# These are repository-specific reviewed non-authored paths. Scanner exclusions
# are never themselves used to decide that an authored file is generated.
GENERATED_PATTERNS = (
    "**/node_modules/**", "**/.venv/**", "**/venv/**", "**/__pycache__/**",
    "**/dist/**", "**/build/**", "**/coverage/**", ".sonar-evidence/**",
    "client/src/lib/v1.d.ts", ".agents/skills/**", "plugins/bifrost/skills/**",
)
SYMLINK_ALIASES = {
    "skills/build": "../.claude/skills/bifrost-build",
    "skills/setup": "../.claude/skills/bifrost-setup",
    "skills/migrate": "../.claude/skills/bifrost-migrate",
    "skills/copilot-cowork-package": "../.claude/skills/bifrost-copilot-cowork-package",
}
CONFIG_FILES = {
    "sonar-project.properties", ".sonarcloud.properties", "pyproject.toml",
    "api/pyproject.toml", "api/.coveragerc", "api/.coveragerc.sonar",
    "client/vitest.config.ts", "client/vitest.sonar.config.ts",
    "client/package.json", "client/package-lock.json", "test.sh",
    "docker-compose.test.yml",
}


def fail(message: str) -> None:
    raise EvidenceError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def git_arguments(args: tuple[str, ...]) -> list[str]:
    fixed = {
        ("rev-parse", "HEAD"): ["rev-parse", "HEAD"],
        ("diff", "--name-only", "HEAD", "--"): ["diff", "--name-only", "HEAD", "--"],
        ("ls-files", "-z"): ["ls-files", "-z"],
    }
    if args in fixed:
        command = fixed[args]
    elif len(args) == 3 and args[:2] == ("rev-parse", "--verify"):
        match = re.fullmatch(r"([0-9a-f]{40})\^\{commit\}", args[2])
        if not match:
            fail("Git evidence requires an exact commit identity")
        command = ["rev-parse", "--verify", "--end-of-options", match.group(1) + "^{commit}"]
    else:
        operations = {
            "merge-base": (["merge-base"], 1),
            "diff": (["diff", "--no-renames", "--name-only", "--diff-filter=ACMT", "-z"], 5),
        }
        operation = operations.get(args[0]) if args else None
        if operation is None:
            fail("Git evidence accepts only fixed read-only operations")
        prefix, count = operation
        if args[:count] != tuple(prefix) or len(args) != count + 2:
            fail("Git evidence accepts only fixed read-only operation signatures")
        command = list(prefix)
        for operand in args[count:]:
            match = re.fullmatch(r"[0-9a-f]{40}", operand)
            if not match:
                fail("Git evidence requires exact commit identities")
            command.append(match.group(0))
    return command


def git(root: Path, *args: str) -> bytes:
    command = git_arguments(args)
    root = root.resolve(strict=True)
    if not root.is_dir():
        fail("Git evidence root is not a directory")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(root), *command], check=False,
                            capture_output=True, env=environment)
    if result.returncode:
        fail(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def validate_checkout(root: Path, head: str, base: str) -> None:
    for label, value in (("head", head), ("base", base)):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            fail(f"{label} must be an exact, lowercase 40-character commit SHA")
        resolved = git(root, "rev-parse", "--verify", f"{value}^{{commit}}").decode().strip()
        if resolved != value:
            fail(f"{label} does not resolve to the requested commit")
    actual = git(root, "rev-parse", "HEAD").decode().strip()
    if actual != head:
        fail(f"head SHA mismatch: requested {head}, checkout {actual}")
    # A PR's event base can advance independently of its head. Preserve the
    # exact event base identity, but compare authored changes from merge-base.
    git(root, "merge-base", base, head)
    if git(root, "diff", "--name-only", "HEAD", "--").strip():
        fail("tracked working tree or index differs from the exact head SHA")


def glob_match(path: str, pattern: str) -> bool:
    """Sonar's path globs: *, ** and ?; **/ includes zero directories."""
    out = []
    index = 0
    while index < len(pattern):
        if pattern[index:index + 3] == "**/":
            out.append("(?:.*/)?")
            index += 3
        elif pattern[index:index + 2] == "**":
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch("".join(out), path) is not None


def matches(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(glob_match(path, pattern) for pattern in patterns)


def classification(path: str) -> tuple[str, str]:
    suffix = PurePosixPath(path).suffix.lower()
    language = "python" if suffix == ".py" else "javascript_typescript" if suffix in JS_SUFFIXES else "unsupported"
    if matches(path, GENERATED_PATTERNS):
        return "generated_or_vendor", language
    if language != "unsupported" and matches(path, TEST_PATTERNS):
        return "test", language
    if path.endswith((".d.ts", ".d.mts", ".d.cts")):
        return "declaration", language
    if language != "unsupported":
        return "authored", language
    if suffix in UNSUPPORTED_SUFFIXES:
        return "unsupported_code_requires_review", suffix.lstrip(".")
    if suffix in CONFIG_SUFFIXES or PurePosixPath(path).name.startswith(("Dockerfile", ".env", ".coveragerc")):
        return "configuration_requires_review", "configuration"
    return "other_not_coverage_evidence", "other"


def safe_relative(path: str) -> str:
    if not path or "\\" in path or any(ord(c) < 32 for c in path):
        fail(f"invalid report/source path: {path!r}")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        fail(f"expected repository-relative path: {path!r}")
    parts = path.split("/")
    if ".." in parts or any(part == "" for part in parts):
        fail(f"path traversal or empty path component: {path!r}")
    cleaned = "/".join(part for part in parts if part != ".")
    if not cleaned:
        fail(f"empty source path: {path!r}")
    return cleaned


def source_bytes(root: Path, path: str) -> bytes:
    safe_relative(path)
    full = root / path
    if full.is_symlink() or root.resolve() not in full.resolve().parents:
        fail(f"source symlink or repository escape: {path}")
    try:
        return full.read_bytes()
    except OSError as exc:
        fail(f"missing/unreadable tracked source {path}: {exc}")


def inventory(root: Path, base: str, head: str) -> list[dict]:
    comparison_base = git(root, "merge-base", base, head).decode().strip()
    changed = set(git(root, "diff", "--no-renames", "--name-only", "--diff-filter=ACMT", "-z", comparison_base, head).decode().split("\0"))
    paths = sorted(filter(None, git(root, "ls-files", "-z").decode().split("\0")))
    result = []
    for path in paths:
        category, language = classification(path)
        if (root / path).is_symlink() and path in SYMLINK_ALIASES:
            target = os.readlink(root / path)
            if target != SYMLINK_ALIASES[path]:
                fail(f"unreviewed source alias target: {path}")
            data = target.encode()
            category, language = "generated_or_vendor", "directory_alias"
        else:
            data = source_bytes(root, path)
        result.append({"path": path, "classification": category, "language": language,
                       "sha256": sha256(data), "line_count": len(data.splitlines()), "changed": path in changed})
    return result


def inventory_digest(items: list[dict]) -> str:
    return sha256(json_bytes([{key: item[key] for key in ("path", "sha256")} for item in items]))


def config_hashes(items: list[dict], properties: str) -> dict[str, str]:
    return {item["path"]: item["sha256"] for item in items if (
        item["path"] in CONFIG_FILES | {properties}
        or item["path"].startswith((".github/workflows/", "scripts/sonar/"))
        or item["classification"] == "configuration_requires_review"
    )}


def read_properties(data: bytes) -> dict[str, str]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError:
        fail("scanner properties must be UTF-8")
    props = {}
    pending = ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        pending += line
        if pending.endswith("\\"):
            pending = pending[:-1]
            continue
        if "=" not in pending:
            fail("unsupported scanner property syntax; use explicit key=value entries")
        key, value = (part.strip() for part in pending.split("=", 1))
        if key in props:
            fail(f"duplicate scanner property: {key}")
        if "\\" in key + value:
            fail(f"escaped scanner properties are not supported: {key}")
        props[key] = value
        pending = ""
    if pending:
        fail("unterminated scanner properties continuation")
    return props


def property_patterns(props: dict[str, str], key: str) -> list[str]:
    return [value.strip() for value in props.get(key, "").split(",") if value.strip()]


def validate_scope(items: list[dict], props: dict[str, str]) -> None:
    if props.get("sonar.projectKey") != "Midtown-Technology-Group_bifrost":
        fail("sonar.projectKey must remain the approved Bifrost project")
    if props.get("sonar.host.url") != "https://sonarcloud.io":
        fail("sonar.host.url must remain the approved HTTPS SonarQube Cloud host")
    if props.get("sonar.sources") != "." or props.get("sonar.tests") != ".":
        fail("scanner scope must use sonar.sources=. and sonar.tests=.")
    if props.get("sonar.scm.exclusions.disabled") != "true":
        fail("sonar.scm.exclusions.disabled=true is required to make tracked-file scope explicit")
    for key in ("sonar.python.file.suffixes", "sonar.javascript.file.suffixes", "sonar.typescript.file.suffixes"):
        if key in props:
            fail(f"custom language suffixes require review: {key}")
    for item in items:
        path, category = item["path"], item["classification"]
        if category not in {"authored", "test", "declaration"}:
            continue
        is_test = matches(path, property_patterns(props, "sonar.test.inclusions"))
        excluded = matches(path, property_patterns(props, "sonar.exclusions"))
        included = property_patterns(props, "sonar.inclusions")
        coverage_excluded = matches(path, property_patterns(props, "sonar.coverage.exclusions"))
        if category == "test":
            if not is_test or not excluded or matches(path, property_patterns(props, "sonar.test.exclusions")):
                fail(f"test is missing from scanner test scope or also indexed as source: {path}")
        elif is_test or excluded or (included and not matches(path, included)) or coverage_excluded:
            fail(f"authored source excluded or misclassified by scanner properties: {path}")


def normalize_path(raw: str, root: Path, tracked: dict[str, dict], kind: str,
                   sources: tuple[str, ...] = ()) -> str:
    """Resolve only explicit checkout/container roots and existing tracked files."""
    if not raw or "\\" in raw or ".." in raw.split("/") or any(ord(c) < 32 for c in raw):
        fail(f"unsafe report source path: {raw!r}")

    def mapped(value: str) -> str | None:
        if value.startswith(str(root.resolve()) + "/"):
            return safe_relative(value[len(str(root.resolve())) + 1:])
        if value.startswith("/repo/"):
            return safe_relative(value[len("/repo/"):])
        if value.startswith("/app/doc_renderer_service/"):
            return safe_relative(value[len("/app/"):])
        if value.startswith("/app/"):
            return "api/" + safe_relative(value[len("/app/"):])
        if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
            return None
        return safe_relative(value)

    possibilities = set()
    initial = mapped(raw)
    if initial:
        possibilities.add(initial)
    if not raw.startswith("/"):
        if kind == "client":
            possibilities.add("client/" + safe_relative(raw))
        if kind == "python":
            for source in sources:
                if ".." in source.split("/") or "\\" in source:
                    fail(f"unsafe Cobertura source root: {source!r}")
                candidate = mapped(source.rstrip("/") + "/" + raw) if source not in {"", "."} else mapped(raw)
                if candidate:
                    possibilities.add(candidate)
    found = sorted(path for path in possibilities if path in tracked)
    if len(found) != 1:
        fail(f"unknown, stale, or ambiguous {kind} source path {raw!r}: {found}")
    path = found[0]
    expected = "python" if kind == "python" else "javascript_typescript"
    if tracked[path]["language"] != expected:
        fail(f"wrong source language in {kind} report: {path}")
    if kind == "client" and not path.startswith("client/"):
        fail(f"non-client source in client report: {path}")
    # Native Node tests legitimately import client/e2e support modules and
    # client/playwright.config.ts; only duplicate files across reports fail.
    source_bytes(root, path)
    return path


def natural(value: str | None, label: str, minimum: int = 0) -> int:
    if value is None or not re.fullmatch(r"[0-9]+", value):
        fail(f"invalid integer for {label}: {value!r}")
    parsed = int(value)
    if parsed < minimum:
        fail(f"{label} must be at least {minimum}")
    return parsed


def valid_line(value: str | None, item: dict, label: str) -> int:
    line = natural(value, label, 1)
    if line > item["line_count"]:
        fail(f"report line {line} outside source {item['path']} ({item['line_count']} lines)")
    return line


def validate_rate(value: str | None, label: str, numerator: int | None = None,
                  denominator: int | None = None) -> None:
    if value is None:
        return
    if not re.fullmatch(r"(?:0(?:\.[0-9]+)?|1(?:\.0+)?)", value):
        fail(f"invalid coverage rate {label}: {value!r}")
    if denominator and numerator is not None and abs(float(value) - numerator / denominator) > 0.0001:
        fail(f"coverage rate {label} disagrees with its line/branch records")


def report_bytes(path: Path, root: Path) -> bytes:
    path = bounded_report_path(path, root)
    try:
        size = path.stat().st_size
        if not 0 < size <= MAX_REPORT_BYTES:
            fail(f"empty or oversized report: {path}")
        data = path.read_bytes()
    except OSError as exc:
        fail(f"missing/unreadable report {path}: {exc}")
    if not data.strip():
        fail(f"empty report: {path}")
    return data


def bounded_report_path(path: Path, root: Path) -> Path:
    """Coverage input/output belongs to this checkout, never an arbitrary path."""
    root = root.resolve(strict=True)
    if ".." in path.parts or path.is_symlink():
        fail("Report path cannot traverse or name a symlink")
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        fail("Report path is outside the current checkout")
    return resolved


def parse_python(data: bytes, root: Path, tracked: dict[str, dict]) -> tuple[bytes, dict[str, dict]]:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        fail("DTD/entity declarations are not accepted in coverage XML")
    try:
        document = ET.fromstring(data)
    except ET.ParseError as exc:
        fail(f"malformed Python Cobertura XML: {exc}")
    if document.tag != "coverage":
        fail("Python report must be Cobertura <coverage> XML")
    for element in document.iter():
        validate_rate(element.get("line-rate"), "line-rate")
        validate_rate(element.get("branch-rate"), "branch-rate")
    sources = tuple(node.text or "" for node in document.findall("./sources/source"))
    results = {}
    totals = {"lines-valid": 0, "lines-covered": 0, "branches-valid": 0, "branches-covered": 0}
    for node in document.findall("./packages/package/classes/class"):
        path = normalize_path(node.get("filename", ""), root, tracked, "python", sources)
        if path in results:
            fail(f"duplicate Cobertura class source: {path}")
        node.set("filename", path)
        # Method-level lines are redundant with class-level coverage, but must
        # still be structurally valid if a producer includes them.
        for method_line in node.findall("./methods/method/lines/line"):
            valid_line(method_line.get("number"), tracked[path], "Python method line")
            natural(method_line.get("hits"), "Python method hits")
        lines = {}
        branches = branches_hit = 0
        for entry in node.findall("./lines/line"):
            number = valid_line(entry.get("number"), tracked[path], "Python line number")
            hits = natural(entry.get("hits"), "Python line hits")
            if number in lines:
                fail(f"duplicate Python line {path}:{number}")
            lines[number] = hits
            branch = entry.get("branch", "false")
            condition = entry.get("condition-coverage")
            if branch not in {"true", "false"}:
                fail(f"invalid Python branch flag: {path}:{number}")
            if branch == "true":
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)% \(([0-9]+)/([0-9]+)\)", condition or "")
                if not match:
                    fail(f"missing/invalid Python condition-coverage: {path}:{number}")
                percent, hit, count = float(match[1]), int(match[2]), int(match[3])
                if count < 1 or hit > count or percent > 100 or abs(percent - hit / count * 100) >= 1.01:
                    fail(f"inconsistent Python branch counts: {path}:{number}")
                if hit and not hits:
                    fail(f"hit Python branch on unhit line: {path}:{number}")
                branches += count
                branches_hit += hit
            elif condition is not None:
                fail(f"condition-coverage on non-branch line: {path}:{number}")
            conditions = entry.findall("./conditions/condition")
            if conditions and branch != "true":
                fail(f"branch conditions on non-branch line: {path}:{number}")
            condition_ids = set()
            for branch_condition in conditions:
                identifier = natural(branch_condition.get("number"), "Python condition identifier")
                percent = branch_condition.get("coverage", "")
                if identifier in condition_ids or not branch_condition.get("type"):
                    fail(f"duplicate/malformed Python condition: {path}:{number}")
                condition_ids.add(identifier)
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%", percent) or float(percent[:-1]) > 100:
                    fail(f"invalid Python condition percentage: {path}:{number}")
        covered = sum(count > 0 for count in lines.values())
        validate_rate(node.get("line-rate"), f"{path} line-rate", covered, len(lines))
        validate_rate(node.get("branch-rate"), f"{path} branch-rate", branches_hit, branches)
        results[path] = {"lines": len(lines), "lines_hit": covered, "branches": branches, "branches_hit": branches_hit}
        for key, value in zip(totals, (len(lines), covered, branches, branches_hit)):
            totals[key] += value
    if not results or not totals["lines-valid"]:
        fail("Python report contains no executable line evidence")
    for key, count in totals.items():
        if natural(document.get(key), f"Cobertura {key}") != count:
            fail(f"Cobertura {key} total does not match its line records")
    validate_rate(document.get("line-rate"), "root line-rate", totals["lines-covered"], totals["lines-valid"])
    validate_rate(document.get("branch-rate"), "root branch-rate", totals["branches-covered"], totals["branches-valid"])
    source_node = document.find("sources")
    if source_node is None:
        source_node = ET.SubElement(document, "sources")
    source_node.clear()
    ET.SubElement(source_node, "source").text = "."
    ET.indent(document)
    return ET.tostring(document, encoding="utf-8", xml_declaration=True) + b"\n", results


def parse_lcov(data: bytes, root: Path, tracked: dict[str, dict], kind: str) -> tuple[bytes, dict[str, dict]]:
    try:
        rows = data.decode("utf-8").splitlines()
    except UnicodeError:
        fail(f"{kind} LCOV report must be UTF-8")
    results, normalized = {}, []
    path = None
    fields, lines, branches, functions, function_hits = {}, {}, {}, [], []
    for row in rows:
        if not row:
            continue
        if row.startswith("TN:") and path is None:
            normalized.append(row)
            continue
        if row.startswith("SF:"):
            if path is not None:
                fail(f"{kind} LCOV missing end_of_record")
            path = normalize_path(row[3:], root, tracked, kind)
            if path in results:
                fail(f"duplicate {kind} LCOV source: {path}")
            fields, lines, branches, functions, function_hits = {}, {}, {}, [], []
            normalized.append("SF:" + path)
            continue
        if path is None:
            fail(f"{kind} LCOV field outside source record: {row}")
        if row == "end_of_record":
            expected = {"LF": len(lines), "LH": sum(hit > 0 for hit in lines.values()),
                        "BRF": len(branches), "BRH": sum(hit > 0 for hit in branches.values()),
                        "FNF": len(functions), "FNH": sum(hit > 0 for _, hit in function_hits)}
            for key, value in expected.items():
                if key not in fields or fields[key] != value:
                    fail(f"{kind} LCOV {key} count missing/inconsistent for {path}")
            if Counter(name for _, name in functions) != Counter(name for name, _ in function_hits):
                fail(f"{kind} LCOV function definitions/hits disagree: {path}")
            # Sourcemapped V8 reports can put BRDA on a different source line
            # from DA. Both must be in bounds; an exact DA match is not required.
            results[path] = {"lines": len(lines), "lines_hit": expected["LH"],
                             "branches": len(branches), "branches_hit": expected["BRH"]}
            normalized.append(row)
            path = None
            continue
        key, separator, value = row.partition(":")
        if not separator:
            fail(f"malformed {kind} LCOV row: {row}")
        if key in {"LF", "LH", "BRF", "BRH", "FNF", "FNH"}:
            if key in fields:
                fail(f"duplicate {kind} LCOV {key} field")
            fields[key] = natural(value, key)
        elif key == "DA":
            parts = value.split(",")
            if len(parts) not in {2, 3}:
                fail(f"malformed {kind} LCOV DA: {row}")
            number = valid_line(parts[0], tracked[path], "LCOV DA line")
            if number in lines:
                fail(f"duplicate {kind} LCOV DA line: {path}:{number}")
            lines[number] = natural(parts[1], "LCOV DA hits")
            if len(parts) == 3 and not re.fullmatch(r"[0-9a-fA-F]{32}", parts[2]):
                fail("LCOV line checksum must be MD5 hexadecimal when present")
        elif key == "BRDA":
            parts = value.split(",")
            if len(parts) != 4:
                fail(f"malformed {kind} LCOV BRDA: {row}")
            number = valid_line(parts[0], tracked[path], "LCOV branch line")
            branch = (number, natural(parts[1], "LCOV branch block"), natural(parts[2], "LCOV branch identifier"))
            if branch in branches:
                fail(f"duplicate {kind} LCOV branch: {path}:{branch}")
            branches[branch] = 0 if parts[3] == "-" else natural(parts[3], "LCOV branch hits")
        elif key == "FN":
            parts = value.split(",", 2)
            if len(parts) < 2:
                fail(f"malformed LCOV function: {row}")
            start = valid_line(parts[0], tracked[path], "LCOV function line")
            if len(parts) == 3 and parts[1].isdigit():
                end = valid_line(parts[1], tracked[path], "LCOV function end")
                if end < start:
                    fail("LCOV function ends before its start")
                name = parts[2]
            else:
                name = ",".join(parts[1:])
            if not name or (start, name) in functions:
                fail(f"empty/duplicate LCOV function definition: {name!r}")
            functions.append((start, name))
        elif key == "FNDA":
            hit, sep, name = value.partition(",")
            if not sep or not name:
                fail(f"malformed LCOV function hits: {row}")
            function_hits.append((name, natural(hit, "LCOV function hits")))
        else:
            fail(f"unsupported {kind} LCOV field: {key}")
        normalized.append(row)
    if path is not None:
        fail(f"{kind} LCOV missing final end_of_record")
    if not results or not sum(item["lines"] for item in results.values()):
        fail(f"{kind} LCOV contains no executable line evidence")
    return ("\n".join(normalized) + "\n").encode(), results


def has_executable_source(data: bytes, language: str) -> bool:
    if language == "python":
        try:
            body = ast.parse(data).body
        except (SyntaxError, UnicodeError) as exc:
            fail(f"cannot establish Python executable source: {exc}")
        return any(not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                       and isinstance(node.value.value, str)) for node in body)
    # Deliberately conservative: .d.ts is classified separately. Other TS may
    # require a measured zero-line record and manual review; no guessed erasure.
    return bool(data.strip())


def complete_inventory(items: list[dict], reports: dict[str, dict], root: Path) -> list[str]:
    review = []
    for item in items:
        path, category = item["path"], item["classification"]
        if category == "authored":
            stats = reports.get(path)
            if stats and stats["lines"]:
                item["coverage_state"] = "reported_with_hits" if stats["lines_hit"] else "reported_zero_hits"
                item["coverage"] = stats
            elif has_executable_source(source_bytes(root, path), item["language"]):
                item["coverage_state"] = "missing_report_uncovered"
                if item["changed"]:
                    fail(f"changed authored executable source omitted from reports: {path}")
            else:
                item["coverage_state"] = "no_executable_lines"
        elif category == "declaration":
            item["coverage_state"] = "declaration_no_runtime_coverage"
        elif category == "test":
            item["coverage_state"] = "test_code_not_source_coverage"
        else:
            item["coverage_state"] = "not_measured_by_this_coverage_gate"
        if item["changed"] and category in {
            "unsupported_code_requires_review", "configuration_requires_review",
            "declaration", "generated_or_vendor",
        }:
            review.append(path)
    return review


def read_json(path: Path, root: Path) -> dict:
    try:
        value = json.loads(report_bytes(path, root))
    except (OSError, ValueError) as exc:
        fail(f"missing/malformed evidence {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"evidence must be a JSON object: {path}")
    return value


def stamp_report(root: Path, head: str, base: str, report: Path, properties: str) -> Path:
    validate_checkout(root, head, base)
    items = inventory(root, base, head)
    if properties not in {item["path"] for item in items}:
        fail("scanner properties must be tracked")
    report = bounded_report_path(report, root)
    data = report_bytes(report, root)
    stamp = {"schema": SCHEMA, "status": "report_produced", "head": head, "base": base,
             "merge_base": git(root, "merge-base", base, head).decode().strip(),
             "report_sha256": sha256(data), "source_inventory_sha256": inventory_digest(items),
             "config_sha256": config_hashes(items, properties)}
    target = bounded_report_path(Path(str(report) + ".provenance.json"), root)
    target.write_bytes(json_bytes(stamp))
    return target


def check_stamp(report: Path, data: bytes, head: str, base: str, merge_base: str,
                digest: str, configs: dict[str, str], root: Path) -> str:
    stamp_path = Path(str(report) + ".provenance.json")
    stamp = read_json(stamp_path, root)
    expected = {"schema": SCHEMA, "status": "report_produced", "head": head, "base": base,
                "merge_base": merge_base,
                "report_sha256": sha256(data), "source_inventory_sha256": digest, "config_sha256": configs}
    if stamp != expected:
        fail(f"report provenance/SHA/config mismatch: {report}")
    return sha256(report_bytes(stamp_path, root))


def preflight(root: Path, head: str, base: str, reports: dict[str, Path],
              output: Path, properties: str) -> dict:
    validate_checkout(root, head, base)
    items = inventory(root, base, head)
    tracked = {item["path"]: item for item in items}
    if properties not in tracked:
        fail("scanner properties must be tracked")
    props = read_properties(source_bytes(root, properties))
    validate_scope(items, props)
    digest, configs = inventory_digest(items), config_hashes(items, properties)
    merge_base = git(root, "merge-base", base, head).decode().strip()
    if set(reports) != {"python", "client", "node"}:
        fail("exactly Python, client and Node reports are required")
    output = output.resolve()
    if root.resolve() not in output.parents:
        fail("evidence directory must be inside the checkout")
    output_rel = output.relative_to(root.resolve()).as_posix()
    if any(path == output_rel or path.startswith(output_rel + "/") for path in tracked):
        fail("evidence output directory may not contain tracked files")
    for filename in ("python.xml", "client.lcov", "node.lcov", "manifest.json"):
        if (output / filename).is_symlink():
            fail(f"evidence output may not overwrite a symlink: {filename}")
    expected_paths = {"sonar.python.coverage.reportPaths": f"{output_rel}/python.xml",
                      "sonar.javascript.lcov.reportPaths": f"{output_rel}/client.lcov,{output_rel}/node.lcov"}
    for key, expected in expected_paths.items():
        if props.get(key) != expected:
            fail(f"{key} must point only to validated outputs: {expected}")
    parsed, measurements, metadata = {}, {}, {}
    for kind in ("python", "client", "node"):
        data = report_bytes(reports[kind], root)
        stamp_hash = check_stamp(reports[kind], data, head, base, merge_base, digest, configs, root)
        normalized, stats = parse_python(data, root, tracked) if kind == "python" else parse_lcov(data, root, tracked, kind)
        overlap = set(measurements) & set(stats)
        if overlap:
            fail(f"source represented in multiple report inputs: {sorted(overlap)}")
        measurements.update(stats)
        filename = "python.xml" if kind == "python" else f"{kind}.lcov"
        parsed[filename] = normalized
        metadata[kind] = {"input_sha256": sha256(data), "provenance_sha256": stamp_hash,
                          "normalized_path": f"{output_rel}/{filename}", "normalized_sha256": sha256(normalized),
                          "source_files": len(stats), "lines": sum(item["lines"] for item in stats.values())}
    review = complete_inventory(items, measurements, root)
    omitted = [item["path"] for item in items if item["coverage_state"] == "missing_report_uncovered"]
    manifest = {"schema": SCHEMA, "status": "validated_for_scan", "head": head, "base": base,
                "merge_base": merge_base,
                "properties": properties, "source_inventory_sha256": digest, "config_sha256": configs,
                "reports": metadata, "inventory": items,
                "unreported_authored_files": omitted, "changed_paths_requiring_human_review": review,
                "coverage_completeness": "incomplete" if omitted else "all_python_js_ts_runtime_files_reported",
                "sonar_quality_gate": "not_observed", "security_hotspot_review": "not_observed",
                "limitations": ["Report presence and valid bytes do not establish test success or full line coverage.",
                                "Unsupported languages, configuration, declarations, and generated/vendor/alias changes require human review.",
                                "Producer stamps bind report bytes and source identity, not trusted execution attestation."]}
    output.mkdir(parents=True, exist_ok=True)
    for filename, data in parsed.items():
        (output / filename).write_bytes(data)
    (output / "manifest.json").write_bytes(json_bytes(manifest))
    return manifest


def verify(root: Path, head: str, base: str, output: Path, manifest_sha256: str) -> dict:
    validate_checkout(root, head, base)
    manifest_path = output / "manifest.json"
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        fail("expected manifest SHA256 must be supplied from the evidence job output")
    if sha256(report_bytes(manifest_path, root)) != manifest_sha256:
        fail("manifest hash does not match the evidence job output")
    manifest = read_json(manifest_path, root)
    if manifest.get("schema") != SCHEMA or manifest.get("status") != "validated_for_scan":
        fail("manifest status/schema is not valid evidence")
    if manifest.get("head") != head or manifest.get("base") != base:
        fail("manifest exact head/base SHA mismatch")
    if manifest.get("merge_base") != git(root, "merge-base", base, head).decode().strip():
        fail("manifest comparison merge-base mismatch")
    items = inventory(root, base, head)
    if inventory_digest(items) != manifest.get("source_inventory_sha256"):
        fail("source inventory changed since preflight")
    properties = manifest.get("properties")
    if not isinstance(properties, str) or config_hashes(items, properties) != manifest.get("config_sha256"):
        fail("configuration changed since preflight")
    validate_scope(items, read_properties(source_bytes(root, properties)))
    reports = manifest.get("reports", {})
    if set(reports) != {"python", "client", "node"}:
        fail("manifest does not contain all three report kinds")
    for kind, report in reports.items():
        expected = (output / ("python.xml" if kind == "python" else f"{kind}.lcov")).resolve()
        path = root / safe_relative(report.get("normalized_path", ""))
        if path.resolve() != expected or sha256(report_bytes(path, root)) != report.get("normalized_sha256"):
            fail(f"normalized report path/content changed since preflight: {kind}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv.pop(0) if argv and argv[0] in {"stamp", "verify"} else "preflight"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--properties", default="sonar-project.properties")
    parser.add_argument("--output-dir", type=Path, default=Path(".sonar-evidence"))
    if command == "stamp":
        parser.add_argument("--report", type=Path, required=True)
    elif command == "verify":
        parser.add_argument("--manifest-sha256", required=True)
    elif command == "preflight":
        for kind in ("python", "client", "node"):
            parser.add_argument("--" + kind, type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    try:
        if command == "stamp":
            result = stamp_report(root, args.head, args.base, args.report, args.properties)
            print(f"Stamped report provenance: {result}")
        elif command == "verify":
            verify(root, args.head, args.base, output, args.manifest_sha256)
            print("Verified exact source/config/report identity; Sonar gate status is separate.")
        else:
            manifest = preflight(root, args.head, args.base,
                                 {kind: getattr(args, kind) for kind in ("python", "client", "node")}, output, args.properties)
            print(f"Validated coverage inputs for {args.head}; {len(manifest['unreported_authored_files'])} authored files remain unreported; "
                  f"{len(manifest['changed_paths_requiring_human_review'])} changed paths require human review.")
        return 0
    except (EvidenceError, OSError) as exc:
        print(f"Sonar evidence rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
