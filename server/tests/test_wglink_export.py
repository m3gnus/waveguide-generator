"""Identity-bearing workspace bundle export contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from hornlab_mesher import read_wglink
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


def _design_count(store: CadLinkStore) -> int:
    row = store._read_one("SELECT COUNT(*) AS count FROM designs", ())
    assert row is not None
    return int(row["count"])


def _request(
    saved, design: DesignConfig | None = None, base_name: str = "demo horn.cfg"
) -> api.WgLinkExportRequest:
    identity = saved["identity"]
    return api.WgLinkExportRequest.model_validate({
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
        step_hash = "sha256:" + "a" * 64
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


def test_wglink_export_writes_identity_hashes_and_retries_without_rebuilding(
    monkeypatch, tmp_path: Path,
) -> None:
    design = _design()
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, design)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes = _install_fake_write(monkeypatch)

    first = api._export_wglink_sync(_request(saved), store, workspace, "attempt-1")
    retry = api._export_wglink_sync(_request(saved), store, workspace, "attempt-1")
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()
    retry_from_other_workspace = api._export_wglink_sync(
        _request(saved), store, other_workspace, "attempt-1"
    )
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
    with pytest.raises(HTTPException, match="replaced or changed") as stale_retry:
        api._export_wglink_sync(_request(saved), store, workspace, "attempt-1")

    assert stale_retry.value.status_code == 409
    assert retry == retry_from_other_workspace == first
    assert len(writes) == 2
    source = writes[0]["kwargs"]["interface_sources"][0]
    assert source.id == "source-hf"
    assert source.role == "HF"
    assert source.default_drive_channel_id == "drive-hf"
    assert source.suggested_resolution_mm == pytest.approx(4.0)
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["bundlePath"] == str(workspace / "wglink" / "demo_horn.wglink")
    assert first["designHash"] == design_hash(design)
    assert first["geometryHash"].startswith("sha256:")
    assert first["artifactSha256"] == "sha256:" + "a" * 64
    manifest = json.loads((Path(first["bundlePath"]) / "wglink.json").read_text())
    assert read_wglink(first["bundlePath"], verify_checksums=False) == manifest
    assert manifest["bundle"]["id"] == second["bundleId"]
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
        "id": second["exportId"],
        "open_throat": True,
        "parent_export_id": first["exportId"],
        "sequence": 2,
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

    never_saved = api.WgLinkExportRequest.model_validate({
        "design": _design().model_dump(mode="json"),
        "designRevision": 0,
        "baseName": "demo horn.cfg",
        "identity": None,
    })
    minted = api._export_wglink_sync(never_saved, store, workspace, "unsaved")
    assert minted["sequence"] == 1
    assert minted["identity"]["baseEditVersion"] == 1
    assert minted["designHash"] == design_hash(_design())
    head = store.get_design(minted["identity"]["designId"])
    assert head is not None
    assert head["design_hash"] == design_hash(_design())
    assert head["snapshot_text"]

    saved = _saved(store, _design())
    changed = DesignConfig.model_validate({"formula": "OSSE", "L": 121, "a": 45})
    exported = api._export_wglink_sync(
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

    with pytest.raises(RuntimeError, match="simulated geometry failure"):
        api._export_wglink_sync(request, store, workspace, "failed-attempt")

    original_identity = saved["identity"]
    head_after_failure = store.get_design(original_identity.design_id)
    assert head_after_failure is not None
    assert head_after_failure["edit_version"] == original_identity.edit_version + 1
    assert head_after_failure["design_hash"] == design_hash(edited)
    assert _design_count(store) == 1

    retried = api._export_wglink_sync(request, store, workspace, "retry-attempt")

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

    first = api._export_wglink_sync(request, store, workspace, "same-attempt")
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

    retry = api._export_wglink_sync(request, store, workspace, "same-attempt")

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

    first = api._export_wglink_sync(request, store, workspace, "same-attempt")
    retry = api._export_wglink_sync(request, store, workspace, "same-attempt")

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

    first = api._export_wglink_sync(stale_request, store, workspace, "first-send")
    second = api._export_wglink_sync(stale_request, store, workspace, "second-send")

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

    exported = api._export_wglink_sync(
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


def test_parameter_namespace_and_bundle_folder_survive_a_rename(
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

    first = api._export_wglink_sync(_request(saved), store, workspace, "first-send")
    renamed = api._export_wglink_sync(
        _request(saved, base_name="tritonia.cfg"), store, workspace, "renamed-send"
    )

    assert [write["kwargs"]["instance_slug"] for write in writes] == [
        "demo_horn",
        "demo_horn",
    ]
    assert _parameter_names(first) == _parameter_names(renamed) == [
        "wg_demo_horn_throat_dia"
    ]
    assert first["bundlePath"] == renamed["bundlePath"] == str(
        workspace / "wglink" / "demo_horn.wglink"
    )
    assert list((workspace / "wglink").iterdir()) == [Path(str(first["bundlePath"]))]
    # Only the namespace is frozen; the readable name still follows the file.
    manifest = json.loads((Path(str(renamed["bundlePath"])) / "wglink.json").read_text())
    assert manifest["design"]["name"] == "tritonia"
    names = store.get_lineage_cad_names(str(renamed["identity"]["lineageId"]))
    assert names is not None
    assert names["parameter_slug"] == "demo_horn"
    assert names["bundle_stem"] == "demo_horn"


def test_parameter_namespace_survives_an_identity_fork(
    monkeypatch, tmp_path: Path,
) -> None:
    """A fork is one design to the user, and to the linked Fusion document."""

    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _saved(store, _design())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes = _install_fake_write(monkeypatch)

    original = api._export_wglink_sync(_request(saved), store, workspace, "original")
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

    forked = api._export_wglink_sync(
        _request(saved, base_name="tritonia.cfg"), store, workspace, "forked"
    )

    assert forked["identity"]["designId"] != original["identity"]["designId"]
    assert forked["identity"]["lineageId"] == original["identity"]["lineageId"]
    assert [write["kwargs"]["instance_slug"] for write in writes] == [
        "demo_horn",
        "demo_horn",
    ]
    assert _parameter_names(forked) == ["wg_demo_horn_throat_dia"]
    # A fork is a second design in the lineage, so it cannot share the folder
    # the first one owns -- but it keeps the readable stem the lineage earned.
    assert forked["bundlePath"] != original["bundlePath"]
    assert Path(str(forked["bundlePath"])).name.startswith("demo_horn-")


def test_a_fresh_lineage_mints_its_namespace_from_the_current_name(
    monkeypatch, tmp_path: Path,
) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writes = _install_fake_write(monkeypatch)

    first = api._export_wglink_sync(
        _request(_saved(store, _design()), base_name="demo horn.cfg"),
        store,
        workspace,
        "first-lineage",
    )
    second = api._export_wglink_sync(
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

    first = api._export_wglink_sync(_request(saved), store, workspace, "before")
    with store._lock, store._transaction() as conn:
        conn.execute("DELETE FROM lineage_cad_names")

    renamed = api._export_wglink_sync(
        _request(saved, base_name="tritonia.cfg"), store, workspace, "after"
    )

    assert _parameter_names(renamed) == ["wg_demo_horn_throat_dia"]
    assert renamed["bundlePath"] == first["bundlePath"]
    names = store.get_lineage_cad_names(str(renamed["identity"]["lineageId"]))
    assert names is not None
    assert names["parameter_slug"] == "demo_horn"


def test_wglink_export_forks_a_stale_identity_and_adopts_an_unknown_one(
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

    # The head advanced past this editor's token: the store's CAS forks rather
    # than overwriting someone else's work, and the export follows the fork.
    stale = api._export_wglink_sync(_request(saved), store, workspace, "stale")
    assert stale["identity"]["designId"] != original_identity.design_id
    assert stale["identity"]["lineageId"] == original_identity.lineage_id
    assert stale["identity"]["baseEditVersion"] == 1

    unknown = _request(saved, base_name="adopted horn.cfg")
    unknown.identity = SaveIdentity.model_validate({
        "designId": "wgd_01K00000000000000000000099",
        "lineageId": original_identity.lineage_id,
        "baseEditVersion": original_identity.edit_version,
    })
    adopted = api._export_wglink_sync(unknown, store, workspace, "unknown")
    assert adopted["identity"] == {
        "designId": "wgd_01K00000000000000000000099",
        "lineageId": original_identity.lineage_id,
        "baseEditVersion": original_identity.edit_version + 1,
    }


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


def test_bundle_name_rejects_casefold_and_disambiguates_an_owned_name(
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
    destination = api._bundle_destination(
        root, api._base_name("\N{COLLISION SYMBOL}"), "wgd_requested"
    )
    assert destination.parent == root
    assert destination.name.startswith("waveguide-")
    assert destination.name.endswith(".wglink")
    assert destination != existing

    destination.mkdir()
    (destination / "wglink.json").write_text(
        json.dumps({"design": {"id": "wgd_requested"}})
    )
    assert (
        api._bundle_destination(root, "waveguide", "wgd_requested")
        == destination
    )


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
            return api._export_wglink_sync(_request(saved), store, workspace, key)
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
        api._export_wglink_sync(_request(saved), store, workspace, "stale-workspace")

    assert caught.value.status_code == 409
    assert not workspace.exists()
