"""Diagnostic drivers retain operator policy and do not hide failed workloads."""

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


spike = load("spike_cleanup_under_test", "scripts/kubernetes/spike.py")
with patch.dict(sys.modules, {"spike": spike}):
    builds = load("build_cleanup_under_test", "scripts/kubernetes/build_spike.py")
with patch.dict(sys.modules, {"memory_sampler": load("sampler_cleanup_under_test", "scripts/memory_sampler.py")}):
    benchmark = load("benchmark_cleanup_under_test", "scripts/benchmark_large_table_responses.py")


class WorkloadProcess:
    def __init__(self, stdout, returncode=0, *, builds=False):
        self.returncode = returncode
        self.stdin = io.StringIO()
        self.terminated = False
        self.polls = iter([None, returncode])
        if builds:
            stdout.write('{"event": "builds_running"}\n{"success": true}\n')
        else:
            stdout.write('{"event":"submitted","execution_id":"fixture"}\n'
                         '{"event":"completed","worker":"worker-a"}\n'
                         '{"event":"completed","worker":"worker-b"}\n')
        stdout.flush()

    def poll(self):
        return next(self.polls, self.returncode)

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.returncode


def pod(name, phase="Running", deleting=False):
    metadata = {"name": name}
    if deleting:
        metadata["deletionTimestamp"] = "2026-10-07"
    return {"metadata": metadata, "status": {"phase": phase, "conditions": [{"type": "Ready", "status": "True"}]}}


class KindDriverCleanup(unittest.TestCase):
    def test_modes_restore_existing_policy_and_deadline_environment(self):
        original = {"spec": {"minReplicaCount": 1, "maxReplicaCount": 3},
                    "metadata": {"annotations": {"autoscaling.keda.sh/paused-replicas": "operator-value"}}}
        for mode in ("warm", "sdk", "cold", "burst", "load", "drain", "deadline"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                calls = []
                def kubectl(*args, **kwargs):
                    calls.append((args, kwargs))
                    if args[:2] == ("get", "scaledobject/bifrost-worker"):
                        return json.dumps(original)
                    if args[:2] == ("get", "deployment/bifrost-worker"):
                        return json.dumps({"spec": {"template": {"spec": {"containers": [{"env": [{"name": "EXISTING", "value": "preserved"}]}]}}}})
                    if args[:2] == ("get", "pods"):
                        return json.dumps({"items": [pod("worker-a"), pod("worker-b")] if mode == "load" else [pod("worker-a")]})
                    if "redis-cli" in args:
                        return "1"
                    if args[:2] == ("exec", "worker-a") or args[:2] == ("exec", "worker-b"):
                        return json.dumps({"current_bytes": 12, "peak_bytes": 15})
                    return '{"event":"warmup"}'
                def process(*args, **kwargs):
                    return WorkloadProcess(kwargs["stdout"])
                output = Path(directory) / "result"
                with patch.object(spike, "kubectl", side_effect=kubectl), patch.object(spike, "wait_workers"), \
                        patch.object(spike.subprocess, "Popen", side_effect=process), \
                        patch.object(spike.time, "monotonic", return_value=0), patch.object(spike.time, "sleep"), \
                        patch.object(sys, "stdout", io.StringIO()):
                    spike.experiment(mode, output)
                restore = calls[-1][0]
                self.assertEqual(restore[:2], ("patch", "scaledobject/bifrost-worker"))
                restored = json.loads(restore[-1])
                self.assertEqual(restored["spec"], original["spec"])
                self.assertEqual(restored["metadata"]["annotations"]["autoscaling.keda.sh/paused-replicas"], "operator-value")
                self.assertTrue((output / "replicas.json").exists())
                if mode == "deadline":
                    restored_env = [json.loads(args[-1])[0]["value"] for args, _ in calls
                                    if args[:3] == ("patch", "deployment/bifrost-worker", "--type=json")]
                    self.assertEqual(restored_env, [[{"name": "EXISTING", "value": "preserved"}]])

    def test_failed_workload_restores_policy_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            def read(*args, **kwargs):
                if args[:2] == ("get", "scaledobject/bifrost-worker"):
                    return '{"spec":{"minReplicaCount":2},"metadata":{}}'
                if args[:2] == ("get", "deployment/bifrost-worker"):
                    return '{"spec":{"template":{"spec":{"containers":[{}]}}}}'
                if args[:2] == ("get", "pods"):
                    return '{"items":[]}'
                return '{}'
            with patch.object(spike, "kubectl", side_effect=read) as command, patch.object(spike, "wait_workers"), \
                    patch.object(spike.subprocess, "Popen", side_effect=lambda *args, **kwargs: WorkloadProcess(kwargs["stdout"], 1)), \
                    patch.object(spike.time, "monotonic", return_value=0), patch.object(spike.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "Workload failed"):
                    spike.experiment("warm", output)
            self.assertEqual(json.loads(command.call_args.args[-1])["spec"], {"minReplicaCount": 2})
            self.assertTrue((output / "replicas.json").exists())
            self.assertIn('"submitted"', (output / "workload.jsonl").read_text())

    def test_snapshot_skips_pending_and_deleting_pods(self):
        pods = [pod("running"), pod("pending", "Pending"), pod("deleting", deleting=True)]
        with patch.object(spike, "workers", return_value=pods), \
                patch.object(spike, "kubectl", return_value='{"current_bytes":123}') as command:
            result = spike.snapshot()
        self.assertEqual(result["samples"], [{"pod": "running", "current_bytes": 123}])
        self.assertEqual(command.call_count, 1)

    def test_worker_readiness_deadline_is_not_claimed_as_success(self):
        with patch.object(spike.time, "monotonic", side_effect=[0, 0, 181]), \
                patch.object(spike.time, "sleep"), patch.object(spike, "workers", return_value=[pod("deleting", deleting=True)]):
            with self.assertRaises(TimeoutError):
                spike.wait_workers(1)
        with patch.object(spike.time, "monotonic", return_value=0), patch.object(spike, "workers", return_value=[pod("ready")]):
            spike.wait_workers(1)

    def test_build_driver_keeps_memory_evidence_and_replaces_scheduler_only_after_overlap(self):
        for restart in (False, True):
            with self.subTest(restart=restart), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "result"
                def kubectl(*args):
                    if args[:2] == ("get", "pods"):
                        return json.dumps({"items": [pod("build"), pod("pending", "Pending")]})
                    if args[:1] == ("exec",):
                        return '{"current_bytes":12,"peak_bytes":15}'
                    return '{}'
                with patch.object(builds, "kubectl", side_effect=kubectl) as command, \
                        patch.object(builds.subprocess, "Popen", side_effect=lambda *args, **kwargs: WorkloadProcess(kwargs["stdout"], builds=True)), \
                        patch.object(builds.time, "monotonic", return_value=0), patch.object(builds.time, "sleep"), \
                        patch.object(sys, "stdout", io.StringIO()):
                    builds.run(output, restart)
                self.assertEqual(json.loads((output / "run.json").read_text())["scheduler_replaced"], restart)
                self.assertEqual(json.loads((output / "result.json").read_text()), {"success": True})
                deletes = [call for call in command.call_args_list if call.args[0] == "delete"]
                self.assertEqual(len(deletes), int(restart))
                self.assertEqual(len(json.loads((output / "memory.json").read_text())), 2)


class MemoryReportChecks(unittest.TestCase):
    def test_invalid_samples_and_unsettled_markers_cannot_certify_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "memory.csv"
            trace.write_text("ts_unix,rss_kb\n1,bad\n2,-1\n3,1024\n")
            self.assertEqual(benchmark._read_samples(trace), [{"ts_unix": 3.0, "rss_kb": 1024.0}])
        with self.assertRaises(ValueError):
            benchmark._nearest_sample([{"ts_unix": 0}], 3)
        self.assertFalse(benchmark._materially_monotonic([1, 2], 0))
        self.assertFalse(benchmark._materially_monotonic([1, 100, 2], 5))
        self.assertTrue(benchmark._materially_monotonic([1, 20, 40], 5))

    def test_driver_does_not_mask_test_failure_or_material_retained_growth(self):
        for status, growth, expected in ((0, 0, 0), (7, 0, 7), (0, 100, 1)):
            with self.subTest(status=status, growth=growth), tempfile.TemporaryDirectory() as directory:
                trace = Path(directory) / "memory.csv"
                trace.write_text("ts_unix,rss_kb,cgroup_bytes,working_set_bytes,inactive_anon_bytes\n" +
                                 "".join(f"{n},{1024 + n * growth * 1024},{1048576 + n * growth * 1048576},{1048576 + n * growth * 1048576},0\n" for n in range(5)))
                rounds = ["LARGE_TABLE_ROUND " + json.dumps({"settled_at_unix": n, "round_started_at_unix": n,
                           "query_ms": [1, 2], "health_max_ms": 1, "response_bytes": 100}) + "\n" for n in range(5)]
                sampler = SimpleNamespace(send_signal=lambda sig: None, wait=lambda timeout: 0)
                workload = SimpleNamespace(stdout=iter(rounds), wait=lambda: status)
                args = SimpleNamespace(label="fixture", trace_out=trace, interval=0.1, max_retained_mib=32)
                with patch.object(benchmark, "_parse_args", return_value=args), \
                        patch.object(benchmark.memory_sampler, "_detect_container", return_value="fixture-api"), \
                        patch.object(benchmark.subprocess, "Popen", side_effect=[sampler, workload]), \
                        patch.object(benchmark.time, "sleep"), patch.object(sys, "stdout", io.StringIO()), \
                        patch.object(sys, "stderr", io.StringIO()):
                    self.assertEqual(benchmark.main(), expected)
