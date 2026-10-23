"""A document id is a comment, never geometry or a CadLink edit identity."""

import asyncio

import pytest
from fastapi import HTTPException

from server.cadlink.identity import design_hash
from server.cli.request import build_request
from server.design.schema import DesignConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.design_io.api import open_design, serialize_design
from server.tests.test_textcfg import SOURCE
from server.tests.textcfg_035_lexer import _lex as lex_035
from server.tests.test_native_formulas import FAMILIES, design_payload

UID = "wgu_01K00000000000000000000000"
OTHER = "wgu_01K00000000000000000000001"
STAMP = f"; Waveguide Generator design-id: {UID}\n"


def test_stamp_is_outside_design_hash_and_semantic_fingerprint():
    plain = parse(SOURCE)
    stamped = parse(STAMP + SOURCE)
    assert stamped.design_uid == UID
    assert stamped.semantic_data() == plain.semantic_data()
    assert stamped._initial_fingerprint == plain._initial_fingerprint
    assert design_hash(stamped.design) == design_hash(plain.design)
    assert serialize(stamped) == STAMP + SOURCE
    emitted = serialize(stamped, design_uid=OTHER)
    assert emitted.count("Waveguide Generator design-id:") == 1
    assert parse(emitted).design_uid == OTHER
    assert parse(emitted).semantic_data() == plain.semantic_data()
    canonical = serialize(plain.design, design_uid=UID)
    assert "; Waveguide Generator design-format: 2\n" in canonical
    assert parse(canonical).design_uid == UID
    stamped.design.root.mesh.vertical_offset = 12
    rewritten = serialize(stamped)
    assert rewritten.count("Waveguide Generator design-id:") == 1
    assert parse(rewritten).design_uid == UID


def test_035_lexer_ignores_stamp_and_preserves_it_as_a_comment():
    before = lex_035(SOURCE)
    after = lex_035(STAMP + SOURCE)
    assert after[0] == before[0]  # assignments
    assert after[1] == before[1]  # blocks, including verbatim entries
    assert after[3] == before[3]  # raw values
    assert after[2] == [STAMP.strip(), *before[2]]
    # The old parser's semantic input is byte-for-byte the same.
    assert parse(STAMP + SOURCE).semantic_data() == parse(SOURCE).semantic_data()


@pytest.mark.parametrize("formula", FAMILIES)
def test_native_stamp_opens_without_new_design_format(formula):
    design = DesignConfig.model_validate(design_payload(formula))
    text = serialize(design, design_uid=UID)
    opened = asyncio.run(open_design(text))
    assert opened["designUid"] == UID
    assert DesignConfig.model_validate(opened["design"]) == design
    assert "design-format: 2" in text


def test_open_and_serialize_carry_uid_beside_design():
    plain = asyncio.run(open_design(SOURCE))
    assert plain["designUid"] is None
    saved = asyncio.run(serialize_design({"design": plain["design"], "designUid": UID}))
    opened = asyncio.run(open_design(saved["text"]))
    assert opened["designUid"] == UID
    assert opened["design"] == parse(saved["text"]).semantic_data()
    assert "designUid" not in opened["design"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(serialize_design({"design": plain["design"], "designUid": "wgd_invalid"}))
    assert exc.value.status_code == 422


def test_cli_carries_uid_and_keeps_unstamped_name_fallback():
    stamped = build_request(parse(STAMP + SOURCE)).request
    plain = build_request(parse(SOURCE)).request
    assert stamped.design_uid == UID
    assert plain.design_uid is None
    assert stamped.design == plain.design


@pytest.mark.parametrize("stamp", [
    "; Waveguide Generator design-id: wgd_01K00000000000000000000000\n",
    "; Waveguide Generator design-id: wgu_Z1K00000000000000000000000\n",
])
def test_invalid_uid_is_refused(stamp):
    with pytest.raises(TextConfigError, match="design-id"):
        parse(stamp + SOURCE)


@pytest.mark.parametrize("extra", ["", STAMP, f"; Waveguide Generator design-id: {OTHER}\n"])
def test_stamp_preserves_saved_design_hash_and_current_classification(tmp_path, extra):
    from server.cadlink.store import CadLinkStore
    from server.design_io.api import save_design

    store = CadLinkStore(tmp_path / 'cadlink.db')
    try:
        saved = asyncio.run(save_design({"design": parse(SOURCE).semantic_data(), "designUid": UID}, store=store))
        parsed = parse(extra + saved["text"])
        assert parsed.design_uid == (None if OTHER in extra else UID)
        assert parsed.cadlink is not None
        before_hash = parsed.cadlink.saved_design_hash
        restamped = serialize(parsed, design_uid=OTHER)
        reopened = asyncio.run(open_design(restamped, store=store))
        assert reopened["cadlink"]["classification"] == "current"
        assert reopened["cadlink"]["identity"]["savedDesignHash"] == before_hash
        assert parse(restamped).cadlink == parsed.cadlink
    finally:
        store.close()


def test_lookup_json_copy_omits_uid_but_mwg_retains_it():
    from server.tests.test_lookup import design

    payload = {"design": design().model_dump(mode="json"), "designUid": UID}
    json_copy = asyncio.run(serialize_design({**payload, "filename": "lookup.json"}))
    assert parse(json_copy["text"]).design_uid is None
    assert parse(serialize(parse(json_copy["text"]), design_uid=UID)).design_uid == UID
    mwg = asyncio.run(serialize_design({**payload, "filename": "lookup.mwg"}))
    assert parse(mwg["text"]).design_uid == UID


@pytest.fixture
def picker_client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.design_io import api
    from server.cadlink.store import CadLinkStore
    from server.workspace.api import PathPickerResult

    source = tmp_path / "same-name.cfg"
    source.write_text(STAMP + SOURCE)
    store = CadLinkStore(":memory:")
    application = FastAPI()
    application.include_router(api.create_design_io_router(store))
    monkeypatch.setattr(api, "select_workspace_path", lambda *args, **kwargs: PathPickerResult("chosen", str(source)))
    with TestClient(application) as client:
        yield client, source
    store.close()


def test_native_open_picker_returns_a_path_and_distinct_outcomes(picker_client, monkeypatch):
    from server.design_io import api
    from server.workspace.api import PathPickerResult

    client, source = picker_client
    chosen = client.post("/api/design/choose").json()
    assert chosen.items() >= {"selected": True, "outcome": "chosen", "source": str(source.resolve()),
                              "filename": source.name, "text": STAMP + SOURCE}.items()
    assert chosen["fileKey"]
    assert "selectedFile" not in chosen
    for outcome in ("cancelled", "unavailable"):
        monkeypatch.setattr(api, "select_workspace_path", lambda *args, **kwargs: PathPickerResult(outcome))
        assert client.post("/api/design/choose").json() == {"selected": False, "outcome": outcome}


def test_explicit_export_writes_the_new_uid_and_detached_cadlink(picker_client, tmp_path):
    from server.cadlink.store import CadLinkStore
    from server.design_io.api import save_design
    from server.tests.test_workspace_write_export import destination_router, selected_state

    client, copy = picker_client
    store = CadLinkStore(":memory:")
    try:
        linked = asyncio.run(save_design({"design": parse(SOURCE).semantic_data(), "designUid": UID}, store=store))
    finally:
        store.close()
    original = copy.with_name("original.cfg")
    original.write_text(linked["text"])
    copy.write_bytes(original.read_bytes())
    before = copy.read_bytes()
    chosen = client.post("/api/design/choose").json()
    opened = client.post("/api/design/open", content=chosen["text"]).json()
    assert opened["designUid"] == UID
    assert opened["cadlink"]["identity"] is not None

    # The UI's new uid and detached identity are sent through normal export.
    serialized = client.post("/api/design/serialize", json={
        "design": opened["design"], "designUid": OTHER, "filename": copy.name,
    })
    assert serialized.status_code == 200, serialized.text
    assert copy.read_bytes() == original.read_bytes() == before
    text = serialized.json()["text"]
    state, _ = selected_state(tmp_path)
    destinations, router = destination_router(state, tmp_path)
    client.app.include_router(router)
    written = client.post("/api/workspace/write-export", data={
        "destination": destinations.issue(copy.parent), "existing": "overwrite",
        "relative_path": copy.name,
    }, files={"file": (copy.name, text, "text/plain")})
    assert written.status_code == 200, written.text
    assert original.read_bytes() == before
    assert copy.read_text().count("Waveguide Generator design-id:") == 1
    assert parse(copy.read_text()).semantic_data() == parse(before.decode()).semantic_data()
    reopened = client.post("/api/design/open", content=copy.read_text()).json()
    assert reopened["designUid"] == OTHER
    assert reopened["cadlink"]["identity"] is None


@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_file_picker_explicit_cancel_does_not_open_twice(system, monkeypatch):
    import subprocess
    from server.workspace import api

    commands = []
    monkeypatch.setattr(api.platform, "system", lambda: system)
    monkeypatch.setenv("DISPLAY", ":test")

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0 if system == "Windows" else 1,
                                           "WG_PICKER_CANCELLED" if system == "Windows" else "",
                                           "User canceled. (-128)" if system == "Darwin" else "")

    monkeypatch.setattr(api.subprocess, "run", run)
    assert api.select_workspace_path("Open design", file=True) == api.PathPickerResult("cancelled")
    assert len(commands) == 1
    serialized = " ".join(commands[0])
    assert ("choose file" if system == "Darwin" else "OpenFileDialog" if system == "Windows" else "--file-selection") in serialized


@pytest.mark.parametrize("failure", ["error", "missing", "timeout"])
@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_headless_picker_failure_is_unavailable_not_cancel(failure, system, monkeypatch):
    import subprocess
    import sys
    from types import SimpleNamespace
    from server.workspace import api

    monkeypatch.setattr(api.platform, "system", lambda: system)
    monkeypatch.setenv("DISPLAY", ":test")

    def run(command, **kwargs):
        if failure == "missing":
            raise FileNotFoundError()
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        return subprocess.CompletedProcess(command, 1, "", "cannot open display")

    def no_display():
        raise RuntimeError("no display")

    monkeypatch.setattr(api.subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=no_display, filedialog=SimpleNamespace()))
    assert api.select_workspace_path("Open design", file=True) == api.PathPickerResult("unavailable")


@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_working_native_picker_chooses_file(system, monkeypatch):
    import subprocess
    from server.workspace import api

    monkeypatch.setattr(api.platform, "system", lambda: system)
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(api.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "/chosen/design.cfg\n", ""))
    assert api.select_workspace_path("Open design", file=True) == api.PathPickerResult("chosen", "/chosen/design.cfg")


@pytest.mark.parametrize("selected", ["", "/chosen/design.cfg"])
def test_tk_picker_distinguishes_selection_and_cancel(selected, monkeypatch):
    import sys
    from types import SimpleNamespace
    from server.workspace import api

    monkeypatch.setenv("DISPLAY", ":test")
    destroyed = []
    root = SimpleNamespace(withdraw=lambda: None, attributes=lambda *args: None, destroy=lambda: destroyed.append(True))
    monkeypatch.setattr(api.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=lambda: root, filedialog=SimpleNamespace(askopenfilename=lambda **kwargs: selected)))
    expected = api.PathPickerResult("chosen", selected) if selected else api.PathPickerResult("cancelled")
    assert api.select_workspace_path("Open design", file=True) == expected
    assert destroyed == [True]


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_and_inline_stamps_normalize_without_geometry_or_hash_changes(inline, conflicting, caplog):
    first = parse(SOURCE)
    extra = f"; Waveguide Generator design-id: {OTHER if conflicting else UID}\n"
    text = STAMP + (SOURCE.replace("Throat.Diameter = 25.4", "Throat.Diameter = 25.4 " + extra.rstrip()) if inline else extra + SOURCE)
    parsed = parse(text)
    assert parsed.design_uid == (None if conflicting else UID)
    if conflicting:
        assert "Conflicting design-id stamps" in caplog.text
        assert asyncio.run(open_design(text))["designUid"] is None
    assert parsed.semantic_data() == first.semantic_data()
    assert parsed._initial_fingerprint == first._initial_fingerprint
    assert design_hash(parsed.design) == design_hash(first.design)
    for uid in [UID, OTHER]:
        normalized = serialize(parsed, design_uid=uid)
        assert normalized.count("Waveguide Generator design-id:") == 1
        assert f"; Waveguide Generator design-id: {uid}\n" in normalized.splitlines(keepends=True)
        reopened = parse(normalized)
        assert reopened.design_uid == uid
        assert reopened.semantic_data() == first.semantic_data()
        assert design_hash(reopened.design) == design_hash(first.design)
    if not conflicting:
        assert serialize(parsed).count("Waveguide Generator design-id:") == 1


def test_solve_submission_persists_document_identity_outside_geometry(tmp_path, monkeypatch):
    import json
    from server.app import create_app
    from server.tests.test_jobs_api import _request, _solve_body

    monkeypatch.setenv('WG2_ENABLE_DRYRUN', '1')

    async def scenario():
        app = create_app(data_dir=tmp_path)
        body = {**_solve_body(), 'design_uid': UID, 'design_id': 'wgd_01K00000000000000000000000'}
        status, raw = await _request(app, 'POST', '/api/solve', body=body)
        assert status == 200, raw
        job_id = json.loads(raw)['job_id']
        await app.state.jobs_runtime.wait_idle()
        row = app.state.jobs_runtime.store.get_job_row(job_id)
        assert row['task_metadata']['design_uid'] == UID
        assert row['task_metadata']['design_id'] == body['design_id']
        assert 'design_uid' not in row['config_json']['geometry']['design']
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_headless_linux_never_misreads_a_silent_picker_failure_as_cancel(monkeypatch):
    from server.workspace import api

    monkeypatch.setattr(api.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(api.subprocess, "run", lambda *args, **kwargs: pytest.fail("no display: skip native utilities"))
    assert api.select_workspace_path("Open design", file=True) == api.PathPickerResult("unavailable")


def test_native_payload_cannot_relocate_stamp_inside_a_block():
    payload = design_payload(FAMILIES[0])
    payload["extra_keys"] = {"Future.Key": "design-format: 2"}
    design = DesignConfig.model_validate(payload)
    text = serialize(design, design_uid=UID)
    assert parse(text).design_uid == UID
    assert text.count("Waveguide Generator design-id:") == 1
    assert parse(text).semantic_data() == design.model_dump(mode="json")
