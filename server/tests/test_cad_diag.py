"""The developer incident CLI: list, show, replay verdicts and redacted export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from server.platform.incidents import IncidentStore


class Clock:
    def __init__(self, start: datetime) -> None:
        self.moment = start

    def __call__(self) -> datetime:
        return self.moment


def _store(tmp_path: Path, **kwargs) -> IncidentStore:
    kwargs.setdefault("app_root", tmp_path / "app")
    kwargs.setdefault("home", tmp_path / "home")
    kwargs.setdefault("user", "ada-lovelace")
    return IncidentStore(tmp_path / "data", **kwargs)



def _cli(capsys, *args, **kwargs):
    from server.tools import cad_diag

    code = cad_diag.main([str(arg) for arg in args], **kwargs)
    return code, capsys.readouterr().out


def test_cli_list_and_show(tmp_path: Path, capsys) -> None:
    clock = Clock(datetime.now(timezone.utc) - timedelta(days=3))
    store = _store(tmp_path, now=clock)
    old = store.record("frame-fallback", "cad-preparation", evidence={"reason_code": "survey"})
    clock.moment = datetime.now(timezone.utc)
    new = store.record("internal-failure", "cad-preparation", evidence={"detail": "mesh child exited"})
    code, out = _cli(capsys, "--data-dir", tmp_path / "data", "list")
    assert code == 0 and out.index(new) < out.index(old)
    code, out = _cli(capsys, "--data-dir", tmp_path / "data", "list", "--since", "1d")
    assert new in out and old not in out
    code, out = _cli(capsys, "--data-dir", tmp_path / "data", "list", "--kind", "frame-fallback", "--json")
    assert [item["id"] for item in json.loads(out)] == [old]
    code, out = _cli(capsys, "--data-dir", tmp_path / "data", "show", new)
    assert json.loads(out)["latest"]["evidence"]["detail"] == "mesh child exited"
    with pytest.raises(SystemExit):
        _cli(capsys, "--data-dir", tmp_path / "data", "show", "missing")


_LOCATED = [{"stage": "locate source", "outcome": "ok"}, {"stage": "preparation", "outcome": "ok"}]


@pytest.mark.parametrize(("payload", "expected", "code"), [
    ({"outcome": "ok", "stages": _LOCATED, "logs": []}, "fixed", 0),
    ({"outcome": "refused", "stages": _LOCATED, "refusal": {"code": "preparation_failed"},
      "logs": [{"cad_diagnostic": {"stage": "internal-failure", "evidence": {"code": "preparation_failed"}}}]},
     "still fails", 1),
    ({"outcome": "refused", "stages": _LOCATED, "refusal": {"code": "model_cut_unsolvable"},
      "logs": [{"cad_diagnostic": {"stage": "domain", "evidence": {"code": "open_half"}}}]},
     "fails differently", 3),
    ({"outcome": "error", "stages": [{"stage": "locate source", "outcome": "error"}],
      "exception": {"type": "FileNotFoundError", "message": "gone"}}, "could not replay", 2),
])
def test_cli_replay_verdicts(tmp_path: Path, capsys, payload, expected, code) -> None:
    store = _store(tmp_path)
    incident = store.record("internal-failure", "cad-preparation", ids={"operation_id": "op-42"},
                            evidence={"code": "preparation_failed"})
    calls = []

    def runner(arguments):
        calls.append(list(arguments))
        return {**payload, "data_dir": "scratch"}

    exit_code, out = _cli(capsys, "--data-dir", tmp_path / "data", "replay", incident, replay_runner=runner)
    assert exit_code == code
    assert f"{incident}: {expected}" in out
    assert calls == [["--from-request", "op-42", "--live-data-dir", str((tmp_path / "data").resolve())]]


def test_cli_replay_uses_the_retained_bundle_without_a_request(tmp_path: Path, capsys) -> None:
    digest = "a" * 64
    bundle = tmp_path / "data" / "imports" / "bundles" / f"{digest}.wgreturn"
    bundle.mkdir(parents=True)
    store = _store(tmp_path)
    incident = store.record("frame-gate", "cad-preparation",
                            ids={"operation_id": "manual-solve:x", "manifest_sha256": f"sha256:{digest}"})
    calls = []
    _cli(capsys, "--data-dir", tmp_path / "data", "replay", incident,
         replay_runner=lambda arguments: calls.append(list(arguments)) or {"outcome": "ok", "stages": _LOCATED})
    assert calls == [[str((tmp_path / "data").resolve() / "imports" / "bundles" / f"{digest}.wgreturn"),
                      "--retained"]]


def test_cli_export_redacts_the_model(tmp_path: Path, capsys) -> None:
    import hashlib

    digest = "b" * 64
    bundle = tmp_path / "data" / "imports" / "bundles" / f"{digest}.wgreturn"
    bundle.mkdir(parents=True)
    step = "ISO-10303-21;\nHEADER;\nFILE_NAME('Tritonia speaker','2026',('Ada'),('Org'),'','','');\nENDSEC;\n"
    (bundle / "assembly.step").write_text(step, encoding="utf-8")
    (bundle / "document.f3d").write_bytes(b"cad document")
    (bundle / "wgreturn.json").write_text(json.dumps({
        "document": {"name": "Tritonia speaker"},
        "files": {"assembly.step": {"sha256": "sha256:old", "size_bytes": len(step)}},
    }), encoding="utf-8")
    store = _store(tmp_path)
    incident = store.record("internal-failure", "cad-preparation",
                            ids={"job_id": "job-9", "manifest_sha256": digest}, evidence={"code": "x"})
    out_dir = tmp_path / "export"
    code, _out = _cli(capsys, "--data-dir", tmp_path / "data", "export", incident, "--out", out_dir, "--redact")
    assert code == 0
    exported = json.loads((out_dir / "incident.json").read_text(encoding="utf-8"))
    assert exported["redacted"] is True and exported["latest"]["ids"] == {
        "job_id": "<redacted>", "manifest_sha256": "<redacted>"}
    model = out_dir / "model.wgreturn"
    assert not (model / "document.f3d").exists()
    manifest = json.loads((model / "wgreturn.json").read_text(encoding="utf-8"))
    assert manifest["document"]["name"] == "redacted-model"
    payload = (model / "assembly.step").read_bytes()
    assert b"Tritonia" not in payload and b"Ada" not in payload
    assert manifest["files"]["assembly.step"]["sha256"] == "sha256:" + hashlib.sha256(payload).hexdigest()


def test_cli_replays_a_retained_bundle_through_the_real_pipeline(tmp_path: Path, capsys) -> None:
    """End to end: the recorded digest, WG's retained copy, cad_replay in its own process."""

    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    import shutil

    import cad_frame_fixtures as fx
    from test_cad_frame_infer_real import _bundle

    from server.cadlink.intake.ingest import read_snapshot, retained_snapshot_path
    from server.tools import cad_diag

    source, _manifest = _bundle(tmp_path / "source", "replay", fx.horn_box, "+z")
    digest = read_snapshot(source).manifest_sha256
    retained = retained_snapshot_path(tmp_path / "data", digest)
    retained.parent.mkdir(parents=True)
    shutil.copytree(source, retained)
    store = _store(tmp_path)
    incident = store.record("internal-failure", "cad-preparation",
                            ids={"operation_id": "manual-solve:1", "manifest_sha256": digest},
                            evidence={"code": "preparation_failed"})
    reports = []

    def runner(arguments):
        payload = cad_diag.run_cad_replay([*arguments, "--data-dir", str(tmp_path / "scratch")])
        reports.append(payload)
        return payload

    code, out = _cli(capsys, "--data-dir", tmp_path / "data", "replay", incident, replay_runner=runner)
    assert reports[0]["outcome"] == "ok", reports[0]
    assert code == 0 and f"{incident}: fixed" in out
    assert not (tmp_path / "data" / "db").exists()  # the live data directory is untouched
