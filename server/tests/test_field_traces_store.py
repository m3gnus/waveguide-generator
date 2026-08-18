from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pytest

from server.jobs.store import JobStore
from server.mesh.artifact import mesh_text_sha256
from server.solver.field_traces_store import (
    ArtifactCorrupt,
    FieldTraceArtifact,
    FieldTraceChannel,
    load_field_traces,
    write_field_traces,
)


MESH_TEXT = """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
4
1 0 0 0
2 1 0 0
3 0 1 0
4 0 0 1
$EndNodes
$Elements
2
1 2 2 1 1 1 2 3
2 2 2 1 1 1 3 4
$EndElements
"""


def _artifact() -> FieldTraceArtifact:
    frequencies = np.asarray([100.0, 250.0], dtype=np.float64)
    left_p = np.asarray(
        [
            [1 + 2j, 3 + 4j, 5 + 6j, 7 + 8j],
            [9 + 10j, 11 + 12j, 13 + 14j, 15 + 16j],
        ],
        dtype=np.complex128,
    )
    left_q = np.asarray(
        [[17 + 18j, 19 + 20j], [21 + 22j, 23 + 24j]],
        dtype=np.complex128,
    )
    return FieldTraceArtifact(
        mesh_text=MESH_TEXT,
        frequencies_hz=frequencies,
        k_real=np.asarray([1.5, 3.75]),
        k_imag=np.asarray([0.0075, 0.01875]),
        symmetry_plane="yz",
        solve_path="full-3d",
        channels=(
            FieldTraceChannel("left", left_p, left_q),
            FieldTraceChannel("right", left_p * (2 - 1j), left_q * (2 - 1j)),
        ),
    )


def _job(job_id: str, *, completed_at: str) -> dict:
    return {
        "id": job_id,
        "status": "complete",
        "created_at": completed_at,
        "updated_at": completed_at,
        "queued_at": completed_at,
        "completed_at": completed_at,
        "progress": 1.0,
        "stage": "complete",
        "stage_message": "complete",
        "config_json": {"design": {"formula": "OSSE", "L": 120}},
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    }


def test_field_trace_round_trip_preserves_offsets_dtype_and_geometry_hash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "field-traces"
    stored = write_field_traces("round-trip", _artifact(), artifact_root=root)

    metadata = json.loads((stored.path / "meta.json").read_text(encoding="utf-8"))
    assert metadata["version"] == 1
    assert metadata["geometry_sha256"] == mesh_text_sha256(MESH_TEXT)
    assert metadata["dof_counts"] == {"p1": 4, "dp0": 2}
    assert metadata["channel_ids"] == ["left", "right"]
    assert metadata["frequency_slices"] == [
        {
            "index": 0,
            "p_offset": 0,
            "p_bytes": 32,
            "q_offset": 32,
            "q_bytes": 16,
            "end_offset": 48,
        },
        {
            "index": 1,
            "p_offset": 48,
            "p_bytes": 32,
            "q_offset": 80,
            "q_bytes": 16,
            "end_offset": 96,
        },
    ]
    assert metadata["total_bytes"] == 192
    assert stored.bytes == 192

    mesh_text, frequency_hz, k_real, symmetry, pressure, neumann = load_field_traces(
        "round-trip",
        1,
        "right",
        artifact_root=root,
    )
    expected = _artifact().channels[1]
    assert mesh_text == MESH_TEXT
    assert frequency_hz == 250.0
    assert k_real == 3.75
    assert symmetry == "yz"
    assert pressure.dtype == np.complex64
    assert neumann.dtype == np.complex64
    np.testing.assert_array_equal(pressure, expected.pressure_p1[1].astype(np.complex64))
    np.testing.assert_array_equal(neumann, expected.neumann_dp0[1].astype(np.complex64))

    (stored.path / "mesh.msh").write_text(MESH_TEXT + "\n", encoding="utf-8")
    with pytest.raises(ArtifactCorrupt, match="geometry sha256"):
        load_field_traces("round-trip", 0, "left", artifact_root=root)


@pytest.mark.parametrize("damage", ["meta", "binary"])
def test_corrupt_metadata_and_truncated_binary_raise_typed_error(
    tmp_path: Path,
    damage: str,
) -> None:
    root = tmp_path / "field-traces"
    stored = write_field_traces(damage, _artifact(), artifact_root=root)
    if damage == "meta":
        (stored.path / "meta.json").write_text("{not-json", encoding="utf-8")
    else:
        channel_path = stored.path / "channel-0000.bin"
        with channel_path.open("r+b") as stream:
            stream.truncate(24)

    with pytest.raises(ArtifactCorrupt):
        load_field_traces(damage, 0, "left", artifact_root=root)


def test_result_retention_prunes_field_trace_row_and_directory(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    completed_at = "2000-01-01T00:00:00"
    store.create_job(_job("pruned", completed_at=completed_at))
    store.store_results(
        "pruned",
        {
            "metadata": {
                "field_plane_available": True,
                "field_trace_bytes": 192,
                "unavailable_reason": None,
            }
        },
    )
    stored = store.store_field_traces("pruned", _artifact())
    assert stored is not None
    assert stored.path.is_dir()
    assert store.get_field_trace_record("pruned") is not None

    assert store.prune_terminal_jobs(retention_days=30) == 1

    assert store.get_field_trace_record("pruned") is None
    assert not stored.path.exists()
    metadata = store.get_job_row("pruned")["task_metadata"]
    assert metadata["field_plane_available"] is False
    assert metadata["field_trace_bytes"] is None
    assert metadata["unavailable_reason"] == "artifact_pruned"


def test_explicit_job_deletion_removes_field_trace_directory(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    completed_at = datetime.now().isoformat()
    store.create_job(_job("deleted", completed_at=completed_at))
    stored = store.store_field_traces("deleted", _artifact())
    assert stored is not None

    deleted, event = store.delete_job_with_event("deleted")

    assert deleted is True
    assert event is not None
    assert not stored.path.exists()
    assert store.get_field_trace_record("deleted") is None
