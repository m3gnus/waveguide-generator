"""Measure cold/warm canonical viewport latency and WGF0 frame encoding."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SPIKE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_ROOT.parent
V1_ROOT = REPO_ROOT.parent / "Waveguide Generator"
V1_SERVER = V1_ROOT / "server"
PAYLOAD_DIR = SPIKE_ROOT / "payloads"
RESULTS_DIR = SPIKE_ROOT / "results"
FAMILIES = ("osse", "rosse", "icw", "freeform")
FORMULA_NAMES = {
    "osse": "OSSE",
    "rosse": "R-OSSE",
    "icw": "ICW",
    "freeform": "FREEFORM",
}
LODS = {
    "coarse": {"n_angular": 8, "n_length": 4},
    "fine": {"n_angular": 96, "n_length": 48},
}


def _payload(family: str, lod: str) -> dict[str, Any]:
    path = PAYLOAD_DIR / f"{family}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"missing {path}; run payloads/extract_payloads.py with the v1 Python first"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(LODS[lod])
    return payload


def _load_builder() -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    sys.path.insert(0, str(V1_SERVER))
    from solver.mesher_adapter import build_viewport_geometry

    return build_viewport_geometry


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot calculate a percentile of no values")
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _stats(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50Ms": _percentile(values, 50),
        "p95Ms": _percentile(values, 95),
        "maxMs": max(values),
    }


def _cold_worker(family: str, lod: str) -> int:
    payload = _payload(family, lod)
    import_started = time.perf_counter()
    builder = _load_builder()
    import_ms = (time.perf_counter() - import_started) * 1000.0
    eval_started = time.perf_counter()
    result = builder(payload)
    eval_ms = (time.perf_counter() - eval_started) * 1000.0
    grid = result.get("grid") or {}
    print(
        json.dumps(
            {
                "importMs": import_ms,
                "evalMs": eval_ms,
                "gridNPhi": int(grid.get("grid_n_phi") or 0),
                "gridNLength": int(grid.get("grid_n_length") or 0),
            },
            separators=(",", ":"),
        )
    )
    return 0


def _measure_cold(family: str, lod: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--cold-worker",
        family,
        "--cold-lod",
        lod,
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    total_ms = (time.perf_counter() - started) * 1000.0
    if completed.returncode:
        raise RuntimeError(
            f"cold subprocess failed ({completed.returncode}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("cold subprocess produced no result")
    details = json.loads(lines[-1])
    details["processMs"] = total_ms
    details["startupOverheadMs"] = max(
        0.0, total_ms - float(details["importMs"]) - float(details["evalMs"])
    )
    return details


def _measure_warm(
    builder: Callable[[Mapping[str, Any]], dict[str, Any]],
    family: str,
    lod: str,
    runs: int,
) -> tuple[dict[str, float], dict[str, float | int], dict[str, int]]:
    from frame_codec import encode, grid_to_mesh

    payload = _payload(family, lod)
    builder(payload)  # Explicit unmeasured family/LOD warm-up.
    timings: list[float] = []
    result: dict[str, Any] = {}
    for _ in range(runs):
        started = time.perf_counter()
        result = builder(payload)
        timings.append((time.perf_counter() - started) * 1000.0)

    encode_timings: list[float] = []
    frame = b""
    for sequence in range(runs):
        started = time.perf_counter()
        frame = encode(sequence, timings[-1], grid_to_mesh(result))
        encode_timings.append((time.perf_counter() - started) * 1000.0)
    grid = result.get("grid") or {}
    return (
        _stats(timings),
        {**_stats(encode_timings), "bytes": len(frame)},
        {
            "nPhi": int(grid.get("grid_n_phi") or 0),
            "nLength": int(grid.get("grid_n_length") or 0),
        },
    )


def _fmt(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Preview timing spike",
        "",
        f"Generated: `{report['generatedAt']}`  ",
        f"Platform: `{report['platform']}`  ",
        f"Python: `{report['python']}`  ",
        f"Warm iterations per case: `{report['warmRuns']}`",
        "",
        (
            "Cold process includes interpreter startup, imports, payload load, and the first "
            "viewport call. Import and first-call evaluation are also shown separately. "
            "Frame encoding includes point-grid tessellation plus WGF0 serialization."
        ),
        "",
        "| Family | LOD | Grid | Cold process ms | Cold import ms | Cold eval ms | Warm p50 ms | Warm p95 ms | Warm max ms | Frame p50 ms | Frame bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in report["cases"]:
        if case.get("error"):
            lines.append(
                f"| {case['family']} | {case['lod']} | ERROR | — | — | — | — | — | — | — | — |"
            )
            continue
        cold = case["cold"]
        warm = case["warm"]
        frame = case["frameEncode"]
        grid = case["grid"]
        lines.append(
            "| {family} | {lod} | {nphi}×{nlength} | {cold_process} | {cold_import} | "
            "{cold_eval} | {warm_p50} | {warm_p95} | {warm_max} | {frame_p50} | "
            "{frame_bytes} |".format(
                family=case["family"],
                lod=case["lod"],
                nphi=grid["nPhi"],
                nlength=grid["nLength"],
                cold_process=_fmt(cold["processMs"]),
                cold_import=_fmt(cold["importMs"]),
                cold_eval=_fmt(cold["evalMs"]),
                warm_p50=_fmt(warm["p50Ms"]),
                warm_p95=_fmt(warm["p95Ms"]),
                warm_max=_fmt(warm["maxMs"]),
                frame_p50=_fmt(frame["p50Ms"]),
                frame_bytes=frame["bytes"],
            )
        )
    errors = [case for case in report["cases"] if case.get("error")]
    if errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {case['family']} {case['lod']}: {case['error']}" for case in errors)
    return "\n".join(lines) + "\n"


def run_benchmark(warm_runs: int) -> int:
    cases: list[dict[str, Any]] = []
    builder: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None
    for family in FAMILIES:
        for lod in LODS:
            case: dict[str, Any] = {
                "family": FORMULA_NAMES[family],
                "payload": family,
                "lod": lod,
            }
            try:
                case["cold"] = _measure_cold(family, lod)
                if builder is None:
                    builder = _load_builder()
                warm, frame, grid = _measure_warm(builder, family, lod, warm_runs)
                case["warm"] = warm
                case["frameEncode"] = frame
                case["grid"] = grid
            except Exception as error:  # noqa: BLE001 - report every matrix failure
                case["error"] = f"{type(error).__name__}: {error}"
            cases.append(case)
            print(
                f"{case['family']:8s} {lod:6s} "
                + (f"ERROR {case['error']}" if case.get("error") else f"p50={case['warm']['p50Ms']:.2f} ms")
            )

    report = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "pythonExecutable": sys.executable,
        "warmRuns": warm_runs,
        "lods": LODS,
        "cases": cases,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "preview-timings.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (RESULTS_DIR / "preview-timings.md").write_text(_markdown(report), encoding="utf-8")
    return 1 if any(case.get("error") for case in cases) else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warm-runs",
        type=int,
        default=40,
        help="measured in-process calls per family/LOD (default: 40)",
    )
    parser.add_argument("--cold-worker", choices=FAMILIES, help=argparse.SUPPRESS)
    parser.add_argument("--cold-lod", choices=tuple(LODS), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.warm_runs < 1:
        parser.error("--warm-runs must be at least 1")
    if (args.cold_worker is None) != (args.cold_lod is None):
        parser.error("--cold-worker and --cold-lod must be supplied together")
    return args


def main() -> int:
    args = _parse_args()
    if args.cold_worker:
        return _cold_worker(args.cold_worker, args.cold_lod)
    return run_benchmark(args.warm_runs)


if __name__ == "__main__":
    raise SystemExit(main())
