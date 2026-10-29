"""A compiled design assembly solved through the one model Solve: admission to job creation.

The mesh is a real design-mesher build; engines are declared, never run.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid

import pytest

from server.cadlink.store import CadLinkStore, design_model_id
from server.engines.registry import EngineInfo
from server.jobs.models_api import mount_models_api
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from test_design_assembly_record import HORN, UID, W1, assembly, woofer
from test_models_api import call

from fastapi import FastAPI

DRIVER = dict(version=1, re_ohm=6.0, le_h=0.0005, bl_n_per_a=7.0, mmd_kg=0.02,
              cms_m_per_n=0.001, rms_n_s_per_m=1.5, motion_axis=[0.0, 0.0, 1.0])
ENGINES = (
    EngineInfo("metal", True, "ready", "1", geometry_sources=("parametric", "imported")),
    EngineInfo("beat-cpu", True, "ready", "1", geometry_sources=("parametric", "imported"),
               imported_features=("exterior-transducers",)),
)


class _Declared:
    """Declares the engines; hands back an inert stand-in, never running a solve."""

    async def capabilities(self):
        return ENGINES

    async def get_engine(self, name):
        from types import SimpleNamespace

        return SimpleNamespace(name=name)

    async def unavailable_reason(self, _name):
        return None


def setup_for(record, *, engine, drivers=False, combine=True, processing=None):
    channels = []
    for excitation in record["excitations"]:
        channel = {"id": excitation["id"], "source_ids": excitation["source_ids"], "motion": excitation["motion"]}
        if drivers and excitation["id"] != "horn-throat":
            channel["exterior_transducer"] = DRIVER
        channels.append(channel)
    groups = [
        {"id": group["id"], "label": group["label"], "channel_ids": group["members"],
         "member_processing": {member: (processing or {}).get(member, {}) for member in group["members"]}}
        for group in record["signal_groups"] if group["members"]
    ]
    geometry = {"required_features": ["wg-design-assembly-v1"], "drive_channels": channels, "driver_groups": groups}
    if combine:
        lf, hf = groups[1]["id"], groups[0]["id"]
        geometry["combine"] = {"members": [lf, hf], "reference": hf, "channels": {
            lf: {"lp": {"family": "lr", "order": 4, "fc_hz": 800}},
            hf: {"hp": {"family": "lr", "order": 4, "fc_hz": 800}},
        }}
    return {"schema_version": 1, "geometry": geometry, "options": {
        "engine": engine, "frequencies_hz": [200.0, 800.0, 2000.0],
        "polar_config": {"enabled_axes": ["horizontal"], "spherical_theta_count": 5, "spherical_phi_count": 8}}}


@pytest.fixture(autouse=True)
def own_build_session():
    """Each test is its own client: revisions are ordered within a build session only."""
    global SESSION
    SESSION = str(uuid.uuid4())


@pytest.fixture
def compiled_app(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    store.initialize()
    runtime = JobRuntime(JobStore.for_data_dir(tmp_path), cadlink_store=store,
                         engine_registry=_Declared())
    runtime.store.initialize()
    app = FastAPI()
    app.state.cadlink_store, app.state.jobs_runtime, app.state.data_dir = store, runtime, tmp_path
    mount_models_api(app)
    yield app, runtime, store
    store.close()


SESSION = str(uuid.uuid4())


def compile_body(revision=1, **changes):
    dump = {**copy.deepcopy(HORN), "assembly": assembly(woofer()), **changes}
    return {"design": dump, "design_uid": UID, "design_name": "Front horn", "design_revision": revision,
            "build_session": SESSION}


def test_assembly_jobs_are_created_through_the_model_solve_for_each_engine(compiled_app):
    app, runtime, store = compiled_app

    async def scenario():
        try:
            compiled = await call(app, "POST", "/api/models/assembly/compile", compile_body())
            record = compiled["prepared"]
            model_id = compiled["model"]["model_id"]
            assert model_id == design_model_id(UID)
            jobs = []
            for press, (engine, drivers) in enumerate((("metal", False), ("beat-cpu", True), ("beat-cpu", False))):
                accepted = await call(app, "POST", f"/api/models/{model_id}/solve", {
                    "prepared_id": record["ingest_id"], "client_request_id": f"press-{press}",
                    "setup": setup_for(record, engine=engine, drivers=drivers)})
                jobs.append(accepted["job_id"])
            assert len(set(jobs)) == 3
            for job_id, drivers in zip(jobs, (False, True, False)):
                row = await asyncio.to_thread(runtime.store.get_job_row, job_id)
                geometry = row["config_json"]["geometry"]
                assert geometry["required_features"] == ["wg-design-assembly-v1"]
                assert row["task_metadata"]["imported_geometry"]["model_id"] == model_id
                assert row["config_summary_json"]["import_origin"] == "design-assembly"
                channels = {c["id"]: c for c in geometry["drive_channels"]}
                assert ("exterior_transducer" in channels[W1]) is drivers
                assert channels["horn-throat"]["motion"] == "normal" and channels[W1]["motion"] == "axial"
                assert [g["member_processing"] for g in geometry["driver_groups"]]
                assert geometry["combine"]["members"][1] == record["signal_groups"][0]["id"]
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_drivers_on_an_engine_without_coupling_are_refused_with_the_reason(compiled_app):
    app, runtime, _store = compiled_app

    async def scenario():
        try:
            compiled = await call(app, "POST", "/api/models/assembly/compile", compile_body())
            record = compiled["prepared"]
            refused = await call(app, "POST", f"/api/models/{compiled['model']['model_id']}/solve", {
                "prepared_id": record["ingest_id"], "client_request_id": "press-metal",
                "setup": setup_for(record, engine="metal", drivers=True)}, expected=422)
            assert "coupled exterior transducers" in refused["error"]["message"]
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize(("change", "status", "code"), [
    (lambda setup: setup["geometry"].update(required_features=[]), 422, None),
    (lambda setup: setup["geometry"]["combine"]["channels"].update(
        {next(iter(setup["geometry"]["combine"]["channels"])): {"gain": {"mode": "max"}}}), 422, None),
    (lambda setup: setup["geometry"]["drive_channels"].pop(), 422, "drive_source_coverage"),
])
def test_an_assembly_setup_is_held_to_its_record(compiled_app, change, status, code):
    app, runtime, _store = compiled_app

    async def scenario():
        try:
            compiled = await call(app, "POST", "/api/models/assembly/compile", compile_body())
            record = compiled["prepared"]
            setup = setup_for(record, engine="beat-cpu", drivers=True)
            if code == "drive_source_coverage":
                setup["geometry"].pop("combine")
                setup["geometry"]["driver_groups"] = None
            change(setup)
            refused = await call(app, "POST", f"/api/models/{compiled['model']['model_id']}/solve", {
                "prepared_id": record["ingest_id"], "client_request_id": "press-x", "setup": setup}, expected=status)
            if code is not None:
                assert refused["error"]["code"] == code
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())


def test_a_stale_record_is_never_reused_after_the_geometry_changes(compiled_app):
    app, runtime, _store = compiled_app

    async def scenario():
        try:
            first = await call(app, "POST", "/api/models/assembly/compile", compile_body())
            moved = compile_body(2)
            moved["design"]["assembly"]["woofers"][0]["placement"]["gap_mm"] = 20
            second = await call(app, "POST", "/api/models/assembly/compile", moved)
            assert second["prepared"]["ingest_id"] != first["prepared"]["ingest_id"]
            model = await call(app, "GET", f"/api/models/{first['model']['model_id']}")
            assert model["latest_prepared"]["ingest_id"] == second["prepared"]["ingest_id"]
            assert json.dumps(model["latest_prepared"]["design"]["revision"]) == "2"
        finally:
            await runtime.shutdown()
    asyncio.run(scenario())
