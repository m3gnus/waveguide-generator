"""Frozen production corpus construction for official-engine conformance."""

import numpy as np
import pytest

from scripts.beat_conformance import corpus
from server.jobs.models import MAX_EXPLICIT_FREQUENCIES

MESH = (corpus.ROOT / "server/solver/warmup_mesh.msh").read_bytes()

@pytest.mark.parametrize("case", corpus.CASES.values(), ids=lambda c: c.name)
def test_corpus_catalogue_uses_valid_wg_requests(case):
    request = case.request()
    assert request.options.frequencies_hz == list(case.coarse_hz)
    assert request.options.polar_config.spherical_sampling
    assert 3 <= len(case.coarse_hz) <= MAX_EXPLICIT_FREQUENCIES
    assert case.dense_step_hz <= 25 and not case.expect_no_features
    assert case.coarse_minutes[1] < 10 and case.refine_minutes[1] < 12
    assert case.fixture and case.covers


@pytest.mark.parametrize("case", corpus.CASES.values(), ids=lambda c: c.name)
def test_corpus_freezes_deterministic_tiny_fixture_once_per_case(case, tmp_path):
    calls = []
    def mesher(design):
        calls.append(design)
        return {"msh_text": MESH.decode(), "stats": {"vertex_count": 20}}
    def cad_builder(case, directory):
        calls.append(case.name)
        return MESH, {"sources": [{"id": "a"}, {"id": "b"}]}, {"vertex_count": 20}
    kwargs = {"mesher": mesher, "cad_builder": cad_builder}
    first = corpus.freeze_case(case, tmp_path, **kwargs)
    second = corpus.freeze_case(case, tmp_path, **kwargs)
    assert first.mesh_bytes == second.mesh_bytes and first.sha256 == second.sha256
    assert len(calls) == 2


def test_corpus_mesh_budget_refuses_before_solves(tmp_path):
    with pytest.raises(ValueError, match="vertex CPU budget"):
        corpus.freeze_case(corpus.CASES["osse-full"], tmp_path,
                          mesher=lambda _: {"msh_text": MESH.decode(), "stats": {"vertex_count": 10000}})


@pytest.mark.parametrize("case", corpus.CASES.values(), ids=lambda c: c.name)
def test_corpus_fast_production_meshing_is_deterministic(case, tmp_path):
    # All nine builders were measured < 2 s here. The separate tiny-fixture tests
    # retain catalogue/runner coverage on machines without the optional mesher.
    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    import time
    from pathlib import Path
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix="beat-corpus-mesh-") as mesh_directory:
        first_dir, second_dir = Path(mesh_directory) / "first", Path(mesh_directory) / "second"
        first_dir.mkdir()
        second_dir.mkdir()
        start = time.monotonic()
        first = corpus.freeze_case(case, first_dir)
        first_elapsed = time.monotonic() - start
        start = time.monotonic()
        second = corpus.freeze_case(case, second_dir)
        # A loose bound: fast meshing takes seconds, the accurate path minutes.
        # Hosted Windows runners have measured 8.8 s for a fast case.
        assert first_elapsed < 30 and time.monotonic() - start < 30
        assert first.mesh_bytes == second.mesh_bytes
        assert first.sha256 == second.sha256
        if first.record:
            from server.solver.beat_imported import imported_beat_preflight
            from server.jobs.models import SolveRequest
            request = SolveRequest.model_validate(first.request)
            assert imported_beat_preflight(first.record, first.mesh_bytes.decode(), request.geometry.drive_channels) is None
        if case.name == "imported-tilted-rear":
            from server.solver.beat_adapter.mesh import read_surface
            mesh = read_surface(first.mesh_bytes.decode())
            points = mesh.points_m[mesh.faces[mesh.tags == first.record["source_tags"]["curved-rear"]]]
            vector = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]).sum(axis=0)
            assert vector[2] < 0 and vector[0] > 0


@pytest.mark.parametrize("case", [c for c in corpus.CASES.values() if c.geometry.startswith("imported")],
                         ids=lambda c: c.name)
def test_corpus_freeze_closes_ingestion_connections_before_directory_removal(case, tmp_path, monkeypatch):
    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    import shutil
    import sqlite3
    from pathlib import Path

    ingestion = tmp_path / "ingestion"
    connections = []
    connect = sqlite3.connect

    def tracked_connect(database, *args, **kwargs):
        connection = connect(database, *args, **kwargs)
        if Path(database) == ingestion / "cadlink.db":
            # Keep strong references: GC cannot hide a missing explicit close
            # on POSIX, where unlinking an open database would otherwise pass.
            connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    frozen = corpus.freeze_case(case, tmp_path)
    shutil.rmtree(ingestion)
    assert not ingestion.exists()
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")
    assert frozen.mesh_bytes and frozen.record
