"""Aggregate interleaved production performance evidence; PLAN slice-8 budgets."""
from __future__ import annotations

import argparse
from collections import defaultdict
import math
from pathlib import Path
from statistics import median

from .json_io import dumps, read_json, write_json
from .run_perf import PERF_FREQUENCIES, SCHEMA, require_identity
from .run_corpus import external_path

GATES = {"first_result_s": 1.20, "warm_sweep_s": 1.10}
METRICS = ("first_result_s", "first_sweep_s", "warm_sweep_s", "peak_rss_bytes")
DEFAULT_LOAD_THRESHOLD = .5  # one-minute load / logical CPU count


def positive(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return float(value)


def summary(values: list[float]) -> dict:
    return {"median": median(values), "min": min(values), "max": max(values), "n": len(values)}


def ranges_overlap(a: dict, b: dict) -> bool:
    return max(a["min"], b["min"]) <= min(a["max"], b["max"])


def identity_key(identity: dict) -> dict:
    return {"wg_commit": identity["wg_commit"]["stdout"],
            "distributions": identity["distributions"], "julia_path": identity["julia_path"],
            "julia_version": identity["julia_version"]["stdout"], "thread_env": identity["thread_env"]}


def validate_run(run: dict) -> None:
    if run.get("schema") != SCHEMA or run.get("error"):
        raise ValueError(f"Invalid/incomplete performance record: {run.get('error', 'schema')}")
    require_identity(run.get("identity", {}))
    identity = run["identity"]
    if (not run.get("environment_sha256")
            or identity.get("environment_sha256") != run["environment_sha256"]
            or identity.get("runtime_env") != run.get("environment")):
        raise ValueError("Child environment differs from the recorded launch environment")
    if run.get("case") not in PERF_FREQUENCIES or run.get("backend") not in ("cpu", "metal"):
        raise ValueError("Unknown performance case/backend")
    if run.get("route") not in ("hbb", "official") or run.get("precision") != "float32":
        raise ValueError("Performance requires HBB/official and production float32")
    if list(run.get("frequencies_hz", [])) != list(PERF_FREQUENCIES[run["case"]]):
        raise ValueError("Performance frequency axis differs from declared plan")
    for name in ("repetition", "sequence"):
        if type(run.get(name)) is not int or run[name] < 1:
            raise ValueError(f"Invalid {name}")
    for name in ("first_result_s", "first_sweep_s", "warm_sweep_s"):
        positive(run["timing"][name], name)
    if run["timing"]["first_result_s"] > run["timing"]["first_sweep_s"]:
        raise ValueError("First callback cannot follow first sweep return")
    if run["timing"].get("warm_host_reused") is not True:
        raise ValueError("Warm sweep must reuse the same production host")
    for name in ("julia_threads", "blas_threads"):
        value = run["timing"]["threads"][name]
        if type(value) is not int or value < 1:
            raise ValueError(f"Missing resolved {name}")
    memory = run["memory"]
    positive(memory["peak_rss_bytes"], "peak RSS")
    if memory.get("errors") or not memory.get("samples") or memory.get("interval_s") != .1:
        raise ValueError("Missing/erroring 100 ms whole-tree memory samples")
    if not run.get("mesh_sha256") or not run.get("request_sha256") or not run.get("comparison_environment_sha256"):
        raise ValueError("Missing input/environment identity")
    if not run.get("hosts") or len(run["hosts"]) != 1:
        raise ValueError("Missing retained production host evidence")
    positive(run["started_at_epoch_s"], "start time")
    if run["ended_at_epoch_s"] <= run["started_at_epoch_s"]:
        raise ValueError("Invalid invocation time interval")
    for state in (run["machine_before"], run["machine_after"]):
        positive(state["logical_cpus"], "logical CPUs")
        if len(state["load_average"]) != 3 or any(
                not math.isfinite(v) or v < 0 for v in state["load_average"]):
            raise ValueError("Missing finite load averages")
        if not state.get("power"):
            raise ValueError("Missing power state evidence")


def aggregate(runs: list[dict], *, load_threshold: float = DEFAULT_LOAD_THRESHOLD,
              expected_cases: tuple[str, ...] = tuple(PERF_FREQUENCIES)) -> dict:
    """Return invalid evidence as a nonpassing verdict, never discard bad rows."""
    report = {"schema": SCHEMA, "gates": GATES, "minimum_repetitions": 3,
              "load_threshold_per_logical_cpu": load_threshold, "cases": [], "errors": [],
              "passed": False, "status": "invalid", "record_count": len(runs),
              "comparison": "production routes", "matched_thread_counts": False}
    try:
        positive(load_threshold, "load threshold")
        if not runs:
            raise ValueError("No performance evidence")
        groups = defaultdict(list)
        identities, pids, sequences = set(), set(), set()
        route_envs = defaultdict(set)
        route_threads = defaultdict(set)
        for run in runs:
            validate_run(run)
            identities.add(dumps(identity_key(run["identity"])))
            pid = run["identity"]["pid"]
            if pid in pids or run["sequence"] in sequences:
                raise ValueError("Duplicate child PID or interleaving sequence")
            pids.add(pid)
            sequences.add(run["sequence"])
            route_envs[(run["backend"], run["route"])].add(run["comparison_environment_sha256"])
            route_threads[(run["backend"], run["route"])].add(tuple(
                run["timing"]["threads"][name] for name in ("julia_threads", "blas_threads")))
            groups[(run["case"], run["backend"])].append(run)
        if len(identities) != 1 or any(len(envs) != 1 for envs in route_envs.values()):
            raise ValueError("Mixed WG/engine/Julia/thread/environment identities")
        if any(len(counts) != 1 for counts in route_threads.values()):
            raise ValueError("Resolved Julia/BLAS thread counts must match within each backend/route across all cases and repetitions")
        chronological = sorted(runs, key=lambda r: r["started_at_epoch_s"])
        if [r["sequence"] for r in chronological] != sorted(sequences):
            raise ValueError("Actual acquisition order differs from interleaving plan")
        if any(a["ended_at_epoch_s"] > b["started_at_epoch_s"] for a, b in zip(chronological, chronological[1:])):
            raise ValueError("Overlapping performance jobs cannot qualify interleaving")
        backends = {run["backend"] for run in runs}
        if set(groups) != {(case, backend) for case in expected_cases for backend in backends}:
            raise ValueError("Missing required cases for each requested backend")
        for (case, backend), records in sorted(groups.items()):
            ordered = sorted(records, key=lambda r: r["sequence"])
            routes = {route: [r for r in ordered if r["route"] == route] for route in ("hbb", "official")}
            count = len(routes["hbb"])
            if count < 3 or len(routes["official"]) != count:
                raise ValueError(f"{case}/{backend}: N >= 3 equal repetitions per route required")
            if len({r["repetition"] for r in routes["hbb"]}) != count:
                raise ValueError("Duplicate repetition")
            if [r["repetition"] for r in routes["hbb"]] != [r["repetition"] for r in routes["official"]]:
                raise ValueError("Unmatched repetitions")
            if any(a["route"] == b["route"] for a, b in zip(ordered, ordered[1:])):
                raise ValueError("Routes must interleave A,B,A,B")
            if any(a["repetition"] != b["repetition"] for a, b in zip(ordered[::2], ordered[1::2])):
                raise ValueError("Interleaved adjacent jobs must be matched repetitions")
            for field in ("mesh_sha256", "request_sha256"):
                if len({r[field] for r in ordered}) != 1:
                    raise ValueError(f"Unmatched {field}")
            threads = {route: dict(zip(("julia_threads", "blas_threads"),
                                      next(iter(route_threads[(backend, route)]))))
                       for route in routes}
            matched_threads = threads["hbb"] == threads["official"]
            metrics = {}
            flags = []
            for metric in METRICS:
                stats = {route: summary([float(r["memory"][metric] if metric == "peak_rss_bytes"
                                              else r["timing"][metric]) for r in records])
                         for route, records in routes.items()}
                ratio = stats["official"]["median"] / stats["hbb"]["median"]
                metrics[metric] = dict(stats, official_over_hbb=ratio)
                if metric in GATES:
                    metrics[metric].update(limit=GATES[metric], passed=ratio <= GATES[metric])
                    if ranges_overlap(stats["hbb"], stats["official"]):
                        flags.append(f"{metric}: route ranges overlap")
            pairs = []
            for a, b in zip(ordered[::2], ordered[1::2]):
                load = max(state["load_average"][0] / state["logical_cpus"]
                           for r in (a, b) for state in (r["machine_before"], r["machine_after"]))
                noisy = load > load_threshold
                if noisy:
                    flags.append(f"repetition {a['repetition']}: normalized load {load:.3f} > {load_threshold:.3f}")
                power = [r[state]["power"] for r in (a, b) for state in ("machine_before", "machine_after")]
                if any(p.get("returncode") != 0 for p in power):
                    flags.append(f"repetition {a['repetition']}: power state unavailable")
                elif len({p.get("stdout") for p in power}) != 1:
                    flags.append(f"repetition {a['repetition']}: power state changed")
                pairs.append({"repetition": a["repetition"], "sequence": [a["sequence"], b["sequence"]],
                              "max_normalized_load": load, "high_load": noisy, "power_states": power})
            report["cases"].append({"case": case, "backend": backend, "repetitions_per_route": count,
                                    "metrics": metrics, "threads_by_route": threads,
                                    "matched_thread_counts": matched_threads,
                                    "pairs": pairs, "noisy": bool(flags), "noise_flags": flags,
                                    "passed": all(metrics[m]["passed"] for m in GATES)})
        report["matched_thread_counts"] = all(c["matched_thread_counts"] for c in report["cases"])
        report["passed"] = all(c["passed"] for c in report["cases"])
        report["noisy"] = any(c["noisy"] for c in report["cases"])
        report["status"] = "pass" if report["passed"] else "fail"
        report["identity"] = identity_key(runs[0]["identity"])
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        report["errors"].append(str(exc))
    return report


def render_markdown(report: dict) -> str:
    lines = ["# BEAT production performance — PLAN slice 8", "",
             f"Verdict: **{report['status'].upper()}**. Records: {report['record_count']}.", "",
             "Fixed median gates: first result official/HBB ≤ 1.20; warm sweep ≤ 1.10.",
             "Comparison: production routes with their observed thread policies; this is not a matched-thread benchmark.",
             "N ≥ 3 per route; ranges are min–max, not confidence intervals.",
             "Noise flags annotate the measured verdict; they never relax either budget.", "",
             "| Case / backend | N/route | Metric | HBB median [range] | Official median [range] | Ratio | Gate |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for case in report["cases"]:
        for name, metric in case["metrics"].items():
            divisor = 1024**2 if name == "peak_rss_bytes" else 1
            unit = "MiB" if name == "peak_rss_bytes" else "s"
            values = [f"{metric[r]['median']/divisor:.3f} [{metric[r]['min']/divisor:.3f}–{metric[r]['max']/divisor:.3f}] {unit}"
                      for r in ("hbb", "official")]
            gate = ("PASS" if metric["passed"] else "FAIL") if "passed" in metric else "record only"
            lines.append(f"| {case['case']} / {case['backend']} | {case['repetitions_per_route']} | {name} | "
                         f"{values[0]} | {values[1]} | {metric['official_over_hbb']:.4f} | {gate} |")
    for case in report["cases"]:
        lines.extend(["", f"{case['case']} / {case['backend']}: " + "; ".join(
            f"{route}: Julia {threads['julia_threads']}, BLAS {threads['blas_threads']}"
            for route, threads in case["threads_by_route"].items()) + ".", ""])
        lines.extend(f"- Noise: {flag}" for flag in case["noise_flags"])
    if report["errors"]:
        lines.extend(["", "Evidence refused:", ""])
        lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", type=Path, help="perf.json files from sequential broker jobs")
    parser.add_argument("--output", required=True, type=Path, help="external report directory")
    parser.add_argument("--load-threshold", type=float, default=DEFAULT_LOAD_THRESHOLD,
                        help="noise annotation: one-minute load / logical CPUs (default 0.5)")
    args = parser.parse_args(argv)
    output = external_path(args.output, "--output")
    output.mkdir(parents=True, exist_ok=True)
    try:
        report = aggregate([read_json(path) for path in args.records], load_threshold=args.load_threshold)
    except (OSError, ValueError) as exc:
        report = aggregate([])
        report["errors"] = [f"Cannot read evidence: {exc}"]
    report["record_files"] = [str(path.resolve()) for path in args.records]
    write_json(output / "verdict.json", report)
    (output / "verdict.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"{report['status']}: {output / 'verdict.md'}")
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
