#!/usr/bin/env python3
"""Inventory measured Waveguide Generator archive and retention payload sizes.

This script intentionally reports observations, not policy.  It reads existing
Workspace run directories and/or job databases without changing them.  Run it
from the repository root with the project's Python environment when database
rows contain retained pressure bases or radiation matrices; those two public
artifact measurements reuse the production readers.
"""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ArchiveMeasurement:
    job_id: str | None
    run_number: int | None
    label: str | None
    path: str
    file_count: int
    total_bytes: int
    result_json_bytes: int
    result_json_files: int
    mesh_bytes: int
    pressure_basis_bytes: int
    pressure_basis_files: int
    radiation_npz_bytes: int
    radiation_csv_bytes: int
    derived_bytes: int
    report_bytes: int
    ordinary_csv_bytes: int
    metadata_bytes: int
    other_bytes: int
    frequencies: int | None
    channels: int | None
    mesh_triangles: int | None
    mesh_vertices: int | None


@dataclass(frozen=True)
class DatabaseMeasurement:
    database: str
    job_id: str
    run_number: int
    label: str | None
    status: str
    results_bytes: int
    mesh_bytes: int
    stored_channel_bases_bytes: int
    public_pressure_basis_bytes: int
    public_pressure_basis_files: int
    radiation_npz_bytes: int
    snapshot_base64_characters: int
    snapshot_wire_bytes: int
    frequencies: int | None
    channels: int | None
    mesh_triangles: int | None
    mesh_vertices: int | None
    error: str | None


def _size(path: Path) -> int:
    return path.stat().st_size


def _integer(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _result_shape(path: Path) -> tuple[int | None, int | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, None
    result = payload.get("results", payload) if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        return None, None
    return _payload_shape(result)


def _payload_shape(result: dict[str, Any]) -> tuple[int | None, int | None]:
    channels = result.get("channels")
    if isinstance(channels, dict):
        frequency_counts = [
            len(channel.get("frequencies", []))
            for channel in channels.values()
            if isinstance(channel, dict) and isinstance(channel.get("frequencies"), list)
        ]
        return (max(frequency_counts) if frequency_counts else None), len(channels)
    frequencies = result.get("frequencies")
    return (len(frequencies) if isinstance(frequencies, list) else None), 1


def measure_archive(run_record: Path) -> ArchiveMeasurement:
    directory = run_record.parent
    try:
        record = json.loads(run_record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        record = {}
    record = record if isinstance(record, dict) else {}
    run = record.get("run") if isinstance(record.get("run"), dict) else {}
    mesh = record.get("mesh") if isinstance(record.get("mesh"), dict) else {}

    totals = {
        "result_json": 0,
        "mesh": 0,
        "pressure_basis": 0,
        "radiation_npz": 0,
        "radiation_csv": 0,
        "derived": 0,
        "report": 0,
        "ordinary_csv": 0,
        "metadata": 0,
        "other": 0,
    }
    result_files: list[Path] = []
    pressure_files = 0
    all_files = [path for path in directory.iterdir() if path.is_file()]
    for path in all_files:
        name = path.name
        size = _size(path)
        if name in {"run.json", "design.json"}:
            totals["metadata"] += size
        elif "_derived_acoustics." in name:
            totals["derived"] += size
        elif name.endswith("_report.html"):
            totals["report"] += size
        elif name.endswith("_pressure_basis.npz"):
            totals["pressure_basis"] += size
            pressure_files += 1
        elif name.endswith("_radiation_impedance.npz"):
            totals["radiation_npz"] += size
        elif name.endswith("_radiation_impedance.csv"):
            totals["radiation_csv"] += size
        elif path.suffix.lower() == ".msh":
            totals["mesh"] += size
        elif path.suffix.lower() == ".json":
            totals["result_json"] += size
            result_files.append(path)
        elif path.suffix.lower() == ".csv":
            totals["ordinary_csv"] += size
        else:
            totals["other"] += size

    shapes = [_result_shape(path) for path in result_files]
    frequency_counts = [count for count, _channels in shapes if count is not None]
    explicit_channels = [count for _frequencies, count in shapes if count is not None]
    # Per-channel archive JSON produces one file per channel.  A combined JSON
    # payload carries its own channel count instead.
    channels = max(explicit_channels, default=None)
    if len(result_files) > 1 and channels == 1:
        channels = len(result_files)

    return ArchiveMeasurement(
        job_id=str(run["jobId"]) if run.get("jobId") else None,
        run_number=_integer(run.get("number")),
        label=str(run["label"]) if run.get("label") is not None else None,
        path=str(directory),
        file_count=len(all_files),
        total_bytes=sum(_size(path) for path in all_files),
        result_json_bytes=totals["result_json"],
        result_json_files=len(result_files),
        mesh_bytes=totals["mesh"],
        pressure_basis_bytes=totals["pressure_basis"],
        pressure_basis_files=pressure_files,
        radiation_npz_bytes=totals["radiation_npz"],
        radiation_csv_bytes=totals["radiation_csv"],
        derived_bytes=totals["derived"],
        report_bytes=totals["report"],
        ordinary_csv_bytes=totals["ordinary_csv"],
        metadata_bytes=totals["metadata"],
        other_bytes=totals["other"],
        frequencies=max(frequency_counts, default=None),
        channels=channels,
        mesh_triangles=_integer(mesh.get("triangle_count")),
        mesh_vertices=_integer(mesh.get("vertex_count")),
    )


def scan_archives(roots: Iterable[Path]) -> list[ArchiveMeasurement]:
    records = {
        path.resolve()
        for root in roots
        if root.exists()
        for path in root.rglob("run.json")
    }
    return sorted(
        (measure_archive(path) for path in records),
        key=lambda item: (item.total_bytes, item.path),
    )


def _loads_object(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def measure_database(path: Path, job_ids: set[str]) -> list[DatabaseMeasurement]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    query = """
        SELECT simulation_jobs.id,
               job_identity.run_number,
               simulation_jobs.status,
               simulation_jobs.label,
               simulation_jobs.mesh_stats_json,
               simulation_results.results_json,
               simulation_artifacts.msh_text,
               simulation_channel_bases.bases_npz,
               simulation_radiation_impedance.matrix_npz
          FROM simulation_jobs
          JOIN job_identity ON job_identity.job_id = simulation_jobs.id
          LEFT JOIN simulation_results
            ON simulation_results.job_id = simulation_jobs.id
          LEFT JOIN simulation_artifacts
            ON simulation_artifacts.job_id = simulation_jobs.id
          LEFT JOIN simulation_channel_bases
            ON simulation_channel_bases.job_id = simulation_jobs.id
          LEFT JOIN simulation_radiation_impedance
            ON simulation_radiation_impedance.job_id = simulation_jobs.id
         WHERE simulation_jobs.status = 'complete'
    """
    rows = connection.execute(query).fetchall()
    connection.close()

    from server.jobs.radiation_impedance import radiation_impedance_presentation
    from server.solver.combine import deserialize_channel_bases
    from server.solver.pressure_basis import export_pressure_basis

    measured: list[DatabaseMeasurement] = []
    for row in rows:
        job_id = str(row["id"])
        if job_ids and job_id not in job_ids:
            continue
        result_text = str(row["results_json"] or "")
        results = _loads_object(result_text)
        mesh_text = str(row["msh_text"] or "")
        bases = bytes(row["bases_npz"]) if row["bases_npz"] is not None else b""
        radiation = bytes(row["matrix_npz"]) if row["matrix_npz"] is not None else b""
        pressure_bases: list[dict[str, str]] = []
        radiation_snapshot: dict[str, Any] | None = None
        error: str | None = None
        try:
            if bases:
                bundle = deserialize_channel_bases(bases)
                for channel_id in bundle["channel_ids"]:
                    exported = export_pressure_basis(bases, results, str(channel_id))
                    pressure_bases.append(
                        {
                            "channel_id": exported.channel_id,
                            "content_base64": base64.b64encode(exported.content).decode("ascii"),
                        }
                    )
            if radiation:
                radiation_snapshot = {
                    "content_base64": base64.b64encode(radiation).decode("ascii"),
                    "presentation": radiation_impedance_presentation(radiation),
                }
            snapshot = {
                "schema_version": 1,
                "results": results,
                # The digest has fixed width; its value does not affect byte size.
                "results_sha256": "0" * 64,
                "mesh_artifact": mesh_text or None,
                "pressure_bases": pressure_bases,
                "radiation_impedance": radiation_snapshot,
            }
            snapshot_bytes = len(
                json.dumps(snapshot, allow_nan=False).encode("utf-8")
            )
        except (KeyError, TypeError, ValueError) as exc:
            error = str(exc)
            snapshot_bytes = 0

        mesh_stats = _loads_object(row["mesh_stats_json"])
        frequencies, channels = _payload_shape(results)
        measured.append(
            DatabaseMeasurement(
                database=str(path),
                job_id=job_id,
                run_number=int(row["run_number"]),
                label=str(row["label"]) if row["label"] is not None else None,
                status=str(row["status"]),
                results_bytes=len(result_text.encode("utf-8")),
                mesh_bytes=len(mesh_text.encode("utf-8")),
                stored_channel_bases_bytes=len(bases),
                public_pressure_basis_bytes=sum(
                    len(base64.b64decode(item["content_base64"]))
                    for item in pressure_bases
                ),
                public_pressure_basis_files=len(pressure_bases),
                radiation_npz_bytes=len(radiation),
                snapshot_base64_characters=sum(
                    len(item["content_base64"]) for item in pressure_bases
                )
                + (
                    len(radiation_snapshot["content_base64"])
                    if radiation_snapshot
                    else 0
                ),
                snapshot_wire_bytes=snapshot_bytes,
                frequencies=frequencies,
                channels=channels,
                mesh_triangles=_integer(mesh_stats.get("triangle_count")),
                mesh_vertices=_integer(mesh_stats.get("vertex_count")),
                error=error,
            )
        )
    return sorted(measured, key=lambda item: (item.snapshot_wire_bytes, item.job_id))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", action="append", type=Path, default=[])
    parser.add_argument("--database", action="append", type=Path, default=[])
    parser.add_argument(
        "--job-id", action="append", default=[], help="limit database output"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    archives = scan_archives(args.archives)
    database_rows = [
        row
        for database in args.database
        for row in measure_database(database, set(args.job_id))
    ]
    payload = {
        "archives": [asdict(row) for row in archives],
        "database_rows": [asdict(row) for row in database_rows],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for group, rows in payload.items():
        print(group)
        for row in rows:
            print(json.dumps(row, sort_keys=True))


if __name__ == "__main__":
    main()
