from __future__ import annotations

from pathlib import Path

import pytest

from scripts import plan_affected_tests as affected


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def graph_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(affected, "REPO_ROOT", tmp_path)
    return tmp_path


def test_backend_helper_selects_reverse_importers_and_route_e2e(
    graph_repo: Path,
) -> None:
    _write(
        graph_repo,
        "api/src/services/helper.py",
        "def normalize(value):\n    return value\n",
    )
    _write(
        graph_repo,
        "api/src/services/report.py",
        "from src.services.helper import normalize\n\ndef report(value):\n    return normalize(value)\n",
    )
    _write(
        graph_repo,
        "api/src/routers/reports.py",
        "from fastapi import APIRouter\nfrom src.services.report import report\n"
        "router = APIRouter(prefix='/api/reports')\n"
        "@router.get('/{report_id}')\ndef get_report(report_id):\n    return report(report_id)\n",
    )
    _write(
        graph_repo,
        "api/tests/unit/services/test_report.py",
        "from src.services.report import report\n\ndef test_report():\n    assert report(1) == 1\n",
    )
    _write(
        graph_repo,
        "api/tests/unit/routers/test_reports.py",
        "from src.routers.reports import get_report\n\ndef test_route():\n    assert get_report(1) == 1\n",
    )
    _write(
        graph_repo,
        "api/tests/e2e/api/test_reports.py",
        "def test_report(e2e_client):\n    assert e2e_client.get('/api/reports/123')\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "api/src/services/helper.py")]
    )

    assert plan.scope == "affected"
    assert plan.python.impacted == (
        "api/src/routers/reports.py",
        "api/src/services/helper.py",
        "api/src/services/report.py",
    )
    assert plan.python.unit_tests == (
        "tests/unit/routers/test_reports.py",
        "tests/unit/services/test_report.py",
    )
    assert plan.python.e2e_tests == ("tests/e2e/api/test_reports.py",)
    assert plan.python.runtime_edges == 1


def test_backend_reexport_keeps_reverse_dependency_coverage(
    graph_repo: Path,
) -> None:
    _write(graph_repo, "api/src/services/helper.py", "VALUE = 1\n")
    _write(
        graph_repo,
        "api/src/services/__init__.py",
        "from src.services.helper import VALUE\n",
    )
    _write(
        graph_repo,
        "api/src/services/consumer.py",
        "from src.services import VALUE\n\ndef consume():\n    return VALUE\n",
    )
    _write(
        graph_repo,
        "api/tests/unit/services/test_consumer.py",
        "from src.services.consumer import consume\n\ndef test_consume():\n"
        "    assert consume() == 1\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "api/src/services/helper.py")]
    )

    assert plan.scope == "affected"
    assert plan.python.impacted == (
        "api/src/services/__init__.py",
        "api/src/services/consumer.py",
        "api/src/services/helper.py",
    )
    assert plan.python.unit_tests == ("tests/unit/services/test_consumer.py",)


def test_unowned_backend_downstream_falls_back_to_comprehensive(
    graph_repo: Path,
) -> None:
    _write(graph_repo, "api/src/services/helper.py", "VALUE = 1\n")
    _write(
        graph_repo,
        "api/src/services/unowned.py",
        "from src.services.helper import VALUE\n",
    )
    _write(
        graph_repo,
        "api/tests/unit/services/test_helper.py",
        "from src.services.helper import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "api/src/services/helper.py")]
    )

    assert plan.scope == "affected"
    assert plan.lane("api_unit") == "comprehensive"
    assert plan.lane("api_e2e") == "comprehensive"
    assert plan.lane("client_e2e") == "skip"
    assert plan.python.uncovered == ("api/src/services/unowned.py",)


def test_client_helper_selects_transitive_component_test(graph_repo: Path) -> None:
    _write(
        graph_repo,
        "client/src/lib/format.ts",
        "export const format = (value: string) => value;\n",
    )
    _write(
        graph_repo,
        "client/src/components/Value.tsx",
        "import { format } from '@/lib/format';\nexport const Value = () => format('ok');\n",
    )
    _write(
        graph_repo,
        "client/src/components/Value.test.tsx",
        "import { Value } from './Value';\nit('works', () => expect(Value()).toBe('ok'));\n",
    )

    plan = affected.plan_changes([affected.GitChange("M", "client/src/lib/format.ts")])

    assert plan.scope == "affected"
    assert plan.client.impacted == (
        "client/src/components/Value.tsx",
        "client/src/lib/format.ts",
    )
    assert plan.client.unit_tests == ("src/components/Value.test.tsx",)
    assert plan.lane("client_e2e") == "skip"


def test_unowned_client_page_requires_comprehensive_browser_validation(
    graph_repo: Path,
) -> None:
    _write(
        graph_repo,
        "client/src/pages/Reports.tsx",
        "export const Reports = () => null;\n",
    )
    _write(
        graph_repo,
        "client/src/pages/Reports.test.tsx",
        "import { Reports } from './Reports';\nit('loads', () => expect(Reports()).toBeNull());\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "client/src/pages/Reports.tsx")]
    )

    assert plan.scope == "affected"
    assert plan.lane("client_unit") == "affected"
    assert plan.lane("client_e2e") == "comprehensive"
    assert plan.lane("api_unit") == "skip"
    assert plan.client.uncovered_e2e == ("client/src/pages/Reports.tsx",)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (affected.GitChange("D", "api/src/services/old.py"), "deletions"),
        (affected.GitChange("M", ".github/workflows/ci.yml"), "high-risk"),
        (affected.GitChange("M", "api/scripts/plan_affected_tests.py"), "high-risk"),
        (affected.GitChange("M", "api/src/routers/auth.py"), "high-risk"),
        (affected.GitChange("M", "requirements.lock"), "high-risk"),
        (affected.GitChange("M", "unexpected.toml"), "unmodelled"),
    ],
)
def test_uncertain_changes_fail_closed(
    graph_repo: Path,
    change: affected.GitChange,
    reason: str,
) -> None:
    plan = affected.plan_changes([change])

    assert plan.scope == "comprehensive"
    assert reason in plan.reason


def test_normalization_preserves_dotfile_paths() -> None:
    assert affected._normalize(".github/workflows/ci.yml") == (
        ".github/workflows/ci.yml"
    )
    assert affected._normalize("./.snyk") == ".snyk"


def test_python_test_only_change_runs_exact_test(graph_repo: Path) -> None:
    _write(
        graph_repo,
        "api/tests/unit/services/test_leaf.py",
        "def test_leaf():\n    assert True\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "api/tests/unit/services/test_leaf.py")]
    )

    assert plan.scope == "affected"
    assert plan.python.unit_tests == ("tests/unit/services/test_leaf.py",)
    assert plan.lane("api_unit") == "affected"
    assert plan.lane("api_e2e") == "skip"


def test_git_changes_rejects_argument_injection() -> None:
    with pytest.raises(affected.PlanError, match="full Git commit SHAs"):
        affected.git_changes("--output=/tmp/escaped", "a" * 40)


@pytest.mark.parametrize(
    ("path", "text"),
    [
        (
            "api/src/services/plugin.py",
            (
                "from importlib import import_module\nname = 'src.services.helper'\n"
                "plugin = import_module(name)\n"
            ),
        ),
        (
            "client/src/lib/plugin.ts",
            "const name = './helper';\nexport const plugin = import(name);\n",
        ),
    ],
)
def test_nonliteral_dynamic_import_falls_back_to_comprehensive(
    graph_repo: Path, path: str, text: str
) -> None:
    _write(graph_repo, path, text)

    plan = affected.plan_changes([affected.GitChange("M", path)])

    assert plan.scope == "affected"
    lane = "api_unit" if path.startswith("api/") else "client_unit"
    assert plan.lane(lane) == "comprehensive"
    assert plan.lane_reasons[lane] == f"non-literal dynamic import in {path}"


def test_unchanged_generator_wiring_does_not_force_comprehensive(
    graph_repo: Path,
) -> None:
    _write(graph_repo, "api/src/services/helper.py", "VALUE = 1\n")
    _write(
        graph_repo,
        "api/scripts/skill-truth/generate.py",
        "from src.services.helper import VALUE\n"
        "from importlib import import_module\n"
        "name = 'bifrost.workflows'\n"
        "module = import_module(name)\n",
    )
    _write(
        graph_repo,
        "api/tests/unit/services/test_helper.py",
        "from src.services.helper import VALUE\n\n"
        "def test_value():\n"
        "    assert VALUE == 1\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "api/src/services/helper.py")]
    )

    assert plan.scope == "affected"
    assert plan.python.impacted == ("api/src/services/helper.py",)
    assert plan.python.unit_tests == ("tests/unit/services/test_helper.py",)


def test_changed_generator_dynamic_import_still_fails_closed(
    graph_repo: Path,
) -> None:
    path = "api/scripts/skill-truth/generate.py"
    _write(
        graph_repo,
        path,
        "from importlib import import_module\n"
        "name = 'bifrost.workflows'\n"
        "module = import_module(name)\n",
    )

    plan = affected.plan_changes([affected.GitChange("M", path)])

    assert plan.scope == "affected"
    lane = "api_unit" if path.startswith("api/") else "client_unit"
    assert plan.lane(lane) == "comprehensive"
    assert plan.lane_reasons[lane] == f"non-literal dynamic import in {path}"


def test_ci_evidence_paths_must_match_runner_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "github-output"
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(allowed))

    assert affected._runner_output("GITHUB_OUTPUT") == allowed.resolve()
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path.parent / "escaped"))
    with pytest.raises(SystemExit, match="outside RUNNER_TEMP"):
        affected._runner_output("GITHUB_OUTPUT")


@pytest.mark.parametrize("event", ["local", "pull_request", "merge_group"])
def test_event_plan_keeps_affected_tests_and_adds_merge_integration_baseline(
    graph_repo: Path, monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    path = "api/src/services/helper.py"
    _write(graph_repo, path, "VALUE = 1\n")
    _write(
        graph_repo,
        "api/tests/unit/test_helper.py",
        "from src.services.helper import VALUE\n",
    )

    def changes(base: str, head: str) -> list[affected.GitChange]:
        assert (base, head) == ("base", "combined-head")
        return [affected.GitChange("M", path)]

    monkeypatch.setattr(affected, "git_changes", changes)

    plan = affected.plan_event(event, "base", "combined-head")

    assert plan.scope == "affected"
    assert plan.python.unit_tests == ("tests/unit/test_helper.py",)
    assert plan.python.e2e_tests == (
        ("tests/e2e/api/test_auth.py",) if event == "merge_group" else ()
    )
    assert plan.client.e2e_tests == (
        ("e2e/auth.unauth.spec.ts",) if event == "merge_group" else ()
    )


@pytest.mark.parametrize(
    "event,base,ref",
    [
        ("schedule", "base", "refs/heads/main"),
        ("workflow_dispatch", "base", "refs/heads/main"),
        ("push", "base", "refs/tags/v1.0"),
        ("merge_group", None, ""),
        ("merge_group", "0" * 40, ""),
        ("unknown", "base", ""),
    ],
)
def test_event_plan_fails_closed_without_computing_a_partial_diff(
    monkeypatch: pytest.MonkeyPatch, event: str, base: str | None, ref: str
) -> None:
    def unexpected(*args):
        raise AssertionError("comprehensive event must not select a partial diff")

    monkeypatch.setattr(affected, "git_changes", unexpected)
    plan = affected.plan_event(event, base, "head", ref)
    assert plan.scope == "comprehensive"
    assert plan.lane("api_e2e") == "comprehensive"
    assert plan.lane("client_e2e") == "comprehensive"


@pytest.mark.parametrize(
    "path,scope",
    [("README.md", "docs-only"), ("api/alembic/versions/new.py", "comprehensive")],
)
def test_merge_group_preserves_docs_skip_and_high_risk_fallback(
    graph_repo: Path, monkeypatch: pytest.MonkeyPatch, path: str, scope: str
) -> None:
    monkeypatch.setattr(
        affected, "git_changes", lambda *args: [affected.GitChange("M", path)]
    )
    assert affected.plan_event("merge_group", "base", "head").scope == scope


@pytest.mark.parametrize(
    "path",
    [
        "api/src/core/security.py",
        "api/src/auth/dependencies.py",
        "api/src/models/orm/user.py",
        "api/alembic/versions/new.py",
    ],
)
def test_security_and_storage_changes_remain_global(
    graph_repo: Path, path: str
) -> None:
    _write(graph_repo, path, "VALUE = 1\n")
    plan = affected.plan_changes([affected.GitChange("M", path)])
    assert plan.scope == "comprehensive"
    assert set(plan.to_dict()["lanes"].values()) == {"comprehensive"}


def test_mixed_safe_surfaces_select_independently(graph_repo: Path) -> None:
    _write(graph_repo, "api/src/services/helper.py", "VALUE = 1\n")
    _write(
        graph_repo,
        "api/tests/unit/test_helper.py",
        "from src.services.helper import VALUE\n",
    )
    _write(graph_repo, "client/src/lib/format.ts", "export const value = 1;\n")
    _write(
        graph_repo, "client/src/lib/format.test.ts", "import {value} from './format';\n"
    )
    plan = affected.plan_changes(
        [
            affected.GitChange("M", "api/src/services/helper.py"),
            affected.GitChange("M", "client/src/lib/format.ts"),
        ]
    )
    assert plan.scope == "affected"
    assert plan.python.unit_tests == ("tests/unit/test_helper.py",)
    assert plan.client.unit_tests == ("src/lib/format.test.ts",)
    assert plan.lane("api_e2e") == plan.lane("client_e2e") == "skip"


@pytest.mark.parametrize(
    "definition", ["class Report: pass\n", "class Report: required: int\n"]
)
def test_contract_changes_require_backend_and_browser_not_all_client_unit(
    graph_repo: Path, definition: str
) -> None:
    path = "api/src/models/contracts/report.py"
    _write(graph_repo, path, definition)
    _write(graph_repo, "client/src/lib/format.test.ts", "it('works', () => {});\n")
    plan = affected.plan_changes(
        [
            affected.GitChange("M", path),
            affected.GitChange("M", "client/src/lib/format.test.ts"),
        ]
    )
    assert plan.scope == "affected"
    for lane in ("api_quality", "api_unit", "api_e2e", "client_e2e", "mcp_conformance"):
        assert plan.lane(lane) == "comprehensive"
        assert "compatibility is not proven" in plan.lane_reasons[lane]
    assert plan.lane("client_unit") == "affected"
    assert plan.client.unit_tests == ("src/lib/format.test.ts",)


def test_one_browser_owner_does_not_cover_another_page(graph_repo: Path) -> None:
    for page in ("Owned", "Unowned"):
        _write(graph_repo, f"client/src/pages/{page}.tsx", "export const value = 1;\n")
        _write(
            graph_repo,
            f"client/src/pages/{page}.test.tsx",
            f"import {{value}} from './{page}';\n",
        )
    _write(
        graph_repo,
        "client/e2e/owned.spec.ts",
        "import {value} from '@/pages/Owned';\n",
    )
    plan = affected.plan_changes(
        [
            affected.GitChange("M", "client/src/pages/Owned.tsx"),
            affected.GitChange("M", "client/src/pages/Unowned.tsx"),
        ]
    )
    assert plan.lane("client_unit") == "affected"
    assert plan.lane("client_e2e") == "comprehensive"
    assert plan.client.uncovered_e2e == ("client/src/pages/Unowned.tsx",)


def test_lane_fallback_serializes_full_e2e_matrix_and_reasons(graph_repo: Path) -> None:
    path = "api/src/models/contracts/report.py"
    _write(graph_repo, path, "class Report: pass\n")
    plan = affected.plan_changes([affected.GitChange("M", path)])
    output, summary = graph_repo / "output", graph_repo / "summary"
    affected.write_github_output(output, plan)
    affected.write_summary(summary, plan)
    assert "api_e2e_mode=comprehensive" in output.read_text()
    assert '"shard":4,"total":4' in output.read_text()
    assert "compatibility is not proven" in summary.read_text()
    assert plan.to_dict()["lane_reasons"] == plan.lane_reasons


def test_browser_only_unowned_page_preserves_owned_component_tests(
    graph_repo: Path,
) -> None:
    _write(
        graph_repo,
        "client/src/pages/Report.tsx",
        "import {value} from '@/components/Value';\n",
    )
    _write(graph_repo, "client/src/components/Value.tsx", "export const value = 1;\n")
    _write(
        graph_repo,
        "client/src/components/Value.test.tsx",
        "import {value} from './Value';\n",
    )
    plan = affected.plan_changes(
        [
            affected.GitChange("M", "client/src/pages/Report.tsx"),
            affected.GitChange("M", "client/src/components/Value.tsx"),
        ]
    )
    assert plan.client.uncovered == ("client/src/pages/Report.tsx",)
    assert plan.lane("client_unit") == "affected"
    assert plan.client.unit_tests == ("src/components/Value.test.tsx",)
    assert plan.lane("client_e2e") == "comprehensive"
