"""Opt-in broker-run analytic gates; independent of production engine choices."""

from __future__ import annotations

import os
from pathlib import Path

from server.solver.beat_runtime.discovery import JULIA_ENV_VAR
from .recorder import write_record
from .runners import output_directory
from .reference_support import expected_engine_revision


def run_references(*, output_dir, selection="all", julia=None):
    if selection not in {"all", "sphere", "lem-sphere"}:
        raise ValueError("Unknown analytic reference selection")
    directory = output_directory(Path(output_dir))
    directory.mkdir(parents=True, exist_ok=True)
    executable = str(julia) if julia else os.environ.get(JULIA_ENV_VAR, "").strip()
    if not executable:
        raise ValueError("Analytic references require an explicit selected Julia")
    from .reference_sphere import run_reference as sphere
    from .reference_lem_sphere import run_reference as lem

    required = {"sphere": sphere, "lem-sphere": lem}
    reports = {}
    failures = {}
    summary = {
        "schema_version": 1,
        "kind": "analytic_reference_qualification",
        "expected_engine_revision": expected_engine_revision(),
        "backend": "cpu",
        "precision": "float64",
        "amplitude_convention": "rms",
        "production_mapping": "unchanged acceleration and mean-pressure conventions",
        "coupled_feedback_resistance_qualified": False,
        "reports": reports,
        "failures": failures,
        "passed": False,
    }
    try:
        for name, run in required.items():
            if selection not in {"all", name}:
                continue
            try:
                reports[name] = run(julia=executable, out=directory / name)
            except Exception as exc:
                failures[name] = {"failure_type": type(exc).__name__}
        missing = sorted(set(required) - set(reports))
        summary["missing_required_references"] = missing
        summary["passed"] = (
            not failures and not missing and all(r.get("passed") is True for r in reports.values())
        )
        return summary
    finally:
        write_record(directory / "analytic-references.json", summary)
