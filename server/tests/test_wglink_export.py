"""Identity-bearing workspace bundle export contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from server.cadlink.identity import SaveIdentity, design_hash
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.design.textcfg import serialize
from server.exports import api


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


def _request(saved, design: DesignConfig | None = None) -> api.WgLinkExportRequest:
    identity = saved["identity"]
    return api.WgLinkExportRequest.model_validate({
        "design": (design or _design()).model_dump(mode="json"),
        "designRevision": 3,
        "baseName": "demo horn.cfg",
        "identity": {
            "designId": identity.design_id,
            "lineageId": identity.lineage_id,
            "baseEditVersion": identity.edit_version,
        },
    })


def test_wglink_export_writes_identity_hashes_and_retries_without_rebuilding(
    monkeypatch, tmp_path: Path,
) -> None:
    design = _design()
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, design)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes: list[object] = []

    def fake_write(geometry, output_path, *, identity, **_kwargs):
        writes.append(geometry)
        target = Path(output_path)
        target.mkdir()
        step_hash = "sha256:" + "a" * 64
        manifest = {
            "wglink_version": "1.0",
            "bundle": dict(identity.bundle or {}),
            "generator": dict(identity.generator or {}),
            "design": dict(identity.design or {}),
            "export": dict(identity.export or {}),
            "files": {"waveguide.step": {"sha256": step_hash, "size_bytes": 4}},
        }
        (target / "waveguide.step").write_bytes(b"STEP")
        manifest_path = target / "wglink.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
        return SimpleNamespace(path=target, manifest_path=manifest_path, manifest=manifest)

    monkeypatch.setattr("hornlab_mesher.write_wglink", fake_write)
    first = api._export_wglink_sync(_request(saved), store, workspace, "attempt-1")
    retry = api._export_wglink_sync(_request(saved), store, workspace, "attempt-1")
    second = api._export_wglink_sync(_request(saved), store, workspace, "attempt-2")
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
    retry_after_save = api._export_wglink_sync(
        _request(saved), store, workspace, "attempt-1"
    )

    assert retry == retry_after_save == first
    assert len(writes) == 2
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["bundlePath"] == str(workspace / "wglink" / "demo_horn.wglink")
    assert first["designHash"] == design_hash(design)
    assert first["geometryHash"].startswith("sha256:")
    assert first["artifactSha256"] == "sha256:" + "a" * 64
    manifest = json.loads((Path(first["bundlePath"]) / "wglink.json").read_text())
    assert manifest["bundle"]["id"] == second["bundleId"]
    assert manifest["export"] == {
        "domain": "full",
        "geometry_hash": first["geometryHash"],
        "id": second["exportId"],
        "open_throat": True,
        "parent_export_id": first["exportId"],
        "sequence": 2,
    }


def test_wglink_export_requires_a_saved_current_design(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = api.WgLinkExportRequest.model_validate({
        "design": _design().model_dump(mode="json"),
        "designRevision": 0,
        "identity": None,
    })
    with pytest.raises(HTTPException, match="never been saved") as missing:
        api._export_wglink_sync(request, store, workspace, "missing")
    assert missing.value.status_code == 409

    saved = _saved(store, _design())
    changed = DesignConfig.model_validate({"formula": "OSSE", "L": 121, "a": 45})
    with pytest.raises(HTTPException, match="unsaved changes") as unsaved:
        api._export_wglink_sync(_request(saved, changed), store, workspace, "changed")
    assert unsaved.value.status_code == 409
