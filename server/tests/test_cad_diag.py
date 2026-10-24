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


def test_cli_could_not_replay_has_its_own_exit_code(tmp_path: Path, capsys) -> None:
    from server.tools.cad_diag import NotReplayable

    store = _store(tmp_path)
    unreplayable = store.record("frame-gate", "cad-preparation", ids={"job_id": "job-1"})
    replayable = store.record("internal-failure", "cad-preparation", ids={"operation_id": "op-1"})

    def no_report(_arguments):
        raise NotReplayable("cad_replay did not produce a report")

    for incident, runner in (("missing", no_report), (unreplayable, no_report), (replayable, no_report)):
        code, out = _cli(capsys, "--data-dir", tmp_path / "data", "replay", incident, replay_runner=runner)
        assert code == 2, incident  # never 1, which is "still fails"


#: A model named like a real one, in every place a Fusion return names it.
DOCUMENT = "Tritonia speaker"
STEP_TEXT = (
    "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Tritonia speaker v3'),'2;1');\n"
    "FILE_NAME('Tritonia speaker v3.step','2026-10-24',('Ada Lovelace'),('Lovelace Audio'),'','','');\n"
    "ENDSEC;\nDATA;\n"
    "#1=PRODUCT('Tritonia speaker v3','Tritonia speaker v3','',(#9));\n"
    "#2=PRODUCT('Waveguide L\\X2\\00E9\\X0\\on','Waveguide L\\X2\\00E9\\X0\\on','',(#9));\n"
    "#3=NEXT_ASSEMBLY_USAGE_OCCURRENCE('Waveguide L\\X2\\00E9\\X0\\on:1','Waveguide L\\X2\\00E9\\X0\\on:1','',#5,#6,$);\n"
    "#4=MANIFOLD_SOLID_BREP('Front baffle',#7);\n"
    "#8=PRODUCT_DEFINITION('design','',#5,#9);\n"
    "ENDSEC;\nEND-ISO-10303-21;\n"
)
SECRETS = (
    DOCUMENT, "Tritonia", "Waveguide L", "Front baffle", "Ada Lovelace", "Lovelace Audio",
    "urn:adsk.wipprod:dm.lineage:SECRETDOC", "wgd_01SECRETDESIGN", "wge_01SECRETEXPORT",
    "wgl_01SECRETLINEAGE", "wgb_01SECRETBUNDLE", "wgr_01SECRETRETURN", "job-secret-9",
    "op-secret-4", "ingest-secret-2", "b" * 64, "speaker_body_token",
)


def _secret_bundle(tmp_path: Path) -> tuple[Path, str]:
    digest = "b" * 64
    bundle = tmp_path / "data" / "imports" / "bundles" / f"{digest}.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_text(STEP_TEXT, encoding="utf-8")
    (bundle / "document.f3d").write_bytes(b"cad document")
    (bundle / "stray-Tritonia.txt").write_text("not a member", encoding="utf-8")
    (bundle / "wgreturn.json").write_text(json.dumps({
        "document": {"name": DOCUMENT, "native_id": "urn:adsk.wipprod:dm.lineage:SECRETDOC"},
        "return": {"id": "wgr_01SECRETRETURN", "created_at": "2026-10-24T09:00:00Z"},
        "assembly": {"file": "assembly.step", "n_bodies_expected": 1},
        "files": {"assembly.step": {"sha256": "sha256:old", "size_bytes": len(STEP_TEXT), "purpose": "exterior-assembly"}},
        "instances": [{
            "instance_id": "instance-1", "design_id": "wgd_01SECRETDESIGN", "export_id": "wge_01SECRETEXPORT",
            "lineage_id": "wgl_01SECRETLINEAGE", "origin_bundle_id": "wgb_01SECRETBUNDLE",
            "occurrence_path": "Tritonia speaker v3/Waveguide Léon:1", "build_mode": "enclosure",
            "parameter_prefix": "wg_tritonia_",
        }],
        "scope": {"included": [{"name": "Front baffle", "component": "Waveguide Léon",
                                "object_id": "speaker_body_token", "body_kind": "solid"}]},
        "sources": [{"id": "source-hf", "role": "HF", "observed": {"bodies": ["Front baffle"]}}],
    }), encoding="utf-8")
    return bundle, digest


def test_cli_export_redacts_every_name_and_id(tmp_path: Path, capsys) -> None:
    import hashlib
    import re

    _bundle, digest = _secret_bundle(tmp_path)
    store = _store(tmp_path)
    ids = {"job_id": "job-secret-9", "operation_id": "op-secret-4", "ingest_id": "ingest-secret-2",
           "manifest_sha256": f"sha256:{digest}"}
    incident = store.record(
        "domain", "cad-preparation", ids=ids,
        evidence={**ids, "code": "open_half",
                  "message": f"WG cannot rebuild the speaker {DOCUMENT} belongs to.",
                  "detail": "Front baffle of Tritonia speaker v3 is open", "cad": {"document_name": DOCUMENT}},
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "job-secret-9.log").write_text(
        f"job job-secret-9 preparing {DOCUMENT} (operation op-secret-4)\nmeshing failed\n", encoding="utf-8")
    store.retain_job_logs(["job-secret-9"], lambda job_id: logs / f"{job_id}.log")
    out_dir = tmp_path / "export"
    code, _out = _cli(capsys, "--data-dir", tmp_path / "data", "export", incident, "--out", out_dir, "--redact")
    assert code == 0

    exported = {path.relative_to(out_dir).as_posix(): path.read_bytes() for path in out_dir.rglob("*") if path.is_file()}
    assert set(exported) == {"incident.json", "model.wgreturn/wgreturn.json", "model.wgreturn/assembly.step"}
    for name, payload in exported.items():
        text = payload.decode("utf-8")
        for secret in SECRETS:
            assert secret.lower() not in text.lower(), (name, secret)
        assert "L\\X2\\00E9\\X0\\on" not in text and "Léon" not in text, name

    record = json.loads(exported["incident.json"])
    assert record["redacted"] is True
    assert record["latest"]["ids"] == {key: "<redacted>" for key in ids}
    assert record["latest"]["evidence"]["job_id"] == "<redacted>"
    assert "redacted-model" in record["latest"]["evidence"]["message"]
    assert list(record["jobLogs"]) == ["job-1"]
    assert record["jobLogs"]["job-1"]["tail"].endswith("meshing failed\n")
    assert record["latest"]["evidence"]["code"] == "open_half"  # fields keep their names

    manifest = json.loads(exported["model.wgreturn/wgreturn.json"])
    assert manifest["document"] == {"name": "redacted-model", "native_id": None}
    [instance] = manifest["instances"]
    assert "origin_bundle_id" not in instance
    assert re.fullmatch(r"wgd_\d{26}", instance["design_id"]) and re.fullmatch(r"wge_\d{26}", instance["export_id"])
    assert instance["build_mode"] == "enclosure" and instance["instance_id"] == "instance-1"
    assert re.fullmatch(r"wgr_[0-9A-HJKMNP-TV-Z]{26}", manifest["return"]["id"])
    # One original, one stand-in, everywhere: a body keeps matching its STEP solid.
    body = manifest["scope"]["included"][0]["name"]
    assert manifest["sources"][0]["observed"]["bodies"] == [body]
    step = exported["model.wgreturn/assembly.step"].decode("ascii")
    assert f"MANIFOLD_SOLID_BREP('{body}',#7);" in step
    component = manifest["scope"]["included"][0]["component"]
    assert f"NEXT_ASSEMBLY_USAGE_OCCURRENCE('{component}:1','{component}:1'" in step
    assert instance["occurrence_path"].endswith(f"/{component}:1")
    assert "PRODUCT_DEFINITION('design','',#5,#9);" in step  # vocabulary is untouched
    # Still a STEP file: every entity line is whole and every literal closed.
    for line in step.splitlines():
        if line.startswith("#"):
            assert line.endswith(";") and line.count("'") % 2 == 0, line
    payload = exported["model.wgreturn/assembly.step"]
    assert manifest["files"]["assembly.step"]["sha256"] == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert manifest["files"]["assembly.step"]["size_bytes"] == len(payload)


def test_cli_export_without_redact_copies_the_bundle_but_not_the_document(tmp_path: Path, capsys) -> None:
    bundle, digest = _secret_bundle(tmp_path)
    store = _store(tmp_path)
    incident = store.record("domain", "cad-preparation", ids={"manifest_sha256": digest})
    out_dir = tmp_path / "export"
    code, _out = _cli(capsys, "--data-dir", tmp_path / "data", "export", incident, "--out", out_dir)
    assert code == 0
    assert (out_dir / bundle.name / "assembly.step").read_text(encoding="utf-8") == STEP_TEXT
    assert not (out_dir / bundle.name / "document.f3d").exists()


def test_cli_replays_a_retained_bundle_through_the_real_pipeline(tmp_path: Path, capsys) -> None:
    """End to end: the recorded digest, WG's retained copy, cad_replay in its own process."""

    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    import shutil

    import cad_frame_fixtures as fx
    from test_cad_frame_infer_real import _bundle

    from server.cadlink.intake.ingest import read_snapshot, retained_snapshot_path
    from server.tools import cad_diag

    import hashlib
    import re

    source, manifest = _bundle(tmp_path / "source", "replay", fx.horn_box, "+z")
    # Named the way a Fusion export names it: the root product is the document.
    step_path = source / "assembly.step"
    step = re.sub(r"PRODUCT\('[^']*',\s*'[^']*'", "PRODUCT('Tritonia speaker v3','Tritonia speaker v3'",
                  step_path.read_text(encoding="utf-8"))
    step_path.write_text(step, encoding="utf-8")
    payload = step_path.read_bytes()
    manifest["files"]["assembly.step"].update(
        sha256="sha256:" + hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
    manifest["document"] = {"name": "Tritonia speaker", "native_id": "urn:adsk.wipprod:fs.file:SECRET"}
    (source / "wgreturn.json").write_text(json.dumps(manifest), encoding="utf-8")
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

    # The redacted export is still a fixture: it names nothing and still prepares.
    _cli(capsys, "--data-dir", tmp_path / "data", "export", incident, "--out", tmp_path / "fixture", "--redact")
    model = tmp_path / "fixture" / "model.wgreturn"
    for path in model.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "tritonia" not in text.lower() and "SECRET" not in text, path
    redacted = cad_diag.run_cad_replay([str(model), "--retained", "--data-dir", str(tmp_path / "scratch-redacted")])
    assert redacted["outcome"] == "ok", json.dumps(redacted.get("exception"), default=str)[:3000]
