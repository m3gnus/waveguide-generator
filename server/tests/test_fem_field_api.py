"""The F9 route consumes the F8 storage contract without a running solver."""
from __future__ import annotations

import asyncio
import base64
import json
import zipfile

from fastapi import FastAPI
import numpy as np
import pytest

from server.jobs.api import FemFieldPresentation, create_jobs_router
from server.jobs.fem_field import _pressure_slice
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.tests.test_jobs_api import _request_with_headers
from server.tests.test_jobs_store import _job


@pytest.fixture
def field_arrays():
    points = np.array([[0., 0., 0.], [10., 0., 0.], [0., 10., 0.], [0., 0., 10.]])
    arrays = {
        "schema": np.array("wg-fem-field-store-v1"),
        "phasor_convention": np.array("exp(-i omega t)"),
        "drive_normalization": np.array("unit_acceleration_m_per_s2"),
        "experimental": np.array(True),
        "frequencies_hz": np.array([100., 1000.]),
        "channel_ids": np.array(["front", "rear"]),
        "region_ids": np.array(["chamber", "port"]),
        "pressure": np.arange(32, dtype=np.float64).reshape(2, 2, 8).astype(np.complex128) * (1 - 2j),
    }
    for index in range(2):
        arrays[f"points_mm_{index}"] = points + index * 20
        arrays[f"tetrahedra_{index}"] = np.array([[0, 1, 2, 3]], dtype=np.int64)
        arrays[f"surface_names_{index}"] = np.array([f"wg-fem-v1|region={index}|group=walls|kind=rigid"])
        arrays[f"surface_triangles_{index}_0"] = np.array([[0, 1, 2]], dtype=np.int64)
    return arrays


@pytest.fixture
def field_app(tmp_path, monkeypatch, field_arrays):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("field", "complete"))
    store.create_job(_job("ordinary", "complete"))
    store.create_job(_job("running", "running"))
    store.store_fem_field_arrays("field", field_arrays)
    app = FastAPI()
    app.include_router(create_jobs_router(JobRuntime(store)))
    return app, store


def _get(app, job="field", query=""):
    status, raw, headers = asyncio.run(_request_with_headers(
        app, "GET", f"/api/results/{job}/fem-field", query=query))
    return status, json.loads(raw), headers


def test_route_decodes_selected_frequency_channel_and_rebases_regions(field_app, field_arrays, monkeypatch):
    app, store = field_app
    # The route must never use the packed-byte download or load pressure.npy
    # through NumPy, either of which would allocate the entire retained field.
    monkeypatch.setattr(store, "get_fem_field", lambda *_: pytest.fail("whole sidecar read"))
    original = np.lib.npyio.NpzFile.__getitem__
    def selected_only(self, key):
        assert key != "pressure"
        return original(self, key)
    monkeypatch.setattr(np.lib.npyio.NpzFile, "__getitem__", selected_only)
    status, payload, headers = _get(app, query="frequency=1000&channel=rear")
    assert status == 200
    assert headers["cache-control"] == "no-store"
    assert payload["schema"] == "wg-fem-field-v1"
    assert payload["frequency_hz"] == 1000
    assert payload["channel_id"] == "rear"
    assert payload["frequencies_hz"] == [100, 1000]
    assert payload["channel_ids"] == ["front", "rear"]
    real = np.frombuffer(base64.b64decode(payload["pressure_real"]), dtype="<f4")
    imag = np.frombuffer(base64.b64decode(payload["pressure_imag"]), dtype="<f4")
    np.testing.assert_array_equal(real + 1j * imag, field_arrays["pressure"][1, 1])
    np.testing.assert_array_equal(np.frombuffer(base64.b64decode(payload["tetrahedra"]), dtype="<u4"),
                                  np.array([0, 1, 2, 3, 4, 5, 6, 7]))
    np.testing.assert_array_equal(np.frombuffer(base64.b64decode(payload["surfaces"][1]["triangles"]), dtype="<u4"),
                                  np.array([4, 5, 6]))
    assert payload["experimental"] is True
    assert payload["phasor_convention"] == "exp(-i omega t)"


def test_default_selection_and_missing_field_refusals(field_app):
    app, store = field_app
    status, payload, _ = _get(app)
    assert status == 200
    assert (payload["frequency_hz"], payload["channel_id"]) == (100, "front")
    for job in ("ordinary", "absent", "running"):
        assert _get(app, job)[0] == 404
    store.delete_fem_field("field")
    assert _get(app)[0] == 404


def test_presentation_alias_preserves_wire_and_openapi_schema(field_app):
    app, _ = field_app
    status, payload, _ = _get(app)
    assert status == 200
    presentation = FemFieldPresentation.model_validate(payload)
    assert presentation.schema_ == "wg-fem-field-v1"
    assert presentation.model_dump(by_alias=True) == payload
    schema = app.openapi()["components"]["schemas"]["FemFieldPresentation"]
    assert "schema" in schema["properties"]
    assert "schema" in schema["required"]
    assert "schema_" not in schema["properties"]


@pytest.mark.parametrize("query", ["frequency=200", "frequency=0", "frequency=nan", "frequency=inf",
                                   "channel=absent", "channel="])
def test_invalid_selection_is_422(field_app, query):
    assert _get(field_app[0], query=query)[0] == 422


def test_flag_off_hides_route_and_job_marker(field_app, monkeypatch):
    app, store = field_app
    assert JobRuntime._serialize_job(store.get_job_row("field"))["has_fem_field_artifact"] is True
    monkeypatch.setenv("WG2_FEM_REGIONS", "0")
    assert _get(app)[0] == 404
    assert JobRuntime._serialize_job(store.get_job_row("field"))["has_fem_field_artifact"] is False
    assert store.get_fem_field("field") is not None


@pytest.mark.parametrize("key,value", [("schema", np.array("other")),
                                     ("pressure", np.zeros((1, 2, 8), dtype=np.complex128)),
                                     ("tetrahedra_0", np.array([[0, 1, 2, 99]])),
                                     ("pressure", np.full((2, 2, 8), np.nan, dtype=np.complex128))])
def test_corrupt_field_is_refused(field_app, field_arrays, key, value):
    app, store = field_app
    store.store_fem_field_arrays("field", {**field_arrays, key: value})
    assert _get(app)[0] == 500


def test_pressure_skip_reads_are_bounded(tmp_path, monkeypatch):
    path = tmp_path / "large.npz"
    # Select the last of many entries; skip memory stays bounded even though
    # decompression of earlier entries is required by the compressed F8 NPZ.
    pressure = np.zeros((4, 2, 100_000), dtype=np.complex128)
    pressure[3, 1] = 3 - 4j
    np.savez_compressed(path, pressure=pressure)
    original = zipfile.ZipExtFile.read
    reads = []
    def read(self, size=-1):
        reads.append(size)
        return original(self, size)
    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    with zipfile.ZipFile(path) as archive:
        result = _pressure_slice(archive, 3, 1, pressure.shape)
    np.testing.assert_array_equal(result, pressure[3, 1])
    assert all(0 <= size <= max(1024 * 1024, pressure[3, 1].nbytes) for size in reads)


def test_fortran_pressure_is_subset_without_changing_the_wire_shape(field_app, field_arrays):
    app, store = field_app
    store.store_fem_field_arrays("field", {**field_arrays, "pressure": np.asfortranarray(field_arrays["pressure"])})
    status, payload, _ = _get(app, query="frequency=1000&channel=rear")
    assert status == 200
    np.testing.assert_array_equal(
        np.frombuffer(base64.b64decode(payload["pressure_real"]), dtype="<f4"),
        field_arrays["pressure"][1, 1].real)
