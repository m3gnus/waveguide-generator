"""Identity-bearing workspace bundle export contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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


def test_wglink_export_conflict_instructions_cover_stale_and_unknown_identity(
    tmp_path: Path,
) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    saved = _saved(store, _design())
    original_identity = saved["identity"]
    store.save(
        requested=SaveIdentity.model_validate({
            "designId": original_identity.design_id,
            "lineageId": original_identity.lineage_id,
            "baseEditVersion": original_identity.edit_version,
        }),
        design_hash=design_hash(_design()),
        filename="demo-horn.cfg",
        snapshot_builder=lambda identity: serialize(_design(), cadlink=identity),
    )

    stale = _request(saved)
    stale.identity = SaveIdentity.model_validate({
        "designId": saved["identity"].design_id,
        "lineageId": saved["identity"].lineage_id,
        "baseEditVersion": saved["identity"].edit_version,
    })
    with pytest.raises(HTTPException, match="Save again before sending it to CAD") as caught:
        api._export_wglink_sync(stale, store, workspace, "stale")
    assert caught.value.status_code == 409

    unknown = _request(saved)
    unknown.identity = SaveIdentity.model_validate({
        "designId": "wgd_01K00000000000000000000099",
        "lineageId": saved["identity"].lineage_id,
        "baseEditVersion": saved["identity"].edit_version,
    })
    with pytest.raises(HTTPException, match="Save the design before sending it to CAD") as caught:
        api._export_wglink_sync(unknown, store, workspace, "unknown")
    assert caught.value.status_code == 409


def test_replace_bundle_uses_atomic_exchange_when_live_bundle_exists(
    monkeypatch, tmp_path: Path,
) -> None:
    staged = tmp_path / "staged.wglink"
    destination = tmp_path / "live.wglink"
    staged.mkdir()
    destination.mkdir()
    (staged / "value").write_text("new")
    (destination / "value").write_text("old")
    exchanged: list[tuple[Path, Path]] = []
    real_exchange = api._exchange_directories

    def observed_exchange(first: Path, second: Path) -> None:
        assert first.is_dir()
        assert second.is_dir()
        exchanged.append((first, second))
        real_exchange(first, second)

    monkeypatch.setattr(api, "_exchange_directories", observed_exchange)
    api._replace_bundle(staged, destination)

    assert exchanged == [(staged, destination)]
    assert (destination / "value").read_text() == "new"
    assert not staged.exists()


def test_exchange_directories_windows_fallback_swaps_and_restores(
    monkeypatch, tmp_path: Path,
) -> None:
    """The non-POSIX path must have the same postcondition as the swap.

    CI's first windows-latest run died on ctypes.CDLL(None) -- the POSIX libc
    handle -- inside this function. The fallback swaps via an aside rename and
    leaves the OLD bundle at ``first`` for the caller to remove, and restores
    the live bundle if the second rename fails.
    """
    monkeypatch.setattr(api.os, "name", "nt")
    first = tmp_path / "staged.wglink"
    second = tmp_path / "live.wglink"
    first.mkdir()
    second.mkdir()
    (first / "value").write_text("new")
    (second / "value").write_text("old")

    api._exchange_directories(first, second)
    assert (second / "value").read_text() == "new"
    assert (first / "value").read_text() == "old"

    # Failure between the renames restores the live bundle.
    real_rename = api.os.rename
    calls = {"n": 0}

    def failing_rename(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated failure installing the staged bundle")
        real_rename(src, dst)

    monkeypatch.setattr(api.os, "rename", failing_rename)
    with pytest.raises(OSError, match="simulated failure"):
        api._exchange_directories(first, second)
    assert (second / "value").read_text() == "new"
    assert first.exists()


def test_bundle_name_rejects_casefold_and_sanitized_unicode_collisions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wglink"
    root.mkdir()
    existing = root / "Horn.wglink"
    existing.mkdir()
    (existing / "wglink.json").write_text(json.dumps({"design": {"id": "wgd_other"}}))

    with pytest.raises(ValueError, match="portable name"):
        api._bundle_destination(root, "horn", "wgd_requested")

    existing.rename(root / "waveguide.wglink")
    assert api._base_name("\N{ANGSTROM SIGN}") == "waveguide"
    assert api._base_name("\N{COLLISION SYMBOL}") == "waveguide"
    with pytest.raises(ValueError, match="another design"):
        api._bundle_destination(root, api._base_name("\N{COLLISION SYMBOL}"), "wgd_requested")


def test_bundle_base_name_cannot_escape_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "wglink"
    root.mkdir(parents=True)
    name = api._base_name("../../outside.cfg")
    destination = api._bundle_destination(root, name, "wgd_requested")

    assert name == "outside"
    assert destination == root / "outside.wglink"
    assert root in destination.parents


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
        step_hash = "sha256:" + "b" * 64
        manifest = {
            "design": dict(identity.design or {}),
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
            return api._export_wglink_sync(_request(saved), store, workspace, key)
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(export, [(first_saved, "first"), (second_saved, "second")]))

    successes = [result for result in results if isinstance(result, dict)]
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert "another design" in str(conflicts[0].detail)


def test_wglink_export_rejects_a_stale_workspace_path(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    workspace = tmp_path / "removed-workspace"

    with pytest.raises(HTTPException, match="workspace folder is unavailable") as caught:
        api._export_wglink_sync(_request(saved), store, workspace, "stale-workspace")

    assert caught.value.status_code == 409
    assert not workspace.exists()
