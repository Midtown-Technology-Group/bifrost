# Repository skill discovery

Edit `.claude/skills/` as the canonical source. Keep descriptions specific and
at most 35 words; full procedures and optional references remain in the skill
body and supporting files. Preserve the source's unprefixed skill name: the
plugin harness applies its own namespace.

Run `scripts/sync-codex-skills.sh` after source edits. It generates:

- `.agents/skills/`: all canonical skills, the single repository-local discovery
  root for Codex and other compatible harnesses.
- `plugins/bifrost/skills/`: public plugin skills selected by the `skills/`
  symlinks. This remains the installed plugin distribution surface.

Do not restore `.codex/skills/` copies. Older checkouts discovered the same
maintainer skill from both roots, while `.agents` copies could become stale.
The host-side `scripts/check_skill_mirrors.py` gate now rejects legacy copies,
duplicate local IDs, repeated namespaces, overlong descriptions, and mirror
drift. Run `python3 -m unittest scripts.test_skill_mirrors` for guard regressions.

The source and mirrors retain complete procedure bodies and relative reference
files. Description trimming changes discovery text only. Explicit requests
still load the selected full procedure. Repository guidance and user-level
skills remain separate sources of context.

For old remote branches, preserve their Git state. A runtime-only disabled-skill
entry can hide an inspected duplicate without deleting files or changing the
branch. Update to merged source through the normal repository workflow when
that checkout is ready. Never copy changed skill trees over unrelated dirty
work or reset a branch merely to update discovery.
