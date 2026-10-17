#!/usr/bin/env python3
"""Benchmark installed checkers and Vite in disposable copies; write JSON to stdout.

Linux, Python 3 and GNU time required. Installs nothing. Example from repo root:
python3 scripts/benchmarks/typescript.py --client client \
  --compiler 'go=node /absolute/client/node_modules/typescript7/bin/tsc' \
  --compiler 'rust=/absolute/tsc-rs/tsc' > /tmp/typescript-results.json
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor


def summarize(values):
    """Return nearest-rank p95 and dispersion for repeated measurements."""
    ordered = sorted(values)
    return {"median": statistics.median(values), "p95": ordered[math.ceil(.95 * len(values)) - 1],
            "min": min(values), "max": max(values), "stdev": statistics.stdev(values),
            "mad": statistics.median(abs(v - statistics.median(values)) for v in values)}


def run(command, cwd, timeout=180):
    """Time an argv command, reject failures and kill its process group on timeout."""
    with tempfile.TemporaryDirectory() as scratch:
        metrics = Path(scratch) / "time"
        start = time.perf_counter()
        process = subprocess.Popen(["/usr/bin/time", "-f", "%M %U %S", "-o", str(metrics),
                                    *command], cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        elapsed = time.perf_counter() - start
        rss, user, system = map(float, metrics.read_text().splitlines()[-1].split())
        output = (stdout + stderr).decode(errors="replace").replace(str(cwd), "<client>")
        if process.returncode != 0:
            raise RuntimeError(f"Command {shlex.join(command)} exited {process.returncode}:\n{output}")
        # Preserve complete messages, including multiline continuations; sort blocks only.
        blocks = re.split(r"(?=^[^\n]*error TS\d+:)", output, flags=re.MULTILINE)
        diagnostics = sorted(b.strip() for b in blocks if re.search(r"error TS\d+:", b))
        return {"seconds": elapsed, "rss_kib": rss, "cpu_seconds": user + system,
                "exit": process.returncode, "diagnostics": diagnostics,
                "output_sha256": hashlib.sha256(output.encode()).hexdigest(), "output": output}


def clear_build_cache(root):
    """Remove only project-root TypeScript metadata in the disposable source copy."""
    for path in root.glob("*.tsbuildinfo"):
        path.unlink()


def sample_rss(stop, peak):
    """Sum descendant process RSS every 50 ms (shared pages counted per process)."""
    while not stop.is_set():
        processes = {}
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text().rsplit(")", 1)[1].split()
                processes[int(path.parent.name)] = (int(fields[1]), int(fields[21]))
            except (OSError, ValueError, IndexError):
                continue  # Processes can exit between directory listing and reading.
        descendants = {os.getpid()}
        while True:
            expanded = descendants | {pid for pid, (parent, _) in processes.items() if parent in descendants}
            if expanded == descendants:
                break
            descendants = expanded
        rss = sum(processes[pid][1] for pid in descendants if pid in processes and pid != os.getpid())
        peak[0] = max(peak[0], rss * os.sysconf("SC_PAGE_SIZE") / 1024)
        stop.wait(.05)


SCENARIOS = {
    "app-full": ["-p", "tsconfig.app.json", "--pretty", "false"],
    "node-full": ["-p", "tsconfig.node.json", "--pretty", "false"],
    "build-full": ["-b", "--force", "--pretty", "false"],
    "build-cache-cold": ["-b", "--incremental", "--pretty", "false"],
    "build-incremental": ["-b", "--pretty", "false"],
    "build-incremental-enabled": ["-b", "--incremental", "--pretty", "false"],
}


def compiler_order(compilers, seed, scenario, iteration):
    """Vary execution order reproducibly; no credentials or random generator involved."""
    return sorted(compilers, key=lambda name: hashlib.sha256(
        f"{seed}/{scenario}/{iteration}/{name}".encode()).digest())


def case_result(samples, metrics):
    """Summarize successful measurements while retaining their raw evidence."""
    return {"summary": {metric: summarize([s[metric] for s in samples]) for metric in metrics},
            "samples": samples}


def checker_sample(root, command, scenario, flags):
    """Reset metadata and, for warm incremental cases, prime outside the timed run."""
    clear_build_cache(root)
    if scenario.startswith("build-incremental"):
        run([*command, *flags], root)
    return run([*command, *flags], root)


def measure_checkers(root, compilers, scenarios, args, report):
    """Discard separate warmups and interleave checkers before summarizing."""
    for scenario, flags in scenarios.items():
        print(f"Measuring {scenario}", file=sys.stderr, flush=True)
        collected = {name: [] for name in compilers}
        for iteration in range(args.warmups + args.runs):
            for name in compiler_order(compilers, report["seed"], scenario, iteration):
                sample = checker_sample(root, compilers[name], scenario, flags)
                if iteration >= args.warmups:
                    collected[name].append(sample)
        for name, samples in collected.items():
            report["cases"][f"{name}/{scenario}"] = case_result(
                samples, ["seconds", "rss_kib", "cpu_seconds"])


def pipeline_children(mode, checker, bundle, root):
    """Require every command to succeed; sequential failures prevent bundling."""
    if mode == "bundle":
        return [run(bundle, root)]
    check = [*checker, "-b", "--pretty", "false"]
    if mode == "sequential":
        return [run(check, root), run(bundle, root)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, command, root) for command in [check, bundle]]
        return [future.result() for future in futures]


def pipeline_sample(mode, checker, bundle, root):
    """Measure a clean build and always stop the process-tree memory sampler."""
    clear_build_cache(root)
    shutil.rmtree(root / "dist", ignore_errors=True)
    stop, peak = threading.Event(), [0]
    sampler = threading.Thread(target=sample_rss, args=(stop, peak), daemon=True)
    sampler.start()
    try:
        start = time.perf_counter()
        children = pipeline_children(mode, checker, bundle, root)
        child_rss = [child["rss_kib"] for child in children]
        return {"seconds": time.perf_counter() - start, "exits": [c["exit"] for c in children],
                "cpu_seconds": sum(c["cpu_seconds"] for c in children),
                "sampled_tree_rss_kib": peak[0],
                "child_rss_estimate_kib": sum(child_rss) if mode == "concurrent" else max(child_rss)}
    finally:
        stop.set()
        sampler.join()


def measure_pipelines(root, compilers, client, args, report):
    """Compare bundle-only, sequential and concurrent pipelines with fresh metadata."""
    checker = next(iter(compilers.values()))
    bundle = ["node", str(client / "node_modules/vite/bin/vite.js"), "build"]
    for mode in ["bundle", "sequential", "concurrent"]:
        print(f"Measuring pipeline/{mode}", file=sys.stderr, flush=True)
        samples = []
        for iteration in range(args.warmups + args.runs):
            sample = pipeline_sample(mode, checker, bundle, root)
            if iteration >= args.warmups:
                samples.append(sample)
        report["cases"][f"pipeline/{mode}"] = case_result(
            samples, ["seconds", "child_rss_estimate_kib", "sampled_tree_rss_kib", "cpu_seconds"])


def main():
    """Validate the CLI, isolate source/cache writes and emit only successful results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--compiler", action="append", required=True, help="name=command (absolute executable paths)")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--builds", action="store_true", help="also benchmark Vite and sequential/concurrent Go builds")
    parser.add_argument("--pipelines-only", action="store_true", help="measure build pipelines without repeating compiler comparisons")
    parser.add_argument("--scenario", action="append", help="restrict compiler scenarios (e.g. build-incremental-enabled)")
    args = parser.parse_args()
    if args.runs < 10 or args.warmups < 1:
        parser.error("Use at least ten measurements and one separate warmup")
    if args.scenario and set(args.scenario) - SCENARIOS.keys():
        parser.error("Unknown scenario: " + ", ".join(sorted(set(args.scenario) - SCENARIOS.keys())))
    client = args.client.resolve()
    compilers = {name: shlex.split(command) for name, command in (value.split("=", 1) for value in args.compiler)}
    report = {"runs": args.runs, "warmups": args.warmups, "seed": 20261008,
              "order_algorithm": "sha256(seed/scenario/iteration/compiler)", "cases": {}, "versions": {}}
    with tempfile.TemporaryDirectory(prefix="bifrost-ts-bench-") as temp:
        root = Path(temp) / "client"
        shutil.copytree(client, root, ignore=shutil.ignore_patterns("node_modules", "dist", "*.tsbuildinfo", ".git"))
        (root / "node_modules").symlink_to(client / "node_modules", target_is_directory=True)
        for name, command in compilers.items():
            report["versions"][name] = run([*command, "--version"], root, timeout=30)["output"].strip()
        scenarios = {name: flags for name, flags in SCENARIOS.items() if not args.scenario or name in args.scenario}
        if not args.pipelines_only:
            measure_checkers(root, compilers, scenarios, args, report)
        if args.builds or args.pipelines_only:
            measure_pipelines(root, compilers, client, args, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
