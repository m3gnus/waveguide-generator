"""Identity-bearing workspace bundle export contracts."""

from __future__ import annotations

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi import HTTPException

from hornlab_mesher import read_wglink
import pytest

from server.cadlink.identity import SaveIdentity, design_hash
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.design.textcfg import serialize
from server.exports import wglink_service as service

pytestmark = pytest.mark.usefixtures("in_process_mesher")


def _design() -> DesignConfig:
    return DesignConfig.model_validate({"formula": "OSSE", "L": 120, "a": 45})


def _saved(store: CadLinkStore, design: DesignConfig):
    return store.save(
        requested=None,
        design_hash=design_hash(design),
        filename="demo-horn.cfg",
        snapshot_builder=lambda identity: serialize(design, cadlink=identity),
        saved_at="2026-08-10T14:22:31Z",
    )


def _design_count(store: CadLinkStore) -> int:
    row = store._read_one("SELECT COUNT(*) AS count FROM designs", ())
    assert row is not None
    return int(row["count"])


def _request(
    saved, design: DesignConfig | None = None, base_name: str = "demo horn.cfg"
) -> service.WgLinkExportRequest:
    identity = saved["identity"]
    return service.WgLinkExportRequest.model_validate({
        "design": (design or _design()).model_dump(mode="json"),
        "designRevision": 3,
        "baseName": base_name,
        "identity": {
            "designId": identity.design_id,
            "lineageId": identity.lineage_id,
            "baseEditVersion": identity.edit_version,
        },
    })


def _install_fake_write(monkeypatch) -> list[object]:
    """Stub the mesher so these identity contracts cost no geometry work."""

    writes: list[object] = []

    def fake_write(geometry, output_path, *, identity, **kwargs):
        writes.append({"geometry": geometry, "kwargs": kwargs})
        target = Path(output_path)
        target.mkdir()
        step_hash = "sha256:" + hashlib.sha256(b"STEP").hexdigest()
        manifest = {
            "wglink_version": "1.0",
            "bundle": dict(identity.bundle or {}),
            "generator": dict(identity.generator or {}),
            "design": dict(identity.design or {}),
            "export": dict(identity.export or {}),
            "files": {"waveguide.step": {"sha256": step_hash, "size_bytes": 4}},
            # The one parameter the namespace is read back from, named the way
            # the mesher names it, so recovery from a stored manifest is real.
            "parameters": [
                {
                    "name": f"wg_{kwargs['instance_slug']}_throat_dia",
                    "value": 25.4,
                    "unit": "mm",
                    "role": "interface",
                }
            ],
        }
        (target / "waveguide.step").write_bytes(b"STEP")
        manifest_path = target / "wglink.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
        return SimpleNamespace(path=target, manifest_path=manifest_path, manifest=manifest)

    monkeypatch.setattr("hornlab_mesher.write_wglink", fake_write)
    return writes


@pytest.mark.parametrize("width,height,refused", [(300, 400, True), (300.5, 400.5, False)])
def test_wglink_refuses_flush_but_keeps_previously_valid_zero_margin(
    monkeypatch, tmp_path: Path, width: float, height: float, refused: bool,
) -> None:
    design = DesignConfig.model_validate({
        "formula": "OSSE", "L": 120, "a": 45,
        "morph": {"target_shape": 1, "target_width": width, "target_height": height},
        "enclosure": {"depth": 280, "space_l": 0, "space_r": 0,
                      "space_t": 0, "space_b": 0},
    })
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, design)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes = _install_fake_write(monkeypatch)

    if refused:
        with pytest.raises(HTTPException) as error:
            service.export_wglink_sync(_request(saved, design), store, workspace, "flush")
        assert error.value.status_code == 422
        assert "Flush enclosures cannot be sent to Fusion" in error.value.detail
        assert not writes
        assert not (workspace / "wglink").exists()
        assert store.find_export_by_idempotency_key("flush") is None
    else:
        result = service.export_wglink_sync(_request(saved, design), store, workspace, "fractional")
        assert Path(result["bundlePath"]).is_dir()
        assert len(writes) == 1


def test_the_cad_bundle_is_built_without_the_export_sizing_planners(
    monkeypatch, tmp_path: Path,
) -> None:
    """Why no export-sizing warning rides on Send to CAD, proved by running it.

    The geometry exports size themselves against a fidelity tolerance and can
    report that they missed it. This bundle does not: it hands the design's own
    resolved geometry to the mesher's ``write_wglink``, so there is no plan and
    no reading to pass on. That is a real scope gap and it is documented in the
    export contract -- but it is only true while the call graph stays this
    shape, so the planners are replaced with detonators rather than grepped for.
    If a later change routes this bundle through the sizing path, this fails and
    says the warning has to be plumbed with it.
    """

    from server.exports import core, sizing

    def detonate(*_args, **_kwargs):  # pragma: no cover - only runs on failure
        raise AssertionError(
            "the CAD bundle reached the export sizing planners; its compromises "
            "are no longer outside the warning path and must be plumbed"
        )

    for module, name in (
        (core, "_surface_grid_plan"),
        (core, "_stl_grid_plan"),
        (core, "_build_step_solid_sync"),
        (sizing, "plan_cad_resolution"),
        (sizing, "plan_grid"),
    ):
        monkeypatch.setattr(module, name, detonate)

    design = _design()
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, design)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)

    row = service.export_wglink_sync(_request(saved), store, workspace, "no-planner")

    assert row["artifactSha256"] == "sha256:" + hashlib.sha256(b"STEP").hexdigest()
    manifest = json.loads((Path(row["bundlePath"]) / "wglink.json").read_text())
    assert manifest["files"]["waveguide.step"]["sha256"]
    # Nothing in the bundle response or its manifest carries a sizing warning,
    # because nothing in the path can produce one.
    assert not any("warning" in key.lower() for key in row)
    assert not any("warning" in key.lower() for key in manifest)


def test_wglink_export_writes_identity_hashes_and_retries_without_rebuilding(
    monkeypatch, tmp_path: Path,
) -> None:
    design = _design()
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, design)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes = _install_fake_write(monkeypatch)

    first = service.export_wglink_sync(_request(saved), store, workspace, "attempt-1")
    retry = service.export_wglink_sync(_request(saved), store, workspace, "attempt-1")
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()
    retry_from_other_workspace = service.export_wglink_sync(
        _request(saved), store, other_workspace, "attempt-1"
    )
    second = service.export_wglink_sync(_request(saved), store, workspace, "attempt-2")
    original_identity = saved["identity"]
    store.save(
        requested=SaveIdentity.model_validate({
            "designId": original_identity.design_id,
            "lineageId": original_identity.lineage_id,
            "baseEditVersion": original_identity.edit_version,
        }),
        design_hash=design_hash(design),
        filename="demo-horn.cfg",
        snapshot_builder=lambda identity: serialize(design, cadlink=identity),
    )
    stale_retry = service.export_wglink_sync(_request(saved), store, workspace, "attempt-1")

    assert stale_retry["exportId"] == first["exportId"]
    locator = json.loads((workspace / "wglink" / f'{first["identity"]["designId"]}.latest.json').read_text())
    assert locator["exportId"] == second["exportId"]
    assert retry == retry_from_other_workspace == first
    assert len(writes) == 2
    source = writes[0]["kwargs"]["interface_sources"][0]
    assert source.id == "source-hf"
    assert source.role == "HF"
    assert source.default_drive_channel_id == "drive-hf"
    assert source.suggested_resolution_mm == pytest.approx(4.0)
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["bundlePath"] == str(workspace / "wglink" / f'{first["exportId"]}.wglink')
    assert first["designHash"] == design_hash(design)
    assert first["geometryHash"].startswith("sha256:")
    assert first["artifactSha256"] == "sha256:" + hashlib.sha256(b"STEP").hexdigest()
    manifest = json.loads((Path(first["bundlePath"]) / "wglink.json").read_text())
    assert read_wglink(first["bundlePath"], verify_checksums=False) == manifest
    assert manifest["bundle"]["id"] == first["bundleId"]
    assert manifest["wglink_version"] == "1.0"
    assert manifest["conventions"] == {
        "frame": {
            "axes": {
                "x": "horizontal",
                "y": "vertical",
                "z": "axial (throat to mouth)",
            },
            "axis_remap_matrix": [[1, 0, 0], [0, -1, 0], [0, 0, 1]],
            "winding": "reversed-on-remap",
        },
        "units": {
            "solver_length": "m",
            "cad_length": "mm",
            "frequency": "Hz",
            "phase": "degrees",
        },
        "phasor": "exp(-i omega t)",
    }
    assert manifest["export"] == {
        "domain": "full",
        "geometry_hash": first["geometryHash"],
        "id": first["exportId"],
        "open_throat": True,
        "parent_export_id": None,
        "sequence": 1,
    }


def test_wglink_export_commits_an_unsaved_or_edited_design(
    monkeypatch, tmp_path: Path,
) -> None:
    """Send to CAD records the design on screen rather than refusing it.

    The bundle needs a registry row to name, not a .cfg the user has downloaded,
    so a never-saved design and an edited one both export.
    """

    store = CadLinkStore(tmp_path / "cadlink.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)

    never_saved = service.WgLinkExportRequest.model_validate({
        "design": _design().model_dump(mode="json"),
        "designRevision": 0,
        "baseName": "demo horn.cfg",
        "identity": None,
    })
    minted = service.export_wglink_sync(never_saved, store, workspace, "unsaved")
    assert minted["sequence"] == 1
    assert minted["identity"]["baseEditVersion"] == 1
    assert minted["designHash"] == design_hash(_design())
    head = store.get_design(minted["identity"]["designId"])
    assert head is not None
    assert head["design_hash"] == design_hash(_design())
    assert head["snapshot_text"]

    saved = _saved(store, _design())
    changed = DesignConfig.model_validate({"formula": "OSSE", "L": 121, "a": 45})
    exported = service.export_wglink_sync(
        _request(saved, changed, "edited horn.cfg"), store, workspace, "changed"
    )
    identity = saved["identity"]
    assert exported["identity"] == {
        "designId": identity.design_id,
        "lineageId": identity.lineage_id,
        "baseEditVersion": identity.edit_version + 1,
    }
    assert exported["designHash"] == design_hash(changed)
    edited_head = store.get_design(identity.design_id)
    assert edited_head is not None
    assert edited_head["design_hash"] == design_hash(changed)


def test_wglink_export_reuses_commit_left_by_failed_export(
    monkeypatch, tmp_path: Path,
) -> None:
    design = _design()
    edited = DesignConfig.model_validate({"formula": "OSSE", "L": 121, "a": 45})
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, design)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)
    request = _request(saved, edited, "demo-horn.cfg")

    from hornlab_mesher.config_builder import resolve_geometry as real_resolve_geometry

    calls = 0

    def fail_once(config):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated geometry failure")
        return real_resolve_geometry(config)

    monkeypatch.setattr(
        "hornlab_mesher.config_builder.resolve_geometry", fail_once
    )

    with pytest.raises(HTTPException, match="WG could not finish this request.*recorded the problem"):
        service.export_wglink_sync(request, store, workspace, "failed-attempt")

    original_identity = saved["identity"]
    head_after_failure = store.get_design(original_identity.design_id)
    assert head_after_failure is not None
    assert head_after_failure["edit_version"] == original_identity.edit_version + 1
    assert head_after_failure["design_hash"] == design_hash(edited)
    assert _design_count(store) == 1

    retried = service.export_wglink_sync(request, store, workspace, "retry-attempt")

    assert retried["identity"] == {
        "designId": original_identity.design_id,
        "lineageId": original_identity.lineage_id,
        "baseEditVersion": head_after_failure["edit_version"],
    }
    assert _design_count(store) == 1
    manifest = json.loads((Path(retried["bundlePath"]) / "wglink.json").read_text())
    assert manifest["design"]["id"] == original_identity.design_id
    assert manifest["design"]["formula"] == "osse"
    assert manifest["design"]["config"]["formula"] == "OSSE"
    assert manifest["design"]["config"]["L"]["value"] == 121


def test_wglink_idempotent_retry_omits_identity_after_head_advanced(
    monkeypatch, tmp_path: Path,
) -> None:
    design = _design()
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, design)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)
    request = _request(saved, base_name="demo-horn.cfg")

    first = service.export_wglink_sync(request, store, workspace, "same-attempt")
    original_identity = saved["identity"]
    advanced = DesignConfig.model_validate({"formula": "OSSE", "L": 121, "a": 45})
    store.save(
        requested=SaveIdentity.model_validate({
            "designId": original_identity.design_id,
            "lineageId": original_identity.lineage_id,
            "baseEditVersion": original_identity.edit_version,
        }),
        design_hash=design_hash(advanced),
        filename="demo-horn.cfg",
        snapshot_builder=lambda identity: serialize(advanced, cadlink=identity),
    )

    retry = service.export_wglink_sync(request, store, workspace, "same-attempt")

    assert "identity" in first
    assert "identity" not in retry


def test_wglink_idempotent_retry_keeps_identity_while_head_is_unchanged(
    monkeypatch, tmp_path: Path,
) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)
    request = _request(saved, base_name="demo-horn.cfg")

    first = service.export_wglink_sync(request, store, workspace, "same-attempt")
    retry = service.export_wglink_sync(request, store, workspace, "same-attempt")

    assert retry["identity"] == first["identity"]


def test_wglink_sequential_identical_edits_reuse_the_advanced_head(
    monkeypatch, tmp_path: Path,
) -> None:
    original = _design()
    edited = DesignConfig.model_validate({"formula": "OSSE", "L": 121, "a": 45})
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, original)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)
    stale_request = _request(saved, edited, "demo-horn.cfg")

    first = service.export_wglink_sync(stale_request, store, workspace, "first-send")
    second = service.export_wglink_sync(stale_request, store, workspace, "second-send")

    original_identity = saved["identity"]
    assert first["identity"] == second["identity"] == {
        "designId": original_identity.design_id,
        "lineageId": original_identity.lineage_id,
        "baseEditVersion": original_identity.edit_version + 1,
    }
    assert _design_count(store) == 1


def test_wglink_identical_content_with_renamed_design_bumps_filename(
    monkeypatch, tmp_path: Path,
) -> None:
    design = _design()
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, design)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)

    exported = service.export_wglink_sync(
        _request(saved, base_name="renamed horn.cfg"),
        store,
        workspace,
        "renamed-send",
    )

    original_identity = saved["identity"]
    assert exported["identity"] == {
        "designId": original_identity.design_id,
        "lineageId": original_identity.lineage_id,
        "baseEditVersion": original_identity.edit_version + 1,
    }
    head = store.get_design(original_identity.design_id)
    assert head is not None
    assert head["filename"] == "renamed_horn.cfg"
    assert _design_count(store) == 1


def _parameter_names(result: dict[str, object]) -> list[str]:
    manifest = json.loads((Path(str(result["bundlePath"])) / "wglink.json").read_text())
    return [str(entry["name"]) for entry in manifest["parameters"]]


def test_parameter_namespace_survives_a_rename_with_new_bundle(
    monkeypatch, tmp_path: Path,
) -> None:
    """Save As must not move the namespace Fusion's expressions reference.

    The slug is minted once per lineage: the linked document's datums and
    enclosure sketches name ``wg_<slug>_*`` forever, and no update can retarget
    them, so following the filename made the first Save As an unrecoverable
    break.
    """

    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes = _install_fake_write(monkeypatch)

    first = service.export_wglink_sync(_request(saved), store, workspace, "first-send")
    renamed = service.export_wglink_sync(
        _request(saved, base_name="tritonia.cfg"), store, workspace, "renamed-send"
    )

    assert [write["kwargs"]["instance_slug"] for write in writes] == [
        "demo_horn",
        "demo_horn",
    ]
    assert _parameter_names(first) == _parameter_names(renamed) == [
        "wg_demo_horn_throat_dia"
    ]
    assert first["bundlePath"] != renamed["bundlePath"]
    assert Path(first["bundlePath"]).is_dir()
    assert Path(renamed["bundlePath"]).name == f'{renamed["exportId"]}.wglink'
    # Only the namespace is frozen; the readable name still follows the file.
    manifest = json.loads((Path(str(renamed["bundlePath"])) / "wglink.json").read_text())
    assert manifest["design"]["name"] == "tritonia"
    names = store.get_lineage_cad_names(str(renamed["identity"]["lineageId"]))
    assert names is not None
    assert names["parameter_slug"] == "demo_horn"


def test_stale_copy_cannot_silently_fork_the_linked_cad_project(
    monkeypatch, tmp_path: Path,
) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes = _install_fake_write(monkeypatch)

    original = service.export_wglink_sync(_request(saved), store, workspace, "original")
    advanced = DesignConfig.model_validate({"formula": "OSSE", "L": 122, "a": 45})
    store.save(
        requested=SaveIdentity.model_validate({
            "designId": str(original["identity"]["designId"]),
            "lineageId": str(original["identity"]["lineageId"]),
            "baseEditVersion": int(original["identity"]["baseEditVersion"]),
        }),
        design_hash=design_hash(advanced),
        filename="demo_horn.cfg",
        snapshot_builder=lambda identity: serialize(advanced, cadlink=identity),
    )

    with pytest.raises(HTTPException, match="older copy of a linked design") as stale:
        service.export_wglink_sync(
            _request(saved, base_name="tritonia.cfg"), store, workspace, "stale"
        )

    assert stale.value.status_code == 409
    assert len(writes) == 1
    assert _design_count(store) == 1
    assert store.find_export_by_idempotency_key("stale") is None


def test_a_fresh_lineage_mints_its_namespace_from_the_current_name(
    monkeypatch, tmp_path: Path,
) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes = _install_fake_write(monkeypatch)

    first = service.export_wglink_sync(
        _request(_saved(store, _design()), base_name="demo horn.cfg"),
        store,
        workspace,
        "first-lineage",
    )
    second = service.export_wglink_sync(
        _request(_saved(store, _design()), base_name="tritonia mk2.cfg"),
        store,
        workspace,
        "second-lineage",
    )

    assert [write["kwargs"]["instance_slug"] for write in writes] == [
        "demo_horn",
        "tritonia_mk2",
    ]
    assert _parameter_names(first) == ["wg_demo_horn_throat_dia"]
    assert _parameter_names(second) == ["wg_tritonia_mk2_throat_dia"]


def test_a_lineage_linked_before_the_registry_recovers_its_namespace(
    monkeypatch, tmp_path: Path,
) -> None:
    """Links made before schema 7 must keep updating, not mint a second slug."""

    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)

    first = service.export_wglink_sync(_request(saved), store, workspace, "before")
    with store._lock, store._transaction() as conn:
        conn.execute("DELETE FROM lineage_cad_names")

    renamed = service.export_wglink_sync(
        _request(saved, base_name="tritonia.cfg"), store, workspace, "after"
    )

    assert _parameter_names(renamed) == ["wg_demo_horn_throat_dia"]
    assert renamed["bundlePath"] != first["bundlePath"]
    names = store.get_lineage_cad_names(str(renamed["identity"]["lineageId"]))
    assert names is not None
    assert names["parameter_slug"] == "demo_horn"


def test_wglink_export_refuses_a_stale_identity_and_adopts_an_unknown_one(
    monkeypatch, tmp_path: Path,
) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _install_fake_write(monkeypatch)
    saved = _saved(store, _design())
    original_identity = saved["identity"]
    advanced = DesignConfig.model_validate({"formula": "OSSE", "L": 122, "a": 45})
    store.save(
        requested=SaveIdentity.model_validate({
            "designId": original_identity.design_id,
            "lineageId": original_identity.lineage_id,
            "baseEditVersion": original_identity.edit_version,
        }),
        design_hash=design_hash(advanced),
        filename="demo-horn.cfg",
        snapshot_builder=lambda identity: serialize(advanced, cadlink=identity),
    )

    # Exporting from an older copy must not silently orphan the existing CAD
    # document by assigning this editor a second design ID in the lineage.
    with pytest.raises(HTTPException, match="CAD-linked designs") as stale:
        service.export_wglink_sync(_request(saved), store, workspace, "stale")
    assert stale.value.status_code == 409

    unknown = _request(saved, base_name="adopted horn.cfg")
    unknown.identity = SaveIdentity.model_validate({
        "designId": "wgd_01K00000000000000000000099",
        "lineageId": original_identity.lineage_id,
        "baseEditVersion": original_identity.edit_version,
    })
    adopted = service.export_wglink_sync(unknown, store, workspace, "unknown")
    assert adopted["identity"] == {
        "designId": "wgd_01K00000000000000000000099",
        "lineageId": original_identity.lineage_id,
        "baseEditVersion": original_identity.edit_version + 1,
    }


def test_concurrent_designs_cannot_claim_the_same_bundle_name(
    monkeypatch, tmp_path: Path,
) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    first_saved = _saved(store, _design())
    second_saved = _saved(store, _design())
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fake_write(_geometry, output_path, *, identity, **_kwargs):
        target = Path(output_path)
        target.mkdir()
        step_hash = "sha256:" + hashlib.sha256(b"STEP").hexdigest()
        manifest = {
            "bundle": dict(identity.bundle or {}),
            "design": dict(identity.design or {}),
            "export": dict(identity.export or {}),
            "files": {"waveguide.step": {"sha256": step_hash}},
        }
        (target / "waveguide.step").write_text("STEP")
        manifest_path = target / "wglink.json"
        manifest_path.write_text(json.dumps(manifest))
        return SimpleNamespace(
            path=target, manifest_path=manifest_path, manifest=manifest
        )

    monkeypatch.setattr("hornlab_mesher.write_wglink", fake_write)

    def export(args):
        saved, key = args
        try:
            return service.export_wglink_sync(_request(saved), store, workspace, key)
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(export, [(first_saved, "first"), (second_saved, "second")]))

    successes = [result for result in results if isinstance(result, dict)]
    assert len(successes) == 2
    assert len({result["bundlePath"] for result in successes}) == 2
    assert all(Path(result["bundlePath"]).is_dir() for result in successes)


def test_wglink_export_rejects_a_stale_workspace_path(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    workspace = tmp_path / "removed-workspace"

    with pytest.raises(HTTPException, match="workspace folder is unavailable") as caught:
        service.export_wglink_sync(_request(saved), store, workspace, "stale-workspace")

    assert caught.value.status_code == 409
    assert not workspace.exists()


@pytest.mark.parametrize('mesh', [
    {'ath_roundover': {'radius': 10, 'segments': 5}},
    {'ath_rear_shape': 3, 'ath_rear_front_offset': 40, 'ath_rear_step': 25},
])
def test_ath_cad_link_refusal_is_422_before_identity_or_geometry_work(tmp_path, monkeypatch, mesh):
    design = DesignConfig.model_validate({
        'formula': 'OSSE', 'L': 80, 'text_import_version': 'ath-2026-09a-v2', 'mesh': mesh,
    })
    store = CadLinkStore(tmp_path / 'cadlink.sqlite3')
    request = service.WgLinkExportRequest(design=design, designRevision=1)
    def unexpected(*args, **kwargs):
        pytest.fail('a refused CAD link must not commit or build geometry')
    monkeypatch.setattr(service, '_commit_design_for_export', unexpected)
    monkeypatch.setattr('hornlab_mesher.config_builder.resolve_geometry', unexpected)
    with pytest.raises(HTTPException) as caught:
        service.export_wglink_sync(request, store, tmp_path, 'ath-refused')
    assert caught.value.status_code == 422
    assert 'STEP export of ATH' in caught.value.detail
    assert not (tmp_path / 'cadlink.sqlite3').exists()
    assert not (tmp_path / 'wglink').exists()


@pytest.mark.parametrize("saved_first", [False, True])
@pytest.mark.parametrize("stop_stage", ["mesh", "check"])
def test_stopped_send_restores_design_registry_and_allows_next_edit(
    tmp_path, monkeypatch, saved_first, stop_stage,
):
    from server.jobs.request_cancellation import ClientDisconnected

    store = CadLinkStore(tmp_path / "cadlink.db")
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    saved = _saved(store, _design()) if saved_first else None
    from server.workspace.archive_identity import claim_project_folder

    uid = "wgu_01K00000000000000000000001"
    claim_project_folder(store, f"design:{uid}", "demo-horn")
    before = store.list_designs()
    names_before = store._read_one("SELECT count(*) AS n FROM lineage_cad_names", ())
    with store._lock:
        archives_before = [dict(row) for row in store._connect().execute("SELECT * FROM archive_folders")]
    head = store.get_design(saved["identity"].design_id) if saved else None
    edited = DesignConfig.model_validate({"formula": "OSSE", "L": 130, "a": 45})
    request = _request(saved, edited, "demo-horn") if saved else service.WgLinkExportRequest(
        design=edited, designRevision=1, baseName="demo-horn",
    )
    request = request.model_copy(update={"design_uid": uid})
    stopped = False

    def cancel():
        if stopped:
            raise ClientDisconnected()

    _install_fake_write(monkeypatch)
    native_build = service.run_mesh_build

    async def build(*args, **kwargs):
        nonlocal stopped
        if stop_stage == "mesh":
            stopped = True
            cancel()
        return await native_build(*args, **kwargs)

    def progress(stage):
        nonlocal stopped
        if stage == stop_stage:
            stopped = True

    monkeypatch.setattr(service, "run_mesh_build", build)
    try:
        with pytest.raises(ClientDisconnected):
            service.export_wglink_sync(request, store, workspace, "stop", cancel_cb=cancel, progress_cb=progress)
        assert store.list_designs() == before
        if saved:
            assert store.get_design(saved["identity"].design_id) == head
        else:
            assert store._read_one("SELECT count(*) AS n FROM lineage_cad_names", ())["n"] == 0
        assert store._read_one("SELECT count(*) AS n FROM lineage_cad_names", ()) == names_before
        with store._lock:
            assert [dict(row) for row in store._connect().execute("SELECT * FROM archive_folders")] == archives_before
        assert store.publication_exports() == []
        assert store.publication_reservation_for_key("stop") is None
        stopped = False
        monkeypatch.setattr(service, "run_mesh_build", native_build)
        request = request.model_copy(update={"design": DesignConfig.model_validate({"formula": "OSSE", "L": 140, "a": 45})})
        result = service.export_wglink_sync(request, store, workspace, "next-edit")
        assert result["identity"]["baseEditVersion"] == (2 if saved else 1)
    finally:
        store.close()


def test_stopping_one_send_does_not_rewind_another_sends_prepared_identity(tmp_path, monkeypatch):
    from server.jobs.request_cancellation import ClientDisconnected

    store = CadLinkStore(tmp_path / "cadlink.db")
    other = CadLinkStore(store.db_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    saved = _saved(store, _design())
    edited = DesignConfig.model_validate({"formula": "OSSE", "L": 130, "a": 45})
    request = _request(saved, edited, "demo-horn")
    _install_fake_write(monkeypatch)
    native_build = service.run_mesh_build
    first_meshing = threading.Event()
    second_started = threading.Event()
    stop_first = threading.Event()
    second_prepared = threading.Event()
    native_commit = service._commit_design_for_export

    def commit(registry, *args):
        result = native_commit(registry, *args)
        if registry is other:
            second_prepared.set()
        return result

    async def mesh(*args, **kwargs):
        if "cancel_cb" in kwargs:
            first_meshing.set()
            assert stop_first.wait(3)
            kwargs["cancel_cb"]()
        return await native_build(*args, **kwargs)

    def cancel():
        if stop_first.is_set():
            raise ClientDisconnected()

    def second():
        second_started.set()
        return service.export_wglink_sync(request, other, workspace, "second")

    monkeypatch.setattr(service, "run_mesh_build", mesh)
    monkeypatch.setattr(service, "_commit_design_for_export", commit)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.export_wglink_sync, request, store, workspace, "first", cancel_cb=cancel)
            try:
                assert first_meshing.wait(3)
                next_send = pool.submit(second)
                assert second_started.wait(3)
                # B either prepares while A is meshing (the regression), or
                # waits on the same design lock until A's compensation ends.
                second_prepared.wait(0.1)
            finally:
                stop_first.set()
            with pytest.raises(ClientDisconnected):
                first.result(timeout=3)
            result = next_send.result(timeout=3)
        assert result["identity"]["baseEditVersion"] == 2
        assert len(store.publication_exports()) == 1
    finally:
        store.close()
        other.close()


@pytest.mark.parametrize("lock_kind", ["lineage_id", "design_id"])
def test_queued_send_observes_stop_before_preparing_design(tmp_path, monkeypatch, lock_kind):
    from server.jobs.request_cancellation import ClientDisconnected

    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    before = store.list_designs()
    waiting, stopped = threading.Event(), threading.Event()
    prepared = []
    original = service._commit_design_for_export

    def prepare(*args):
        prepared.append(True)
        return original(*args)

    def check():
        if stopped.is_set():
            raise ClientDisconnected()

    def send():
        return service.export_wglink_sync(_request(saved), store, tmp_path, "queued", cancel_cb=check)

    monkeypatch.setattr(service, "_commit_design_for_export", prepare)
    native_lock = store._export_build_lock
    identity = getattr(saved["identity"], lock_kind)
    lock = native_lock(identity)

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            waiting.set()
            return lock.acquire(*args, **kwargs)

        def release(self):
            lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_args):
            self.release()

    observed = ObservedLock()
    monkeypatch.setattr(store, "_export_build_lock", lambda key: observed if key == identity else native_lock(key))
    lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(send)
            try:
                assert waiting.wait(2)
                stopped.set()
                with pytest.raises(ClientDisconnected):
                    future.result(timeout=1)
                assert prepared == []
                assert store.list_designs() == before
            finally:
                lock.release()
    finally:
        store.close()
