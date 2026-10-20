#!/usr/bin/env python3
"""Prepare and publish reviewed MTG releases; never select a deployment target.

Git is the release interval authority. GitHub supplies complete PR metadata,
review and CI provenance. All failures precede mutation wherever possible.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

REPOSITORY = "Midtown-Technology-Group/bifrost"
BRANCH = "automation/release"
MAIN_REF = "origin/main"
RELEASES_API = "releases?per_page=100"
SAME_TREE_EVIDENCE = "same_tree_ci"
CANDIDATE = "release/candidate.json"
NOTES = "release/notes.md"
MANIFESTS = (
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "plugins/bifrost/.codex-plugin/plugin.json",
)
RELEASE_PATHS = {*MANIFESTS, CANDIDATE, NOTES}
STABLE = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA = re.compile(r"[0-9a-f]{40}$")
ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "version_policy", ROOT / "scripts/next-version.py"
)
assert _spec and _spec.loader
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)
_manifest_spec = importlib.util.spec_from_file_location(
    "release_manifest", ROOT / "scripts/release/release_manifest.py"
)
assert _manifest_spec and _manifest_spec.loader
release_manifest = importlib.util.module_from_spec(_manifest_spec)
_manifest_spec.loader.exec_module(release_manifest)


class ReleaseError(ValueError):
    """A release input or provenance could not be certified."""


def command(args: list[str]) -> str:
    if not args or args[0] not in {"git", "gh"}:
        raise ReleaseError("Release commands are limited to Git and GitHub CLI")
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode:
        # Do not echo request bodies, authorization headers or environment values.
        raise ReleaseError(
            f"{args[0]} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def git(*args: str) -> str:
    return command(["git", *args])


def api(path: str, *, pages: bool = False, fields: dict | None = None) -> object:
    if not re.fullmatch(r"[A-Za-z0-9_./?=&%-]+", path) or ".." in path:
        raise ReleaseError("Invalid repository-scoped GitHub API path")
    args = ["gh", "api", f"repos/{REPOSITORY}/{path}"]
    if pages:
        args += ["--paginate"]
    if fields is not None:
        args += ["--method", "POST", "--input", "-"]
        result = subprocess.run(
            args, input=json.dumps(fields), text=True, capture_output=True, check=False
        )
        if result.returncode:
            raise ReleaseError(
                f"GitHub {path.split('?')[0]} failed ({result.returncode}); read back before retrying"
            )
        raw = result.stdout.strip()
    else:
        raw = command(args)
    if pages:
        # gh --paginate emits one JSON document per page. Decode that stream
        # directly so local recovery also works with pre---slurp GH CLIs.
        documents = []
        decoder = json.JSONDecoder()
        while raw.strip():
            document, end = decoder.raw_decode(raw.lstrip())
            documents.append(document)
            raw = raw.lstrip()[end:]
        return documents
    return json.loads(raw) if raw else None


def array_pages(path: str, key: str | None = None) -> list[dict]:
    pages = api(path, pages=True)
    if not isinstance(pages, list) or not pages:
        raise ReleaseError(f"missing paginated result for {path}")
    rows = []
    for page in pages:
        values = page.get(key) if key and isinstance(page, dict) else page
        if not isinstance(values, list) or any(
            not isinstance(row, dict) for row in values
        ):
            raise ReleaseError(f"invalid paginated result for {path}")
        rows.extend(values)
    return rows


def blob(ref: str, path: str) -> object:
    return json.loads(git("show", f"{ref}:{path}"))


def stable_tag(source: str) -> str | None:
    # git describe also considers merge-parent tags. The fork's baseline must
    # be on its main first-parent history, never an imported upstream tag.
    candidates = git(
        "tag", "--merged", source, "--list", "v*", "--sort=-version:refname"
    ).splitlines()
    first_parent = set(git("rev-list", "--first-parent", source).splitlines())
    for tag in candidates:
        if (
            STABLE.fullmatch(tag)
            and git("rev-parse", f"{tag}^{{commit}}") in first_parent
        ):
            return tag
    return None


def merged_prs() -> list[dict]:
    return array_pages("pulls?state=closed&base=main&per_page=100")


def interval(source: str, base: str | None, prs: list[dict]) -> list[dict]:
    commits = git(
        "rev-list",
        "--first-parent",
        "--reverse",
        f"{base}..{source}" if base else source,
    ).splitlines()
    by_commit: dict[str, list[dict]] = {}
    for pr in prs:
        if pr.get("merged_at") and pr.get("base", {}).get("ref") == "main":
            by_commit.setdefault(pr.get("merge_commit_sha", ""), []).append(pr)
    entries = []
    for sha in commits:
        matches = by_commit.get(sha, [])
        if not matches or len({pr["number"] for pr in matches}) != len(matches):
            raise ReleaseError(
                f"commit {sha} has {len(matches)} merged main PRs; reconcile its release classification"
            )
        # GitHub can mark an absorbed upstream PR merged at the same commit as
        # its enclosing fork PR (e.g. #706/#711). Retain both distinct PRs and
        # their highest bump instead of dropping absorbed changes.
        for pr in sorted(matches, key=lambda item: item["number"]):
            labels = pr.get("labels")
            if not isinstance(pr.get("title"), str) or not isinstance(labels, list):
                raise ReleaseError(f"incomplete PR metadata at {sha}")
            entries.append(
                {
                    "number": pr["number"],
                    "title": pr["title"],
                    "labels": sorted(label["name"] for label in labels),
                    "merge_commit": sha,
                    "author": pr["user"]["login"],
                }
            )
    return entries


def calculate(
    source: str,
    prs: list[dict],
    *,
    base_version: str | None = None,
    default: str = "patch",
) -> dict | None:
    base_tag = stable_tag(source)
    entries = interval(source, base_tag, prs)
    if not entries:
        return None
    floor = policy.contract_bump(base_tag, source) if base_tag else None
    version = (
        "v" + policy.next_version(base_version or base_tag[1:], entries, default, floor)
        if (base_version or base_tag)
        else "v1.0.0"
    )
    return {
        "schema": 1,
        "repository": REPOSITORY,
        "version": version,
        "base_tag": base_tag,
        "source_commit": source,
        "source_tree": git("rev-parse", f"{source}^{{tree}}"),
        "contract_floor": floor,
        "pull_requests": entries,
    }


def changed_paths(base: str, source: str) -> set[str]:
    return set(git("diff", "--name-only", base, source).splitlines())


def validate_candidate(source: str, prs: list[dict]) -> dict:
    if not SHA.fullmatch(source):
        raise ReleaseError("release source must be an exact commit")
    candidate = blob(source, CANDIDATE)
    if (
        not isinstance(candidate, dict)
        or candidate.get("schema") != 1
        or candidate.get("repository") != REPOSITORY
    ):
        raise ReleaseError("invalid release candidate schema/repository")
    previous = candidate.get("source_commit", "")
    version = candidate.get("version", "")
    if not SHA.fullmatch(previous) or not STABLE.fullmatch(version):
        raise ReleaseError("invalid source/version in release candidate")
    git("merge-base", "--is-ancestor", previous, source)
    changes = changed_paths(previous, source)
    if not changes or not changes <= RELEASE_PATHS or CANDIDATE not in changes:
        raise ReleaseError(
            "release contains changes outside its reviewed metadata/manifests; refresh the PR"
        )
    expected = calculate(previous, prs)
    if candidate != expected:
        raise ReleaseError(
            "release calculation/PR inventory changed; refresh the release PR"
        )
    for path in MANIFESTS:
        expected_manifest = blob(previous, path)
        expected_manifest["version"] = version[1:]
        if blob(source, path) != expected_manifest:
            raise ReleaseError(f"{path} must change only its version to {version}")
    notes = git("show", f"{source}:{NOTES}")
    for heading in (
        f"# Bifrost {version}",
        "## Fixed vulnerabilities",
        "## Upgrade notes",
        "## Contributors",
    ):
        if heading not in notes:
            raise ReleaseError(f"release notes missing {heading}")
    return candidate


def pending_release(source: str, prs: list[dict]) -> bool:
    # Do not recursively draft a patch from the release PR itself or overwrite
    # its candidate while its exact main CI/tag publication is still pending.
    try:
        candidate = blob(source, CANDIDATE)
    except (ReleaseError, json.JSONDecodeError):
        return False
    if not isinstance(candidate, dict) or not STABLE.fullmatch(
        candidate.get("version", "")
    ):
        raise ReleaseError("invalid retained release candidate")
    ancestor = set(git("rev-list", "--first-parent", source).splitlines())
    release_prs = [
        pr
        for pr in prs
        if pr.get("merged_at")
        and pr.get("head", {}).get("ref") == BRANCH
        and pr.get("merge_commit_sha") in ancestor
    ]
    if not release_prs:
        raise ReleaseError("retained candidate has no merged release PR")
    ordered = git("rev-list", "--first-parent", source).splitlines()
    merged = min(release_prs, key=lambda pr: ordered.index(pr["merge_commit_sha"]))[
        "merge_commit_sha"
    ]
    tag = candidate["version"]
    if tag not in git("tag", "--list", tag).splitlines():
        print(
            f"Release {tag} at {merged} is awaiting publication; rerun Release tag after fixing its reported blocker.",
            file=sys.stderr,
        )
        return True
    if git("rev-parse", f"{tag}^{{commit}}") != merged:
        raise ReleaseError(
            f"existing {tag} points outside its merged release candidate"
        )
    releases = array_pages(RELEASES_API)
    if not any(
        item.get("tag_name") == tag and not item.get("draft") and item.get("immutable")
        for item in releases
    ):
        print(
            f"Release {tag} is still packaging; preserve its candidate until immutable publication.",
            file=sys.stderr,
        )
        return True
    return False


def markdown(value: str) -> str:
    return (
        value.replace("\n", " ")
        .replace("\r", " ")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def retained_candidate(source: str, merged: str) -> dict:
    """Require the same well-formed candidate on main and its named release PR."""
    candidate = blob(source, CANDIDATE)
    if not isinstance(candidate, dict):
        raise ReleaseError("invalid retained release candidate")
    version = candidate.get("version")
    if not isinstance(version, str) or not STABLE.fullmatch(version):
        raise ReleaseError("invalid retained release candidate version")
    if candidate != blob(merged, CANDIDATE):
        raise ReleaseError("retained candidate differs from the named release PR")
    return candidate


def checked_same_tree_pr_ci(source: str, run_id: int, number: int) -> dict:
    """Certify completed PR evidence for the exact merged preparation tree."""
    pr = api(f"pulls/{number}")
    run = api(f"actions/runs/{run_id}")
    if not isinstance(pr, dict) or not isinstance(run, dict):
        raise ReleaseError("missing same-tree PR/CI identity")
    head = pr.get("head", {})
    if any((
        not pr.get("merged"), pr.get("merge_commit_sha") != source,
        pr.get("base", {}).get("ref") != "main",
        head.get("repo", {}).get("full_name") != REPOSITORY,
        not SHA.fullmatch(head.get("sha", "")),
        run.get("head_sha") != head.get("sha"),
        run.get("head_branch") != head.get("ref"),
        run.get("event") != "pull_request", run.get("status") != "completed",
        run.get("conclusion") != "success", run.get("path") != ".github/workflows/ci.yml",
        run.get("head_repository", {}).get("full_name") != REPOSITORY,
    )):
        raise ReleaseError("same-tree evidence needs the exact merged fork PR and successful trusted CI")
    git("merge-base", "--is-ancestor", source, MAIN_REF)
    git("merge-base", "--is-ancestor", pr["base"]["sha"], head["sha"])
    tree = git("rev-parse", f"{source}^{{tree}}")
    if not SHA.fullmatch(tree) or tree != git("rev-parse", f"{head['sha']}^{{tree}}"):
        raise ReleaseError("PR CI tree differs from the current preparation source")
    attempt = run.get("run_attempt")
    if type(attempt) is not int or attempt < 1:
        raise ReleaseError("missing exact CI attempt")
    require_preparation_jobs(run_id, attempt)
    sonar = checked_pr_sonar(head["sha"], number, run)
    return {"pr": number, "head": head["sha"], "tree": tree,
            "run": run_id, "attempt": attempt, "sonar_check": sonar}


def require_preparation_jobs(run_id: int, attempt: int) -> None:
    """Reject skipped/partial CI rather than reusing a nominal green run."""
    required = {
        "Plan Affected Tests", "Lint & Type Check", "Unit Tests", "Client Unit Tests",
        "E2E Tests", "MCP Conformance", "Candidate Images",
        "sonar-coverage / Sonar Coverage Evidence", "sonar-coverage / Sonar CI Analysis",
    }
    jobs = array_pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", "jobs")
    for name in required:
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) != 1 or matches[0].get("conclusion") != "success":
            raise ReleaseError(f"same-tree CI needs one successful {name} job")


def checked_pr_sonar(head: str, number: int, run: dict) -> int:
    """Require fresh successful Sonar App evidence on the tested PR head."""
    checks = array_pages(
        f"commits/{head}/check-runs?check_name=SonarCloud%20Code%20Analysis&per_page=100",
        "check_runs",
    )
    trusted = [check for check in checks if check.get("app", {}).get("id") == 12526
               and check.get("name") == "SonarCloud Code Analysis"]
    if not trusted:
        raise ReleaseError("missing trusted Sonar check")
    check = max(trusted, key=lambda row: row["id"])
    expected_url = (
        "https://sonarcloud.io/dashboard?id=Midtown-Technology-Group_bifrost"
        + f"&pullRequest={number}"
    )
    started = check.get("started_at")
    completed = check.get("completed_at")
    created = run.get("created_at")
    updated = run.get("updated_at")
    if any((
        check.get("head_sha") != head, check.get("status") != "completed",
        check.get("conclusion") != "success", check.get("details_url") != expected_url,
        not started, not completed, not created, not updated,
    )):
        raise ReleaseError("Sonar identity/gate is not complete for the same-tree PR")
    if not (created <= started <= completed <= updated):
        raise ReleaseError("Sonar evidence is outside the successful CI run window")
    return check["id"]


def supersede_unpublished(
    source: str, prs: list[dict], number: int, run_id: int, *,
    same_tree_pr: int | None = None,
) -> dict:
    """Replace only a failed, untagged candidate through a fresh release PR."""
    same_tree = None
    if same_tree_pr is None:
        checked_main_ci(source, run_id)
    else:
        same_tree = checked_same_tree_pr_ci(source, run_id, same_tree_pr)
    ordered = git("rev-list", "--first-parent", source).splitlines()
    releases = [
        pr for pr in prs
        if pr.get("merged_at") and pr.get("head", {}).get("ref") == BRANCH
        and pr.get("base", {}).get("ref") == "main"
        and pr.get("merge_commit_sha") in ordered
    ]
    if not releases:
        raise ReleaseError("no merged release candidate to supersede")
    previous = min(releases, key=lambda pr: ordered.index(pr["merge_commit_sha"]))
    if previous["number"] != number:
        raise ReleaseError("supersession must name the latest merged release PR")
    merged = previous["merge_commit_sha"]
    candidate = retained_candidate(source, merged)
    version = candidate["version"]
    refs = array_pages(f"git/matching-refs/tags/{version}")
    if any(ref.get("ref") == f"refs/tags/{version}" for ref in refs):
        raise ReleaseError("a tagged candidate cannot be superseded; never move tags")
    if any(item.get("tag_name") == version for item in array_pages(RELEASES_API)):
        raise ReleaseError("a candidate with a GitHub Release cannot be superseded")
    runs = array_pages(
        f"actions/workflows/ci.yml/runs?head_sha={merged}&event=push&per_page=100",
        "workflow_runs",
    )
    trusted = [run for run in runs if run.get("head_sha") == merged
               and run.get("head_branch") == "main" and run.get("event") == "push"
               and run.get("path") == ".github/workflows/ci.yml"
               and run.get("head_repository", {}).get("full_name") == REPOSITORY]
    if not trusted:
        raise ReleaseError("original candidate has no exact main CI evidence")
    failed = max(trusted, key=lambda run: run["id"])
    if failed.get("status") != "completed" or failed.get("conclusion") != "failure":
        raise ReleaseError("original candidate must have terminal failed main CI")
    proof = {"pr": number, "source": merged, "version": version,
             "failed_ci": failed["id"], "replacement_ci": run_id}
    if same_tree is not None:
        proof[SAME_TREE_EVIDENCE] = same_tree
    return proof


def same_tree_notes(proof: dict | None) -> list[str]:
    """Retain the explicit reusable evidence without claiming main CI passed."""
    if proof is None or SAME_TREE_EVIDENCE not in proof:
        return []
    return ["## Same-tree preparation evidence", "",
            json.dumps(proof[SAME_TREE_EVIDENCE], sort_keys=True), "",
            "PR CI was reused only for preparation. Publication still requires exact main-push CI.", ""]


def preparation_ci_note(proof: dict) -> str:
    """Label the receipt by its actual CI source."""
    label = "Replacement source main CI"
    if SAME_TREE_EVIDENCE in proof:
        label = "Replacement same-tree PR CI"
    return f"{label}: {proof['replacement_ci']}."


def prepare_manifests(version: str) -> None:
    """Change only plugin versions inside the checked-out manifests."""
    for path in MANIFESTS:
        checkout = Path.cwd().resolve()
        target = (checkout / path).resolve()
        if not target.is_relative_to(checkout) or (checkout / path).is_symlink():
            raise ReleaseError("Release manifest escapes the checkout")
        manifest = json.loads(target.read_text())
        manifest["version"] = version[1:]
        with target.open("w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")


def prepare(
    source: str, prs: list[dict], *, supersede_pr: int | None = None,
    source_ci_run: int | None = None, same_tree_pr: int | None = None,
) -> dict | None:
    if git("rev-parse", "HEAD") != source or git("status", "--porcelain"):
        raise ReleaseError("prepare requires a clean checkout of its exact source")
    if (supersede_pr is None) != (source_ci_run is None):
        raise ReleaseError("supersession requires both release PR and exact main CI run")
    if same_tree_pr is not None and supersede_pr is None:
        raise ReleaseError("same-tree reuse is limited to explicit supersession preparation")
    pending = pending_release(source, prs)
    superseded = None
    if supersede_pr is not None:
        if not pending or source_ci_run is None:
            raise ReleaseError("supersession requires an unpublished pending candidate")
        superseded = supersede_unpublished(
            source, prs, supersede_pr, source_ci_run, same_tree_pr=same_tree_pr
        )
    elif pending:
        return None
    candidate = calculate(source, prs)
    if candidate is None:
        return None
    Path("release").mkdir(exist_ok=True)
    Path(CANDIDATE).write_text(json.dumps(candidate, indent=2) + "\n")
    prepare_manifests(candidate["version"])
    entries = candidate["pull_requests"]
    changes = [f"- {markdown(pr['title'])} (#{pr['number']})" for pr in entries]
    security = [
        line
        for pr, line in zip(entries, changes, strict=True)
        if re.search(r"security|CVE|GHSA|PYSEC|vulnerab", pr["title"], re.IGNORECASE)
    ]
    notes = [
        f"# Bifrost {candidate['version']}",
        "",
        "## Changes",
        "",
        *changes,
        "",
        "## Fixed vulnerabilities",
        "",
        (
            "Security-related PR titles below are an inventory, not a verified CVE assessment. "
            "The release reviewer should add confirmed identifiers and impact before merging."
        ),
        "",
        *(
            security
            or [
                "No PR titles matched the security filter; this does not establish that no vulnerabilities were fixed."
            ]
        ),
        "",
        "## Upgrade notes",
        "",
        *release_manifest.upgrade_notes(source, candidate["base_tag"]),
        f"Contract comparison requires: {candidate['contract_floor'] or 'no additional bump floor'}.",
        "This release uses the MTG fork's independent SemVer; upstream versions are not its baseline.",
        "",
        "## Contributors",
        "",
        ", ".join(sorted({"@" + pr["author"] for pr in entries})),
        "",
        "## Source",
        "",
        f"Reviewed interval: `{candidate['base_tag'] or 'initial history'}..{source}`.",
        "",
        "Publishing packages does not deploy the MTG production runtime.",
        "",
    ]
    if superseded:
        notes += [
            "## Superseded unpublished candidate", "",
            f"Replaces #{superseded['pr']} at `{superseded['source']}` "
            + f"({superseded['version']}); no tag or GitHub Release existed at preparation.",
            f"Original failed main CI: {superseded['failed_ci']}. "
            + preparation_ci_note(superseded),
            "The original failure remains recorded. This candidate requires fresh exact-head "
            + "review, queue checks, main CI and signed stable packaging.", "",
        ]
    notes += same_tree_notes(superseded)
    Path(NOTES).write_text("\n".join(notes))
    return candidate


def checked_main_ci(source: str, run_id: int) -> None:
    """Require the exact completed trusted main-push CI, including all its gates."""
    run = api(f"actions/runs/{run_id}")
    if not isinstance(run, dict) or any(
        (
            run.get("head_sha") != source,
            run.get("head_branch") != "main",
            run.get("event") != "push",
            run.get("conclusion") != "success",
            run.get("status") != "completed",
            run.get("path") != ".github/workflows/ci.yml",
            run.get("head_repository", {}).get("full_name") != REPOSITORY,
        )
    ):
        raise ReleaseError(
            "publication needs successful CI on the exact trusted main push"
        )
    git("merge-base", "--is-ancestor", source, MAIN_REF)


def checked_release(source: str, run_id: int, prs: list[dict]) -> dict:
    checked_main_ci(source, run_id)
    matches = [
        pr
        for pr in prs
        if pr.get("merged_at")
        and pr.get("merge_commit_sha") == source
        and pr.get("base", {}).get("ref") == "main"
    ]
    if (
        len(matches) != 1
        or matches[0].get("head", {}).get("ref") != BRANCH
        or matches[0].get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
    ):
        raise ReleaseError("checked source is not the merged fork release PR")
    pr = matches[0]
    files = array_pages(f"pulls/{pr['number']}/files?per_page=100")
    if not files or {row["filename"] for row in files} - RELEASE_PATHS:
        raise ReleaseError("release PR changed non-release paths")
    commits = array_pages(f"pulls/{pr['number']}/commits?per_page=100")
    if not commits or any(
        not row.get("commit", {}).get("verification", {}).get("verified")
        for row in commits
    ):
        raise ReleaseError("release PR commits are not verified signed commits")
    commit = api(f"commits/{source}")
    if not commit.get("commit", {}).get("verification", {}).get("verified"):
        raise ReleaseError("merged release commit is not verified signed")
    reviews = array_pages(f"pulls/{pr['number']}/reviews?per_page=100")
    latest = {}
    for review in reviews:
        if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[review["user"]["login"]] = review
    approved = False
    for login, review in latest.items():
        if review["state"] == "CHANGES_REQUESTED":
            raise ReleaseError("release PR has a requested-change review")
        if (
            review["state"] == "APPROVED"
            and review.get("commit_id") == pr["head"]["sha"]
            and review["user"].get("type") == "User"
        ):
            permissions = api(f"collaborators/{quote(login, safe='')}/permission")
            if permissions.get("permission") in {"admin", "maintain", "write"}:
                approved = True
    if not approved:
        raise ReleaseError(
            "release PR needs human maintainer approval of its exact head"
        )
    return validate_candidate(source, prs)


def reject_superseded_source(source: str) -> None:
    """The latest merged release PR on main durably owns publication.

    Read fresh main and PR inventory, not the historical CI run's checkout.
    Ordinary main merges do not supersede a candidate. A later merged release
    PR does, even while its own CI or packaging is still pending.
    """
    git("fetch", "origin", "main")
    ordered = git("rev-list", "--first-parent", MAIN_REF).splitlines()
    releases = [
        pr for pr in merged_prs()
        if pr.get("merged_at") and pr.get("head", {}).get("ref") == BRANCH
        and pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY
        and pr.get("base", {}).get("ref") == "main"
        and pr.get("merge_commit_sha") in ordered
    ]
    if not releases:
        raise ReleaseError("cannot establish the current merged release candidate")
    current = min(releases, key=lambda pr: ordered.index(pr["merge_commit_sha"]))
    if current["merge_commit_sha"] != source:
        raise ReleaseError(
            f"release source is superseded by merged release PR #{current['number']}"
        )
    # Refuse inconsistent retained metadata rather than treating it as absent.
    if blob(MAIN_REF, CANDIDATE) != blob(source, CANDIDATE):
        raise ReleaseError("current main candidate differs from its merged release PR")


def publish(source: str, run_id: int, prs: list[dict]) -> None:
    if not any(
        pr.get("merged_at")
        and pr.get("merge_commit_sha") == source
        and pr.get("head", {}).get("ref") == BRANCH
        for pr in prs
    ):
        print("Exact CI source is an ordinary main merge; no release to publish.")
        return
    candidate = checked_release(source, run_id, prs)
    version = candidate["version"]
    reject_superseded_source(source)
    # Fetch full ref inventory before creating: a failed read cannot mean absent.
    refs = array_pages("git/matching-refs/tags/v")
    matches = [ref for ref in refs if ref.get("ref") == f"refs/tags/{version}"]
    if matches:
        obj = matches[0]["object"]
        while obj["type"] == "tag":
            obj = api(f"git/tags/{obj['sha']}")["object"]
        if obj["type"] != "commit" or obj["sha"] != source:
            raise ReleaseError(
                f"{version} already points to another source; never move it"
            )
    else:
        api("git/refs", fields={"ref": f"refs/tags/{version}", "sha": source})
    verified = api(f"git/ref/tags/{version}")
    obj = verified["object"]
    while obj["type"] == "tag":
        obj = api(f"git/tags/{obj['sha']}")["object"]
    if obj["type"] != "commit" or obj["sha"] != source:
        raise ReleaseError("tag readback does not match the approved source")
    releases = array_pages(RELEASES_API)
    published = [
        release
        for release in releases
        if release.get("tag_name") == version and not release.get("draft")
    ]
    if published:
        release = published[0]
        required = {
            f"bifrost-{version}-source.tar.gz" + suffix
            for suffix in ("", ".sha256", ".sigstore")
        }
        assets = {
            asset["name"]
            for asset in release.get("assets", [])
            if asset.get("state") == "uploaded" and asset.get("size", 0) > 0
        }
        if not release.get("immutable") or not required <= assets:
            raise ReleaseError(
                "published release is not immutable or its signed source assets are incomplete"
            )
        print(
            f"{version}: immutable release already published; no rebuild or replacement"
        )
        return
    # A GITHUB_TOKEN-created tag does not emit a CI push run. Dispatch explicitly
    # once; on delayed/replayed runs inspect existing tag CI rather than stacking
    # another publisher. Failed CI is recovered by rerunning that same run.
    runs = array_pages(
        f"actions/workflows/ci.yml/runs?head_sha={source}&per_page=100", "workflow_runs"
    )
    tag_runs = [
        run
        for run in runs
        if run.get("head_branch") == version
        and run.get("event") in {"push", "workflow_dispatch"}
    ]
    if tag_runs:
        print(
            f"{version}: packaging already recorded: "
            + ", ".join(str(run["id"]) for run in tag_runs)
        )
        return
    reject_superseded_source(source)
    api("actions/workflows/ci.yml/dispatches", fields={"ref": version})
    print(
        f"{version}: exact tag created/read back and packaging dispatched; verify its CI and immutable release"
    )


def checked_source_ref(source: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/~^{}-]*", source) or ".." in source:
        raise ReleaseError("Source must be a Git ref, never a command option")
    return source


def write_preparation_output(version: str, source: str) -> None:
    """Retain the exact prepared version and source for the calling workflow."""
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a") as stream:
            stream.write(f"version={version}\nsource={source}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("version", "prepare", "validate", "publish"))
    parser.add_argument("--source", default="HEAD")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--supersede-unpublished-pr", type=int)
    parser.add_argument("--source-ci-run", type=int)
    parser.add_argument("--same-tree-pr", type=int)
    parser.add_argument("--base")
    parser.add_argument(
        "--default", choices=("patch", "minor", "major"), default="patch"
    )
    args = parser.parse_args()
    try:
        if args.action != "prepare" and (
            args.supersede_unpublished_pr is not None or args.source_ci_run is not None
            or args.same_tree_pr is not None
        ):
            raise ReleaseError("supersession is limited to release PR preparation")
        source_ref = checked_source_ref(args.source)
        source = git("rev-parse", "--verify", "--end-of-options", f"{source_ref}^{{commit}}")
        prs = merged_prs()
        if args.action == "version":
            candidate = calculate(
                source, prs, base_version=args.base, default=args.default
            )
            if candidate is None:
                return 3
            print(candidate["version"])
        elif args.action == "prepare":
            candidate = prepare(
                source, prs, supersede_pr=args.supersede_unpublished_pr,
                source_ci_run=args.source_ci_run, same_tree_pr=args.same_tree_pr,
            )
            if candidate:
                write_preparation_output(candidate["version"], source)
                print(candidate["version"])
        elif args.action == "validate":
            print(validate_candidate(source, prs)["version"])
        else:
            if not args.run_id:
                raise ReleaseError("publish requires an exact main CI run id")
            publish(source, args.run_id, prs)
    except (ReleaseError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        print(f"automatic-release: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
