"""Real Git intervals and mocked GitHub transport exercise release boundaries."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = (
    Path("/repo")
    if Path("/repo/scripts").is_dir()
    else Path(__file__).resolve().parents[3]
)
SPEC = importlib.util.spec_from_file_location(
    "automatic_release", ROOT / "scripts/release/automatic-release.py"
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def test_label_and_title_decisions() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/test-next-version.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    release.git("init", "-q")
    release.git("config", "user.name", "Release fixture")
    release.git("config", "user.email", "release@example.invalid")
    release.git("config", "commit.gpgsign", "false")
    for path in release.MANIFESTS:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"name": "bifrost", "version": "1.0.0"}))
    for path in ("api/shared/contract_version.py", "api/bifrost/contract_version.py"):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("CONTRACT_VERSION: int = 11\n")
    release.git("add", ".")
    release.git("commit", "-qm", "fixture baseline")
    release.git("tag", "v1.0.0")
    base = release.git("rev-parse", "HEAD")
    prs = []

    def commit(
        title="fix: compatible", *, branch="feature", labels=(), write_feature=True
    ):
        if write_feature:
            Path("feature.txt").write_text(title)
        release.git("add", ".")
        release.git("commit", "--allow-empty", "-qm", title)
        sha = release.git("rev-parse", "HEAD")
        pr = {
            "number": len(prs) + 1,
            "title": title,
            "merged_at": "2026-10-02T00:00:00Z",
            "merge_commit_sha": sha,
            "labels": [{"name": label} for label in labels],
            "base": {"ref": "main"},
            "head": {
                "ref": branch,
                "sha": sha,
                "repo": {"full_name": release.REPOSITORY},
            },
            "user": {"login": "maintainer"},
        }
        prs.append(pr)
        return sha

    def candidate():
        source = commit("feat: first feature")
        prepared = release.prepare(source, prs)
        release.git("add", ".")
        release.git("commit", "-qm", "chore(release): v1.1.0")
        merged = release.git("rev-parse", "HEAD")
        pr = {
            "number": len(prs) + 1,
            "title": "chore(release): v1.1.0",
            "merged_at": "2026-10-02T00:00:01Z",
            "merge_commit_sha": merged,
            "labels": [],
            "base": {"ref": "main"},
            "head": {
                "ref": release.BRANCH,
                "sha": merged,
                "repo": {"full_name": release.REPOSITORY},
            },
            "user": {"login": "github-actions[bot]"},
        }
        prs.append(pr)
        release.git("update-ref", "refs/remotes/origin/main", merged)
        return merged, prepared, pr

    return {"base": base, "prs": prs, "commit": commit, "candidate": candidate}


def test_empty_git_interval_does_not_release(history):
    assert release.calculate(history["base"], []) is None


def test_preparation_cannot_mix_another_ref_or_dirty_work(history):
    source = history["commit"]()
    with pytest.raises(release.ReleaseError, match="clean checkout"):
        release.prepare(history["base"], history["prs"])
    Path("unrelated.txt").write_text("preserve this work")
    with pytest.raises(release.ReleaseError, match="clean checkout"):
        release.prepare(source, history["prs"])
    assert Path("unrelated.txt").read_text() == "preserve this work"


def test_git_interval_ignores_same_day_pr_before_tag(history):
    source = history["commit"]()
    prior = {
        "merge_commit_sha": history["base"],
        "merged_at": "2026-10-02T00:00:00Z",
        "base": {"ref": "main"},
    }
    candidate = release.calculate(source, [prior, *history["prs"]])
    assert len(candidate["pull_requests"]) == 1
    assert candidate["version"] == "v1.0.1"


def test_rapid_merges_all_appear_in_interval(history):
    history["commit"]("fix: first")
    history["commit"]("feat: second")
    source = history["commit"]("fix: third")
    candidate = release.calculate(source, history["prs"])
    assert candidate["version"] == "v1.1.0"
    assert [pr["number"] for pr in candidate["pull_requests"]] == [1, 2, 3]


def test_delayed_retry_keeps_exact_source_even_after_later_merge(history):
    source, expected, _ = history["candidate"]()
    history["commit"]("feat!: later breaking change")
    assert release.validate_candidate(source, history["prs"]) == expected


def test_concurrent_merge_invalidates_candidate(history):
    history["candidate"]()
    advanced = history["commit"]("fix: concurrent source change")
    with pytest.raises(release.ReleaseError, match="outside"):
        release.validate_candidate(advanced, history["prs"])


def test_revert_is_included_and_does_not_erase_prior_bump(history):
    original = history["commit"]("feat: new command")
    release.git("revert", "--no-commit", original)
    source = history["commit"]("revert: new command", write_feature=False)
    candidate = release.calculate(source, history["prs"])
    assert candidate["version"] == "v1.1.0"
    assert len(candidate["pull_requests"]) == 2


def test_contract_change_overrides_patch_label(history):
    for path in ("api/shared/contract_version.py", "api/bifrost/contract_version.py"):
        Path(path).write_text("CONTRACT_VERSION: int = 13\n")
    source = history["commit"]("fix: contract", labels=["semver:patch"])
    assert release.calculate(source, history["prs"])["version"] == "v2.0.0"


@pytest.mark.parametrize(
    "content",
    ['CONTRACT_VERSION = int("11")\n', "CONTRACT_VERSION = 12\n", "other = 11\n"],
)
def test_invalid_or_mismatched_contract_fails(history, content):
    Path("api/bifrost/contract_version.py").write_text(content)
    source = history["commit"]()
    with pytest.raises(ValueError):
        release.calculate(source, history["prs"])


def test_unattributed_commit_fails_closed(history):
    source = history["commit"]()
    with pytest.raises(release.ReleaseError, match="0 merged"):
        release.calculate(source, [])


def test_ambiguous_pr_identity_fails_closed(history):
    source = history["commit"]()
    with pytest.raises(release.ReleaseError, match="2 merged"):
        release.calculate(source, history["prs"] * 2)


def test_invalid_base_version_is_not_no_release(history):
    source = history["commit"]()
    with pytest.raises(ValueError, match="base must"):
        release.calculate(source, history["prs"], base_version="invalid")


def test_complete_pagination_is_not_search_capped(monkeypatch):
    pages = [[{"number": n} for n in range(100)], [{"number": 101}]]
    monkeypatch.setattr(release, "api", lambda *args, **kwargs: pages)
    assert len(release.array_pages("pulls")) == 101


def test_older_gh_concatenated_json_pages_are_decoded(monkeypatch):
    monkeypatch.setattr(
        release, "command", lambda args: '[{"number":1}]\n[{"number":2}]'
    )
    assert release.array_pages("pulls") == [{"number": 1}, {"number": 2}]


def test_failed_later_page_does_not_use_partial_inventory(monkeypatch):
    def failed_page(args):
        raise release.ReleaseError("gh page two failed")

    monkeypatch.setattr(release, "command", failed_page)
    with pytest.raises(release.ReleaseError, match="page two"):
        release.array_pages("pulls")


def test_absorbed_prs_at_one_merge_keep_both_bump_classifications(history):
    source = history["commit"]()
    absorbed = dict(
        history["prs"][0], number=2, title="feat!: absorbed upstream change"
    )
    candidate = release.calculate(source, [*history["prs"], absorbed])
    assert candidate["version"] == "v2.0.0"
    assert [pr["number"] for pr in candidate["pull_requests"]] == [1, 2]


@pytest.mark.parametrize("pages", [None, [], [[{}], {"error": "partial"}]])
def test_incomplete_or_invalid_pagination_refuses(monkeypatch, pages):
    monkeypatch.setattr(release, "api", lambda *args, **kwargs: pages)
    with pytest.raises(release.ReleaseError):
        release.array_pages("pulls")


def test_discovery_failure_propagates(monkeypatch):
    def fail(*args, **kwargs):
        raise release.ReleaseError("discovery failed")

    monkeypatch.setattr(release, "api", fail)
    with pytest.raises(release.ReleaseError, match="discovery failed"):
        release.merged_prs()


def test_manifest_version_is_only_allowed_manifest_change(history):
    source, _, _ = history["candidate"]()
    manifest = Path(release.MANIFESTS[0])
    manifest.write_text(json.dumps({"name": "changed-plugin", "version": "1.1.0"}))
    release.git("add", ".")
    release.git("commit", "-qm", "tampered manifest")
    with pytest.raises(release.ReleaseError, match="only its version"):
        release.validate_candidate(release.git("rev-parse", "HEAD"), history["prs"])
    assert release.validate_candidate(source, history["prs"])


def test_changed_classification_requires_new_review(history):
    source, _, _ = history["candidate"]()
    history["prs"][0]["labels"] = [{"name": "semver:major"}]
    with pytest.raises(release.ReleaseError, match="calculation"):
        release.validate_candidate(source, history["prs"])


def test_pending_release_does_not_recursively_prepare(history, monkeypatch):
    source, _, _ = history["candidate"]()
    history["commit"]("fix: arrived during packaging")
    assert release.prepare(release.git("rev-parse", "HEAD"), history["prs"]) is None
    release.git("tag", "v1.1.0", source)
    monkeypatch.setattr(
        release,
        "api",
        lambda *args, **kwargs: [
            [{"tag_name": "v1.1.0", "draft": True, "immutable": False}]
        ],
    )
    assert release.prepare(release.git("rev-parse", "HEAD"), history["prs"]) is None
    monkeypatch.setattr(
        release,
        "api",
        lambda *args, **kwargs: [
            [{"tag_name": "v1.1.0", "draft": False, "immutable": True}]
        ],
    )
    assert (
        release.prepare(release.git("rev-parse", "HEAD"), history["prs"])["version"]
        == "v1.1.1"
    )


@pytest.fixture
def published_candidate(history, monkeypatch):
    source, candidate, pr = history["candidate"]()
    operations = []
    run = {
        "head_sha": source,
        "head_branch": "main",
        "event": "push",
        "conclusion": "success",
        "status": "completed",
        "path": ".github/workflows/ci.yml",
        "head_repository": {"full_name": release.REPOSITORY},
    }
    review = {
        "state": "APPROVED",
        "commit_id": pr["head"]["sha"],
        "user": {"login": "maintainer", "type": "User"},
    }
    signed = {"commit": {"verification": {"verified": True}}}
    transport = {
        "actions/runs/42": run,
        f"pulls/{pr['number']}/files?per_page=100": [
            [{"filename": name} for name in release.RELEASE_PATHS]
        ],
        f"pulls/{pr['number']}/commits?per_page=100": [[signed]],
        f"commits/{source}": signed,
        f"pulls/{pr['number']}/reviews?per_page=100": [[review]],
        "collaborators/maintainer/permission": {"permission": "admin"},
        "git/matching-refs/tags/v": [[]],
        f"git/ref/tags/{candidate['version']}": {
            "object": {"type": "commit", "sha": source}
        },
        "releases?per_page=100": [[]],
        f"actions/workflows/ci.yml/runs?head_sha={source}&per_page=100": [
            {"workflow_runs": []}
        ],
    }

    def api(path, *, pages=False, fields=None):
        if fields is not None:
            operations.append((path, fields))
            return None
        return transport[path]

    monkeypatch.setattr(release, "api", api)
    return source, transport, operations, candidate


def test_approved_merge_tags_exact_source_and_explicitly_dispatches(
    history, published_candidate
):
    source, _, operations, candidate = published_candidate
    release.publish(source, 42, history["prs"])
    assert operations == [
        ("git/refs", {"ref": "refs/tags/" + candidate["version"], "sha": source}),
        ("actions/workflows/ci.yml/dispatches", {"ref": candidate["version"]}),
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("conclusion", "failure"),
        ("head_sha", "0" * 40),
        ("event", "pull_request"),
        ("head_branch", "feature"),
        ("path", ".github/workflows/untrusted.yml"),
    ],
)
def test_wrong_ci_cannot_publish(history, published_candidate, field, value):
    source, transport, operations, _ = published_candidate
    transport["actions/runs/42"][field] = value
    with pytest.raises(release.ReleaseError, match="successful CI"):
        release.publish(source, 42, history["prs"])
    assert operations == []


@pytest.mark.parametrize(
    "problem",
    ["unsigned", "bot", "stale", "changes_requested", "read_only", "extra_path"],
)
def test_review_and_source_guards_precede_any_write(
    history, published_candidate, problem
):
    source, transport, operations, _ = published_candidate
    pr = history["prs"][-1]
    review = transport[f"pulls/{pr['number']}/reviews?per_page=100"][0][0]
    if problem == "unsigned":
        transport[f"commits/{source}"]["commit"]["verification"]["verified"] = False
    elif problem == "bot":
        review["user"]["type"] = "Bot"
    elif problem == "stale":
        review["commit_id"] = "0" * 40
    elif problem == "changes_requested":
        review["state"] = "CHANGES_REQUESTED"
    elif problem == "read_only":
        transport["collaborators/maintainer/permission"]["permission"] = "read"
    else:
        transport[f"pulls/{pr['number']}/files?per_page=100"][0].append(
            {"filename": "api/runtime.py"}
        )
    with pytest.raises(release.ReleaseError):
        release.publish(source, 42, history["prs"])
    assert operations == []


def test_existing_tag_on_different_commit_is_never_moved(history, published_candidate):
    source, transport, operations, candidate = published_candidate
    transport["git/matching-refs/tags/v"] = [
        [
            {
                "ref": "refs/tags/" + candidate["version"],
                "object": {"type": "commit", "sha": history["base"]},
            }
        ]
    ]
    with pytest.raises(release.ReleaseError, match="never move"):
        release.publish(source, 42, history["prs"])
    assert operations == []


def test_delayed_dispatch_readback_does_not_duplicate_run(history, published_candidate):
    source, transport, operations, candidate = published_candidate
    transport["git/matching-refs/tags/v"] = [
        [
            {
                "ref": "refs/tags/" + candidate["version"],
                "object": {"type": "commit", "sha": source},
            }
        ]
    ]
    transport[f"actions/workflows/ci.yml/runs?head_sha={source}&per_page=100"] = [
        {
            "workflow_runs": [
                {
                    "id": 99,
                    "head_branch": candidate["version"],
                    "event": "workflow_dispatch",
                    "conclusion": "failure",
                }
            ]
        }
    ]
    release.publish(source, 42, history["prs"])
    assert operations == []  # recover by rerunning 99, never stacking another run


def test_published_immutable_release_is_not_rebuilt(history, published_candidate):
    source, transport, operations, candidate = published_candidate
    tag = candidate["version"]
    transport["git/matching-refs/tags/v"] = [
        [{"ref": "refs/tags/" + tag, "object": {"type": "commit", "sha": source}}]
    ]
    transport["releases?per_page=100"] = [
        [
            {
                "tag_name": tag,
                "draft": False,
                "immutable": True,
                "assets": [
                    {
                        "name": f"bifrost-{tag}-source.tar.gz" + suffix,
                        "state": "uploaded",
                        "size": 1,
                    }
                    for suffix in ("", ".sha256", ".sigstore")
                ],
            }
        ]
    ]
    release.publish(source, 42, history["prs"])
    assert operations == []


def test_unverified_tag_readback_never_dispatches(history, published_candidate):
    source, transport, operations, candidate = published_candidate
    transport[f"git/ref/tags/{candidate['version']}"]["object"]["sha"] = history["base"]
    with pytest.raises(release.ReleaseError, match="tag readback"):
        release.publish(source, 42, history["prs"])
    assert len(operations) == 1 and operations[0][0] == "git/refs"


def test_ordinary_main_merge_has_no_release_mutations(history, monkeypatch):
    source = history["commit"]()

    def reject(*args, **kwargs):
        pytest.fail("ordinary main merge attempted publication")

    monkeypatch.setattr(release, "api", reject)
    release.publish(source, 42, history["prs"])



def test_prepared_notes_include_actual_contract_transition_and_retirement_limit(history):
    for path in release.release_manifest.CONTRACT_PATHS:
        Path(path).write_text("CONTRACT_VERSION: int = 13\n")
    migration = Path(release.release_manifest.MIGRATIONS) / "20261003_workflow_registration_retirement.py"
    migration.parent.mkdir(parents=True, exist_ok=True)
    migration.write_text("revision = '20261003_workflow_retirement'\ndown_revision = None\n")
    source = history["commit"]("fix: terminal registration history")
    release.prepare(source, history["prs"])
    notes = Path(release.NOTES).read_text()
    assert "11 -> 13" in notes
    assert "refuses once retirement evidence exists" in notes
    assert "Preserve terminal workflow retirement evidence" in notes
    assert "protected infrastructure migration lane" in notes
    assert "production caller/byte evidence" in notes
    assert "uncertain outcome requires readback before any exact retry" in notes
    assert f"/blob/{source}/docs/architecture/rapid-workspace-promotion.md" in notes
