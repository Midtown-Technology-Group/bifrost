"""Benchmark safety regressions; collected by the repository Sonar Docker lane."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("typescript_benchmark", Path(__file__).with_name("typescript.py"))
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # The repository Python test image need not install GNU time. This
        # fixture exercises real child processes/pipe handling/process groups;
        # GNU time itself was verified in the dedicated benchmark VM.
        timer = self.root / "timer.py"
        timer.write_text(
            'import pathlib,resource,subprocess,sys\n'
            'result=subprocess.run(sys.argv[5:])\n'
            'usage=resource.getrusage(resource.RUSAGE_CHILDREN)\n'
            'pathlib.Path(sys.argv[4]).write_text(f"{usage.ru_maxrss} {usage.ru_utime} {usage.ru_stime}\\n")\n'
            'raise SystemExit(result.returncode)\n')
        actual_popen = subprocess.Popen

        def fixture_timer(command, **kwargs):
            if command[0] == "/usr/bin/time":
                command = [sys.executable, str(timer), *command[1:]]
            return actual_popen(command, **kwargs)

        timer_patch = patch.object(bench.subprocess, "Popen", fixture_timer)
        timer_patch.start()
        self.addCleanup(timer_patch.stop)
        self.client = self.root / "client"
        self.client.mkdir()
        (self.client / "node_modules").mkdir()
        (self.client / "source.ts").write_text("export const value = 1;\n")
        self.checker = self.root / "checker.py"
        self.checker.write_text(
            'import pathlib,sys\n'
            'if "--version" in sys.argv: print("Version fixture")\n'
            'else: pathlib.Path("fixture.tsbuildinfo").write_text("cache")\n')
        binary_dir = self.root / "bin"
        binary_dir.mkdir()
        node = binary_dir / "node"
        node.write_text(f'#!{sys.executable}\nimport pathlib\npathlib.Path("dist").mkdir(exist_ok=True)\n')
        node.chmod(0o755)
        self.environment = patch.dict(os.environ, {"PATH": str(binary_dir) + os.pathsep + os.environ["PATH"]})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def main(self, *extra):
        argv = ["benchmark", "--client", str(self.client), "--compiler",
                f"fixture={sys.executable} {self.checker}", "--warmups", "1", *extra]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            bench.main()
        return json.loads(output.getvalue())

    def test_all_scenarios_and_pipelines_keep_source_and_caches_isolated(self):
        report = self.main("--builds")
        self.assertEqual(len(report["cases"]), 9)
        self.assertEqual(report["versions"], {"fixture": "Version fixture"})
        for case in report["cases"].values():
            self.assertEqual(len(case["samples"]), 10)
            self.assertGreater(case["summary"]["seconds"]["min"], 0)
        self.assertEqual((self.client / "source.ts").read_text(), "export const value = 1;\n")
        self.assertFalse((self.client / "fixture.tsbuildinfo").exists())
        self.assertFalse((self.client / "dist").exists())

    def test_selected_scenario_and_pipeline_only(self):
        self.assertEqual(list(self.main("--scenario", "node-full")["cases"]), ["fixture/node-full"])
        self.assertEqual(len(self.main("--pipelines-only")["cases"]), 3)

    def test_invalid_cli_rejects_before_commands(self):
        for options in [("--runs", "9"), ("--warmups", "0"), ("--scenario", "unknown")]:
            with self.subTest(options=options), self.assertRaises(SystemExit) as error:
                self.main(*options)
            self.assertEqual(error.exception.code, 2)

    def test_statistics_and_order_are_reproducible(self):
        summary = bench.summarize(list(range(1, 11)))
        self.assertEqual(summary["median"], 5.5)
        self.assertEqual(summary["p95"], 10)
        names = {"go": [], "rust": [], "oracle": []}
        orders = [bench.compiler_order(names, 20261008, "app-full", i) for i in range(10)]
        self.assertGreater(len({tuple(order) for order in orders}), 1)
        self.assertEqual(orders, [bench.compiler_order(dict(reversed(list(names.items()))), 20261008, "app-full", i) for i in range(10)])

    def test_failed_command_preserves_diagnostic_and_no_success_result(self):
        with self.assertRaisesRegex(RuntimeError, "exited 3") as error:
            bench.run([sys.executable, "-c", 'print("src/f.ts(1,1): error TS2322: mismatch"); raise SystemExit(3)'], self.root)
        self.assertIn("TS2322: mismatch", str(error.exception))

    def test_timeout_kills_descendants(self):
        pidfile = self.root / "pid"
        code = ('import pathlib,subprocess,time; p=subprocess.Popen(["sleep","30"]); '
                f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(30)')
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            bench.run([sys.executable, "-c", code], self.root, timeout=1)
        self.assertLess(time.monotonic() - start, 5)
        stat = Path("/proc") / pidfile.read_text() / "stat"
        self.assertTrue(not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z")

    def test_failed_phases_emit_no_summary(self):
        # Version, warmup, measured run, incremental prime, pipeline checker and bundle.
        cases = [(1, ["--scenario", "app-full"]), (2, ["--scenario", "app-full"]),
                 (3, ["--scenario", "app-full"]), (2, ["--scenario", "build-incremental-enabled"]),
                 (5, ["--scenario", "build-incremental-enabled"]), (2, ["--pipelines-only"]),
                 (13, ["--pipelines-only"]), (14, ["--pipelines-only"]),
                 (35, ["--pipelines-only"]), (36, ["--pipelines-only"])]
        actual_run = bench.run
        for fail_at, options in cases:
            calls = []
            def failing_run(command, cwd, **kwargs):
                calls.append((command, kwargs))
                if len(calls) == fail_at:
                    raise RuntimeError("injected failure")
                return actual_run(command, cwd, **kwargs)
            with self.subTest(fail_at=fail_at, options=options), patch.object(bench, "run", failing_run):
                with self.assertRaisesRegex(RuntimeError, "injected failure"):
                    self.main(*options)
            self.assertEqual(calls[0][1], {"timeout": 30})

    def test_sequential_failure_prevents_bundle_and_sampler_stops(self):
        threads_before = set(threading.enumerate())
        with patch.object(bench, "run", side_effect=RuntimeError("checker failed")) as runner:
            with self.assertRaisesRegex(RuntimeError, "checker failed"):
                bench.pipeline_sample("sequential", ["compiler"], ["bundle"], self.root)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(set(threading.enumerate()), threads_before)

    def test_sampler_observes_live_child_and_stops(self):
        child = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(child.wait)
        self.addCleanup(os.killpg, child.pid, signal.SIGKILL)
        stop, peak = threading.Event(), [0]
        sampler = threading.Thread(target=bench.sample_rss, args=(stop, peak))
        sampler.start()
        try:
            deadline = time.monotonic() + 2
            while peak[0] == 0 and time.monotonic() < deadline:
                stop.wait(.01)
            self.assertGreater(peak[0], 0)
        finally:
            stop.set()
            sampler.join(timeout=2)
        self.assertFalse(sampler.is_alive())

    def test_cache_deletion_preserves_other_files(self):
        (self.root / "test.tsbuildinfo").write_text("cache")
        (self.root / "source.ts").write_text("source")
        bench.clear_build_cache(self.root)
        self.assertFalse((self.root / "test.tsbuildinfo").exists())
        self.assertEqual((self.root / "source.ts").read_text(), "source")


if __name__ == "__main__":
    unittest.main()
