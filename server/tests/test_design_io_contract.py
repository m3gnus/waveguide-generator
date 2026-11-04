"""Design file endpoint and real v1 corpus round-trip contracts."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import pytest

from server.cadlink.identity import CadLink, design_hash
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.design.textcfg import parse, serialize
from server.design_io import api
from server.design_io.api import import_report, open_design, save_design, serialize_design
from server.tests._asgi import _post


ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT.parent / "Waveguide Generator"
CORPUS = sorted((V1 / "output").glob("*/script.snapshot.mwg")) + sorted(
    (V1 / "tests/fixtures/ath").glob("*")
)


@pytest.mark.parametrize("route", ["choose", "save", "serialize", "open", "import-report"])
def test_worker_responses_keep_the_committed_object_schema(route):
    store = CadLinkStore(":memory:")
    try:
        app = FastAPI()
        app.include_router(api.create_design_io_router(store))
        committed = json.loads((ROOT / "docs/reference/openapi.v1.json").read_text())
        path = f"/api/design/{route}"
        live = app.openapi()["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        expected = committed["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert live == expected
        assert live["type"] == "object"
    finally:
        store.close()


@pytest.mark.skipif(
    not any(path.suffix == ".mwg" for path in CORPUS),
    reason="the v1 checkout is not beside this one",
)
def test_open_real_legacy_mwg_reports_dialect_and_passthrough() -> None:
    path = next(path for path in CORPUS if path.suffix == ".mwg")
    result = asyncio.run(open_design(path.read_text()))
    assert result["dialect"] == "mwg"
    assert result["design"]["formula"] in {"OSSE", "R-OSSE", "ICW", "FREEFORM"}
    assert set(result["passthrough"]) == {
        "keysPreserved", "blocksPreserved", "keyCount", "blockCount"
    }


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: str(path.relative_to(V1)))
def test_open_reuses_batch_a_round_trip_law_on_the_real_corpus(path: Path) -> None:
    source = path.read_text()
    parsed = parse(source)
    opened = asyncio.run(open_design(source))
    assert opened["design"] == parsed.semantic_data()
    emitted = serialize(parsed)
    assert emitted == source
    assert parse(emitted).semantic_data() == parsed.semantic_data()


def test_save_bare_design_json_uses_cfg_and_v1_header() -> None:
    design = parse("OSSE = {\nL = 120\na = 45\n}\n").semantic_data()
    result = asyncio.run(save_design(design))
    assert result["suggestedFilename"] == "untitled.cfg"
    assert result["text"].startswith("; Parameter config\n")
    assert "Waveguide Generator design-format: 2" in result["text"]


@pytest.mark.parametrize(
    "design",
    [
        {"formula": "OSSE", "throat_profile": 1},
        {"formula": "R-OSSE"},
        {"formula": "ICW"},
    ],
    ids=("OSSE", "R-OSSE", "ICW"),
)
def test_save_sparse_design_can_be_opened_again(design: dict[str, object]) -> None:
    saved = asyncio.run(save_design({"design": design, "filename": "sparse.cfg"}))
    reopened = asyncio.run(open_design(saved["text"]))

    assert reopened["design"]["formula"] == design["formula"]


def test_import_report_is_dry_run_and_invalid_text_has_parse_detail() -> None:
    source = "OSSE = {\nL = 120\na = 45\n}\n"
    first = asyncio.run(import_report(source))
    second = asyncio.run(import_report(source))
    assert first == second
    assert first["dialect"] == "ath"
    with pytest.raises(HTTPException) as caught:
        asyncio.run(open_design("this is not a design"))
    assert caught.value.status_code == 422
    assert caught.value.detail["type"] == "parse_error"
    assert "could not find" in caught.value.detail["message"]


def test_both_endpoints_report_the_wg_solve_keys_the_solve_ignores() -> None:
    """`WG.Solve.Engine` and `SolverMode` are stated, kept, and never honoured.

    They are not migrated away, so they carry no migration note; the report
    names them separately instead, and both endpoints that classify a document
    have to say the same thing.
    """

    source = (
        "; Parameter config\nOSSE = {\nL = 120\na = 45\n}\n"
        "WG.Solve = {\nEngine = metal\nSolverMode = circsym\n}\n"
    )

    opened = asyncio.run(open_design(source))
    inspected = asyncio.run(import_report(source))

    assert opened["ignoredSettings"] == inspected["ignoredSettings"]
    assert [item["key"] for item in opened["ignoredSettings"]] == [
        "WG.Solve.Engine",
        "WG.Solve.SolverMode",
    ]
    assert [item["value"] for item in opened["ignoredSettings"]] == ["metal", "circsym"]
    for item in opened["ignoredSettings"]:
        assert "Solve options" in item["note"]
    # Reported, not migrated: the block is still passed through whole.
    assert opened["migrationsApplied"] == []
    assert "WG.Solve" in opened["passthrough"]["blocksPreserved"]


def test_a_design_without_machine_keys_reports_an_empty_list() -> None:
    """Present and empty, not absent: a client can read the key unconditionally."""

    opened = asyncio.run(open_design("OSSE = {\nL = 120\na = 45\n}\n"))
    assert opened["ignoredSettings"] == []


def test_open_report_names_the_machine_solver_mode_it_refused_to_honour() -> None:
    """The one thing a stated solver mode must not do is vanish without a word.

    It is not honoured -- which formulation this host can run is a machine
    fact, so the mode travels in solve options and export strips it -- so the
    open report has to carry the refusal.
    """

    source = "; Parameter config\nOSSE = {\nL = 120\na = 45\n}\nSimulation.SolverMode = circsym\n"

    result = asyncio.run(open_design(source))

    assert result["design"]["simulation"]["solver_mode"] is None
    applied = result["migrationsApplied"]
    assert [item["name"] for item in applied] == ["006_machine_solver_mode_not_portable"]
    assert "Simulation.SolverMode" in applied[0]["note"]
    assert "Solve options" in applied[0]["note"]


@pytest.mark.parametrize("stage,route", [
    ("parse", "open"), ("parse", "import-report"),
    ("serialize", "serialize"), ("serialize", "save"),
    ("registry", "save"), ("read", "choose"), ("encode", "open"),
])
def test_design_work_allows_a_concurrent_heartbeat(monkeypatch, tmp_path, stage, route):
    source = "OSSE = {\nL = 120\na = 45\n}\n"
    payload = {"design": parse(source).semantic_data()}
    selected = tmp_path / "design.cfg"
    selected.write_text(source)
    monkeypatch.setattr(api, "select_workspace_path", lambda *a, **kw: SimpleNamespace(outcome="chosen", path=selected))
    started, heartbeat = threading.Event(), threading.Event()
    observed = []

    def delayed(original):
        def work(*args, **kwargs):
            started.set()
            observed.append(heartbeat.wait(0.2))
            return original(*args, **kwargs)
        return work

    store = CadLinkStore(tmp_path / "registry.db")
    owner, name = {
        "parse": (api, "parse"), "serialize": (api, "serialize"),
        "registry": (store, "save"), "read": (Path, "open"),
        "encode": (JSONResponse, "render"),
    }[stage]
    monkeypatch.setattr(owner, name, delayed(getattr(owner, name)))
    app = FastAPI()
    app.include_router(api.create_design_io_router(store))

    async def scenario():
        async def beat():
            while not started.is_set():
                await asyncio.sleep(0.001)
            heartbeat.set()

        task = asyncio.create_task(beat())
        try:
            body = source if route in {"open", "import-report"} else payload
            status, raw = await _post(app, f"/api/design/{route}", body)
            assert status == 200, raw
            await asyncio.wait_for(task, timeout=0.5)
        finally:
            task.cancel()

    try:
        asyncio.run(scenario())
        assert observed and all(observed), f"{stage} blocked the event loop"
    finally:
        store.close()


def test_worker_saves_share_the_in_memory_registry():
    store = CadLinkStore(":memory:")

    async def scenario():
        first = await save_design({"design": {"formula": "OSSE"}}, store=store)
        # A second worker and the caller must see the first worker's commit.
        with ThreadPoolExecutor(max_workers=1) as executor:
            head = await asyncio.get_running_loop().run_in_executor(executor, store.get_design, first["identity"]["designId"])
        assert head is not None
        opened = await open_design(first["text"], store=store)
        assert opened["cadlink"]["classification"] == "current"
        return first

    try:
        saved = asyncio.run(scenario())
        assert store.get_design(saved["identity"]["designId"]) is not None
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["legacy", "lookup", "cadlink"])
def test_worker_round_trips_preserve_document_bytes_and_semantic_values(tmp_path, kind):
    source = "; MWG config\nLength = 120\nCoverage.Angle = 45\nTerm.n = 4\n"
    if kind == "lookup":
        source = serialize(DesignConfig.model_validate({
            "formula": "LOOKUP", "lookup_profile": [[0, 12.7], [20, 25], [80, 60]],
        }))
    elif kind == "cadlink":
        design = parse(source).design
        source = serialize(design, cadlink=CadLink(
            design_id="wgd_" + "0" * 26, lineage_id="wgl_" + "0" * 26, edit_version=1,
            saved_at="2026-11-04T00:00:00Z", saved_design_hash=design_hash(design)[:23],
        ))
    parsed = parse(source)
    store = CadLinkStore(tmp_path / "registry.db")

    async def scenario():
        opened = await open_design(source, store=store)
        assert opened["design"] == parsed.semantic_data()
        assert serialize(parsed).encode() == source.encode()
        payload = {"design": opened["design"]}
        portable = await serialize_design(payload)
        assert portable["text"].encode() == serialize(parsed.design).encode()
        saved = await save_design(payload, store=store)
        saved_parse = parse(saved["text"])
        assert saved["text"].encode() == serialize(parsed.design, cadlink=saved_parse.cadlink).encode()
        assert (await open_design(saved["text"], store=store))["design"] == parse(portable["text"]).semantic_data()
        app = FastAPI()
        app.include_router(api.create_design_io_router(store))
        status, raw = await _post(app, "/api/design/open", source)
        expected = await open_design(source, store=store)
        assert status == 200
        assert raw == JSONResponse(expected).body
        assert json.loads(raw)["design"] == parsed.semantic_data()

    try:
        asyncio.run(scenario())
    finally:
        store.close()


@pytest.mark.parametrize("failure,message", [
    ("extension", "Choose a .cfg, .mwg, .txt or .json design file"),
    ("size", "Design exceeds the 16 MiB open limit"),
    ("utf8", "utf-8"),
])
def test_native_reader_keeps_its_limits_and_error_envelope(tmp_path, monkeypatch, failure, message):
    selected = tmp_path / ("design.exe" if failure == "extension" else "design.cfg")
    selected.write_bytes(b" " * (16 * 1024 * 1024 + 1) if failure == "size" else b"\xff")
    monkeypatch.setattr(api, "select_workspace_path", lambda *a, **kw: SimpleNamespace(outcome="chosen", path=selected))
    app = FastAPI()
    app.include_router(api.create_design_io_router(CadLinkStore(":memory:")))
    status, raw = asyncio.run(_post(app, "/api/design/choose", {"path": "ignored.cfg"}))
    assert status == 422
    assert message in json.loads(raw)["detail"]
