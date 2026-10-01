#!/usr/bin/env python3
"""List or read exact level-two guidance sections without loading whole playbooks."""
import argparse
from pathlib import Path
import re


def sections(text):
    lines = text.splitlines(keepends=True)
    starts = []
    fence = None
    for index, line in enumerate(lines):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None and line.startswith("## "):
            starts.append((index, line[3:].strip()))
    return [(title, start + 1, "".join(lines[start:end]))
            for (start, title), end in zip(starts, [s for s, _ in starts[1:]] + [len(lines)])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", choices=["platform", "claude"])
    parser.add_argument("headings", nargs="*", help="Exact headings; omit to list")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    path = root / ("docs/dev/agent-platform-rules.md" if args.file == "platform" else "CLAUDE.md")
    entries = sections(path.read_text(encoding="utf-8"))
    if not args.headings:
        for heading, line, _ in entries:
            print(f"{line}: {heading}")
        return
    known = {heading for heading, _, _ in entries}
    missing = set(args.headings) - known
    if missing:
        parser.error("Unknown headings: " + ", ".join(sorted(missing)))
    for heading, _, content in entries:
        if heading in args.headings:
            print(content, end="" if content.endswith("\n") else "\n")


if __name__ == "__main__":
    main()
