"""Portable radiation-package contracts: readiness, integrity, determinism."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
import zipfile

import numpy as np
import pytest

from server.cli.args import main
from server.design.conventions import artifact_conventions
from server.exports.radiation_package import (
    MANIFEST_MEMBER,
    MESH_MEMBER,
    PACKAGE_MEMBER_ORDER,
    RADIATION_PACKAGE_SCHEMA,
    RADIATION_PACKAGE_VERSION,
    TRACES_MEMBER,
    build_radiation_package,
    validate_radiation_package,
)
from server.jobs.store import JobStore
from server.mesh.artifact import mesh_text_sha256
from server.solver.field_traces_store import (
    BEMPP_FIELD_TRACE_BACKEND,
    FieldTraceArtifact,
    FieldTraceChannel,
    METAL_FIELD_TRACE_BACKEND,
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

FREQUENCIES = [100.0, 250.0]
CREATED_AT = "2026-08-18T09:15:30"

LEFT_P = np.asarray(
    [
        [1 + 2j, 3 + 4j, 5 + 6j, 7 + 8j],
        [9 + 10j, 11 + 12j, 13 + 14j, 15 + 16j],
    ],
    dtype=np.complex128,
)
LEFT_Q = np.asarray(
    [[17 + 18j, 19 + 20j], [21 + 22j, 23 + 24j]],
    dtype=np.complex128,
)


def _artifact(
    *,
    multi: bool = False,
    backend: str = METAL_FIELD_TRACE_BACKEND,
    frequencies: list[float] | None = None,
) -> FieldTraceArtifact:
    grid = np.asarray(frequencies or FREQUENCIES, dtype=np.float64)
    count = int(grid.size)
    channels = [FieldTraceChannel("default", LEFT_P[:count], LEFT_Q[:count])]
    if multi:
        channels = [
            FieldTraceChannel("left", LEFT_P[:count], LEFT_Q[:count]),
            FieldTraceChannel(
                "right", LEFT_P[:count] * (2 - 1j), LEFT_Q[:count] * (2 - 1j)
            ),
        ]
    return FieldTraceArtifact(
        mesh_text=MESH_TEXT,
        frequencies_hz=grid,
        k_real=np.asarray([1.5, 3.75][:count]),
        k_imag=np.asarray([0.0075, 0.01875][:count]),
        symmetry_plane="yz",
        solve_path="full-3d",
        channels=tuple(channels),
        backend=backend,
    )


def _store(tmp_path: Path, name: str = "data") -> JobStore:
    store = JobStore.for_data_dir(tmp_path / name)
    store.initialize()
    return store


def _create_job(
    store: JobStore,
    job_id: str = "job-1",
    *,
    status: str = "complete",
    traces: bool = True,
    multi: bool = False,
    unavailable_reason: str | None = None,
    result_frequencies: list[float] | None = None,
    trace_frequencies: list[float] | None = None,
    backend: str = METAL_FIELD_TRACE_BACKEND,
) -> None:
    geometry: dict[str, Any] = (
        {
            "type": "imported",
            "drive_channels": [
                {"id": "left", "source_ids": ["source-left"]},
                {"id": "right", "source_ids": ["source-right"]},
            ],
        }
        if multi
        else {"type": "parametric"}
    )
    store.create_job(
        {
            "id": job_id,
            "status": status,
            "created_at": "2026-08-18T09:00:00",
            "updated_at": CREATED_AT,
            "queued_at": "2026-08-18T09:00:00",
            "started_at": "2026-08-18T09:00:01",
            "completed_at": CREATED_AT if status == "complete" else None,
            "progress": 1.0 if status == "complete" else 0.5,
            "stage": status,
            "stage_message": status,
            "label": "Test run",
            "config_json": {"geometry": geometry, "options": {"engine": "metal"}},
            "config_summary_json": {},
            "has_results": status == "complete",
            "task_metadata": {
                "solve_path": "full-3d",
                "field_plane_available": traces,
                "unavailable_reason": (
                    None if traces else (unavailable_reason or "solve_predates_traces")
                ),
            },
        }
    )
    if status == "complete":
        results: dict[str, Any] = {
            "frequencies": result_frequencies or FREQUENCIES,
            "metadata": {"engine": "hornlab-metal-bem", "solve_path": "full-3d"},
        }
        store.store_results(job_id, results)
    if traces:
        store.store_field_traces(
            job_id,
            _artifact(multi=multi, backend=backend, frequencies=trace_frequencies),
        )


def _members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def test_happy_path_package_carries_manifest_mesh_and_raw_traces(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_job(store, multi=True)
    destination = tmp_path / "package.zip"

    result = build_radiation_package(store, "job-1", destination)

    assert result.ok
    assert result.path == destination
    assert destination.is_file()

    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == list(PACKAGE_MEMBER_ORDER)
    members = _members(destination)

    manifest = json.loads(members[MANIFEST_MEMBER].decode("utf-8"))
    assert manifest["schema"] == RADIATION_PACKAGE_SCHEMA
    assert manifest["version"] == RADIATION_PACKAGE_VERSION
    assert manifest["job_id"] == "job-1"
    assert manifest["run_number"] == 1
    assert manifest["engine"] == "hornlab-metal-bem"
    assert manifest["backend"] == METAL_FIELD_TRACE_BACKEND
    assert manifest["geometry_sha256"] == mesh_text_sha256(MESH_TEXT)
    assert manifest["conventions"] == artifact_conventions()
    assert manifest["provenance"]["created_at"] == CREATED_AT
    assert manifest["dof_counts"] == {"p1": 4, "dp0": 2}

    # The package is an equivalent source on the REDUCED mesh, and says so.
    assert manifest["symmetry"]["plane"] == "yz"
    assert manifest["symmetry"]["traces_domain"] == "reduced-mesh"
    assert "image-expand" in manifest["symmetry"]["consumer_rule"]

    # No combine state, ever.
    assert manifest["channels"] == {
        "ids": ["left", "right"],
        "kind": "raw",
        "combine_included": False,
        "note": manifest["channels"]["note"],
    }
    assert "crossover" in manifest["channels"]["note"]

    assert manifest["frequencies"]["hz"] == FREQUENCIES
    assert manifest["frequencies"]["job_frequencies_hz"] == FREQUENCIES
    assert manifest["frequencies"]["available"] == [True, True]
    assert manifest["frequencies"]["k_real"] == [1.5, 3.75]
    assert manifest["frequencies"]["k_imag"] == [0.0075, 0.01875]

    layout = manifest["arrays"][TRACES_MEMBER]["members"]
    assert layout["pressure_p1"] == {
        "dtype": "complex64",
        "dimensions": ["frequency", "channel", "p1_node"],
        "shape": [2, 2, 4],
    }
    assert layout["neumann_dp0"]["dimensions"] == [
        "frequency",
        "channel",
        "dp0_element",
    ]

    assert members[MESH_MEMBER].decode("utf-8") == MESH_TEXT
    with np.load(io.BytesIO(members[TRACES_MEMBER])) as data:
        assert list(data["channel_ids"]) == ["left", "right"]
        assert data["frequencies_hz"].tolist() == FREQUENCIES
        assert data["pressure_p1"].dtype == np.complex64
        assert data["neumann_dp0"].dtype == np.complex64
        np.testing.assert_allclose(
            data["pressure_p1"][:, 0, :], LEFT_P.astype(np.complex64)
        )
        np.testing.assert_allclose(
            data["pressure_p1"][:, 1, :], (LEFT_P * (2 - 1j)).astype(np.complex64)
        )
        np.testing.assert_allclose(
            data["neumann_dp0"][:, 1, :], (LEFT_Q * (2 - 1j)).astype(np.complex64)
        )
    store.close()


def test_manifest_digests_cover_every_non_manifest_member(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store)
    destination = tmp_path / "package.zip"

    result = build_radiation_package(store, "job-1", destination)

    members = _members(destination)
    files = (result.manifest or {})["files"]
    assert set(files) == set(PACKAGE_MEMBER_ORDER) - {MANIFEST_MEMBER}
    for name, entry in files.items():
        assert entry["bytes"] == len(members[name])
        assert entry["sha256"].startswith("sha256:")
    assert validate_radiation_package(destination).ok
    store.close()


def test_bempp_backend_is_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store, backend=BEMPP_FIELD_TRACE_BACKEND)

    result = build_radiation_package(store, "job-1", tmp_path / "package.zip")

    assert (result.manifest or {})["backend"] == BEMPP_FIELD_TRACE_BACKEND
    store.close()


def test_two_builds_of_one_job_are_byte_identical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store, multi=True)

    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    assert build_radiation_package(store, "job-1", first).ok
    assert build_radiation_package(store, "job-1", second).ok

    assert first.read_bytes() == second.read_bytes()
    store.close()


def test_missing_job_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    destination = tmp_path / "package.zip"

    result = build_radiation_package(store, "absent", destination)

    assert _codes(result) == ["job_not_found"]
    assert not destination.exists()
    store.close()


@pytest.mark.parametrize("status", ["queued", "running", "error", "cancelled"])
def test_incomplete_job_is_refused(tmp_path: Path, status: str) -> None:
    store = _store(tmp_path)
    _create_job(store, status=status, traces=False)
    destination = tmp_path / "package.zip"

    result = build_radiation_package(store, "job-1", destination)

    assert _codes(result) == ["job_not_complete"]
    assert status in result.issues[0].message
    assert not destination.exists()
    store.close()


def test_unretained_traces_carry_the_stored_reason(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store, traces=False, unavailable_reason="size_cap_exceeded")
    destination = tmp_path / "package.zip"

    result = build_radiation_package(store, "job-1", destination)

    assert _codes(result) == ["traces_not_retained"]
    assert "size_cap_exceeded" in result.issues[0].message
    assert not destination.exists()
    store.close()


def test_traces_incomplete_against_the_solved_frequency_list(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(
        store,
        result_frequencies=[100.0, 250.0, 400.0],
        trace_frequencies=[100.0, 250.0],
    )
    destination = tmp_path / "package.zip"

    result = build_radiation_package(store, "job-1", destination)

    assert _codes(result) == ["traces_incomplete"]
    assert "400" in result.issues[0].message
    assert not destination.exists()
    store.close()


def test_unreadable_sidecar_is_refused_before_writing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store)
    record = store.get_field_trace_record("job-1")
    assert record is not None
    artifact_dir = store.field_traces_dir / "job-1"
    (artifact_dir / "meta.json").write_text("{ not json", encoding="utf-8")
    destination = tmp_path / "package.zip"

    result = build_radiation_package(store, "job-1", destination)

    assert _codes(result) == ["traces_unreadable"]
    assert not destination.exists()
    store.close()


def test_existing_destination_is_refused_and_left_untouched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store)
    destination = tmp_path / "package.zip"
    destination.write_bytes(b"existing")

    result = build_radiation_package(store, "job-1", destination)

    assert _codes(result) == ["destination_exists"]
    assert destination.read_bytes() == b"existing"
    store.close()


def test_missing_destination_directory_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store)

    result = build_radiation_package(
        store, "job-1", tmp_path / "absent" / "package.zip"
    )

    assert _codes(result) == ["destination_directory_missing"]
    store.close()


def test_no_partial_archive_survives_a_refusal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store, traces=False)
    destination = tmp_path / "package.zip"

    assert not build_radiation_package(store, "job-1", destination).ok

    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["data"]
    store.close()


def test_validation_catches_a_tampered_member(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store)
    destination = tmp_path / "package.zip"
    assert build_radiation_package(store, "job-1", destination).ok
    assert validate_radiation_package(destination).ok

    members = _members(destination)
    tampered = tmp_path / "tampered.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(
                name,
                content + b"\n$Trailing\n" if name == MESH_MEMBER else content,
            )
    tampered.write_bytes(buffer.getvalue())

    result = validate_radiation_package(tampered)

    assert _codes(result) == ["checksum_mismatch"]
    assert MESH_MEMBER in result.issues[0].message
    store.close()


def test_validation_reports_missing_and_unexpected_members(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_job(store)
    destination = tmp_path / "package.zip"
    assert build_radiation_package(store, "job-1", destination).ok

    members = _members(destination)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(MANIFEST_MEMBER, members[MANIFEST_MEMBER])
        archive.writestr(TRACES_MEMBER, members[TRACES_MEMBER])
        archive.writestr("data/stowaway.bin", b"unlisted")
    broken = tmp_path / "broken.zip"
    broken.write_bytes(buffer.getvalue())

    result = validate_radiation_package(broken)

    assert _codes(result) == ["member_missing", "member_unexpected"]
    store.close()


def test_validation_rejects_a_foreign_or_unreadable_archive(tmp_path: Path) -> None:
    not_a_zip = tmp_path / "not-a-zip.zip"
    not_a_zip.write_bytes(b"nope")
    assert _codes(validate_radiation_package(not_a_zip)) == ["package_unreadable"]

    assert _codes(validate_radiation_package(tmp_path / "absent.zip")) == [
        "package_unreadable"
    ]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", b"hello")
    stray = tmp_path / "stray.zip"
    stray.write_bytes(buffer.getvalue())
    assert _codes(validate_radiation_package(stray)) == ["manifest_missing"]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(MANIFEST_MEMBER, json.dumps({"schema": "other", "version": 9}))
    foreign = tmp_path / "foreign.zip"
    foreign.write_bytes(buffer.getvalue())
    assert _codes(validate_radiation_package(foreign)) == ["schema_unsupported"]


def test_cli_export_and_verify_round_trip(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    store = JobStore.for_data_dir(data_dir)
    store.initialize()
    _create_job(store, multi=True)
    store.close()
    destination = tmp_path / "package.zip"

    exit_code = main(
        [
            "export-package",
            "job-1",
            "--data-dir",
            str(data_dir),
            "--output",
            str(destination),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "2 frequencies" in captured.out
    assert "2 raw channels" in captured.out
    assert destination.is_file()

    assert main(["export-package", "--verify", str(destination)]) == 0
    assert "valid" in capsys.readouterr().out


def test_cli_refuses_and_lists_issue_codes(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    store = JobStore.for_data_dir(data_dir)
    store.initialize()
    _create_job(store, traces=False, unavailable_reason="unsupported_solve_mode")
    store.close()

    exit_code = main(
        [
            "export-package",
            "job-1",
            "--data-dir",
            str(data_dir),
            "--output",
            str(tmp_path / "package.zip"),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[traces_not_retained]" in captured.err
    assert "unsupported_solve_mode" in captured.err


def test_cli_argument_refusals(tmp_path: Path, capsys) -> None:
    assert main(["export-package", "job-1"]) == 1
    assert "--output PATH are both required" in capsys.readouterr().err

    assert (
        main(["export-package", "job-1", "--output", str(tmp_path / "package.tar")])
        == 1
    )
    assert "must name a .zip path" in capsys.readouterr().err

    assert (
        main(
            [
                "export-package",
                "job-1",
                "--verify",
                str(tmp_path / "package.zip"),
            ]
        )
        == 1
    )
    assert "--verify takes a package path on its own" in capsys.readouterr().err

    assert (
        main(
            [
                "export-package",
                "job-1",
                "--data-dir",
                str(tmp_path / "empty"),
                "--output",
                str(tmp_path / "package.zip"),
            ]
        )
        == 1
    )
    assert "[job_not_found]" in capsys.readouterr().err
