# Advisory workflow scanner pilot

The `Workflow Security (Advisory)` workflow runs Zizmor on workflow and local
action definitions when their source changes on a PR or on `main`. Maintainers
can also dispatch it manually. It is independent of the existing CI and
required checks; it does not change branch protection or merge authorization.

Zizmor 1.30.1 is pinned to its official Linux x86-64 release and SHA-256 digest
(`e65324f4430c2717591937edcec90ccbefaf14c174f8ec9415e03ca875b46e1a`). The digest
was read from the [official release](https://github.com/zizmorcore/zizmor/releases/tag/v1.30.1).
Updates require reviewing and changing both the version and digest.

The pilot runs on a GitHub-hosted runner with `contents: read`, no persisted
checkout credentials, no repository secrets, no SARIF upload permission and no
PR comments. It uses `pull_request`, checks out the exact PR head, and never
uses `pull_request_target`. It statically reads definitions without executing
their referenced actions or run blocks. Offline mode avoids GitHub API access
and omits online-only audits. See [Zizmor usage](https://docs.zizmor.sh/usage/).

SARIF findings do not fail the job. Download, checksum, collection, scanner and
report errors do fail it so missing evidence cannot look like a clean audit.
No `continue-on-error`, baseline suppression or automatic fixes are used.

Each successful run publishes a count summary and a 14-day
`workflow-security-<head SHA>` artifact containing `results.sarif`,
`zizmor.stderr`, `metadata.json` and `summary.md`. Metadata records the exact
scanned head, scanner version and offline scope. A failed run may have only
partial diagnostic artifacts; absence of a successful report is not zero
findings. These artifacts are advisory evidence, not contribution admission,
code-review approval or security certification.

Review the SARIF locations and surrounding workflow before deciding whether a
finding is actionable. Use private vulnerability reporting for sensitive
findings, following `SECURITY.md`; do not paste exploit details into public
issues. Separate fixes and justified per-finding exceptions should be reviewed
on their own. Poutine and any required scanner gate remain follow-up decisions.

Verify report behavior with
`python3 -m unittest scripts.test_workflow_security_report`. Before publishing,
run the normal clean-candidate pre-PR gate and comprehensive CI because workflow
changes are broad-impact inputs. The scanner workflow also runs its report
contracts. No platform runtime or UI behavior changes in this pilot.
