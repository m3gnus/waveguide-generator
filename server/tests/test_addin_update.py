"""WGLink is always the add-in this build ships, except two installs that are not WG's to change."""

from __future__ import annotations

import json
from pathlib import Path

from server.cadlink import addin_update


def _wg_root(tmp_path: Path, commit: str) -> Path:
    root = tmp_path / "wg"
    (root / "integrations" / "wglink").mkdir(parents=True)
    (root / "shared").mkdir(parents=True)
    (root / "shared" / "version.json").write_text(
        json.dumps({"version": "0.3.2"}), encoding="utf-8"
    )
    (root / "integrations" / "wglink" / "source.json").write_text(
        json.dumps({
            "schema": 1,
            "repository": "https://example.invalid/addin.git",
            "commit": commit,
            "license": "AGPL-3.0-or-later",
            "addinVersion": "0.1.1",
        }),
        encoding="utf-8",
    )
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    real = Path(addin_update.app_root()) / "scripts" / "install_wglink.py"
    (root / "scripts" / "install_wglink.py").write_text(
        real.read_text(encoding="utf-8"), encoding="utf-8"
    )
    builder = Path(addin_update.app_root()) / "scripts" / "build_wglink_package.py"
    (root / "scripts" / "build_wglink_package.py").write_text(
        builder.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return root


def _installed(addins: Path, *, commit: str | None, root: Path | None, dev: bool = False) -> Path:
    target = addins / "WGLink"
    target.mkdir(parents=True)
    (target / "WGLink.py").write_text("# add-in\n", encoding="utf-8")
    if commit is not None:
        (target / "wglink_install.json").write_text(
            json.dumps({
                "schema": 1,
                "managedBy": "waveguide-generator",
                "waveguideGeneratorRoot": str(root),
                "waveguideGeneratorVersion": "0.3.1",
                "sourceCommit": commit,
                "addinVersion": "0.1.1",
            }),
            encoding="utf-8",
        )
    if dev:
        (target / "wglink_dev.json").write_text('{"sourceCommit": "local"}', encoding="utf-8")
    return target


def test_an_add_in_already_at_the_pin_is_left_alone(tmp_path: Path) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit="a" * 40, root=root)

    assert addin_update.refresh_wglink(root=root, addins_dir=addins)[0] == "current"


def test_a_developer_sync_is_never_overwritten(tmp_path: Path) -> None:
    """The marker still says WG-managed, because a sync replaces code and not
    identity. Reading only that would make the dev loop unusable: every start
    of WG would put the pinned build back under whoever was editing it."""

    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit="b" * 40, root=root, dev=True)

    verdict, detail = addin_update.refresh_wglink(root=root, addins_dir=addins)
    assert verdict == "developer"
    assert "developer sync" in detail


def test_an_add_in_managed_by_another_wg_installation_is_left_alone(tmp_path: Path) -> None:
    """Two Waveguide Generators fighting over one add-in is the failure the
    marker exists to prevent, so a stale commit is not licence to take it."""

    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit="b" * 40, root=tmp_path / "another-wg")

    assert addin_update.refresh_wglink(root=root, addins_dir=addins)[0] == "external"


def _recording_installer(monkeypatch, addins: Path, calls: list[dict[str, object]]) -> None:
    real = addin_update._installer

    def recording(root_path: Path):
        module = real(root_path)
        module.default_addins_dir = lambda _platform: addins

        def install(**kwargs):
            calls.append(kwargs)
            target = addins / "WGLink"
            target.mkdir(parents=True, exist_ok=True)
            return "installed", target

        module.install = install
        return module

    monkeypatch.setattr(addin_update, "_installer", recording)


def test_an_add_in_no_waveguide_generator_manages_is_replaced(
    tmp_path: Path, monkeypatch
) -> None:
    """Decision 4: WG always uses the add-in it ships.

    A WGLink with no WG marker was copied in by hand, before WG managed it; it
    is too old for this WG to talk to. Leaving it would leave the user with a
    refusal and no way out, so it is replaced from this build's package.
    """

    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit=None, root=None)
    calls: list[dict[str, object]] = []
    _recording_installer(monkeypatch, addins, calls)

    verdict, detail = addin_update.refresh_wglink(root=root, addins_dir=addins)

    assert verdict == "replaced"
    assert "restart Fusion" in detail
    assert calls and calls[0]["replace_external"] is True


def test_an_absent_add_in_stays_absent_only_when_asked(tmp_path: Path) -> None:
    root = _wg_root(tmp_path, "a" * 40)

    assert addin_update.refresh_wglink(
        root=root, addins_dir=tmp_path / "AddIns", install_absent=False
    )[0] == "absent"


def test_startup_installs_an_absent_add_in_where_fusion_is_installed(
    tmp_path: Path, monkeypatch
) -> None:
    """Fusion need not be running: its API folder says it is installed."""

    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "Autodesk Fusion" / "API" / "AddIns"
    addins.parent.mkdir(parents=True)
    archive = tmp_path / "wglink.zip"
    calls: list[dict[str, object]] = []
    _recording_installer(monkeypatch, addins, calls)
    monkeypatch.setattr(addin_update, "fusion_process_running", lambda: False)
    monkeypatch.setattr(addin_update, "_verified_shipped_package", lambda *_args: (archive, None))

    verdict, _detail = addin_update.refresh_wglink(root=root)

    assert verdict == "installed"
    assert calls and calls[0]["archive_path"] == archive


def test_the_startup_refresh_can_be_turned_off_and_reports_what_it_did(
    tmp_path: Path, monkeypatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(addin_update, "refresh_wglink", lambda: seen.append("ran") or ("updated", "x"))

    monkeypatch.setenv("WG2_WGLINK_REFRESH", "0")
    assert addin_update.refresh_and_log()[0] == "disabled"
    assert seen == []
    assert addin_update.last_refresh() == {"verdict": "disabled", "detail": "WG2_WGLINK_REFRESH=0"}

    monkeypatch.setenv("WG2_WGLINK_REFRESH", "1")
    assert addin_update.refresh_and_log() == ("updated", "x")
    assert addin_update.last_refresh() == {"verdict": "updated", "detail": "x"}


def test_startup_recovers_a_managed_target_after_a_crash_moved_it_to_backup(
    tmp_path: Path,
) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    target = _installed(addins, commit="a" * 40, root=root)
    workspace = addins / ".WGLink-install-recovery"
    workspace.mkdir()
    previous = workspace / "previous"
    target.rename(previous)
    (previous / "wglink_runtime.json").write_text(
        json.dumps({
            "schema": 1,
            "root": str(root / "runtime"),
            "python": str(addin_update.sys.executable),
        }),
        encoding="utf-8",
    )
    marker = json.loads((previous / addin_update.INSTALL_MARKER).read_text(encoding="utf-8"))
    installer = addin_update._installer(root)
    (addins / ".WGLink-install-transaction.json").write_text(
        json.dumps({
            "schema": 1,
            "managedBy": "waveguide-generator",
            "waveguideGeneratorRoot": str(root.resolve()),
            "workspace": workspace.name,
            "hadPrevious": True,
            "replaceExternal": False,
            "expectedMarker": marker,
            "expectedFiles": installer._file_inventory(previous),
            "phase": "previous-moved",
        }),
        encoding="utf-8",
    )

    verdict, detail = addin_update.refresh_wglink(root=root, addins_dir=addins)

    assert verdict == "current"
    assert "pinned" in detail
    assert (target / "WGLink.py").is_file()
    assert not (addins / ".WGLink-install-transaction.json").exists()
    assert not workspace.exists()


def test_an_explicit_first_install_uses_only_the_verified_shipped_package(
    tmp_path: Path, monkeypatch
) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    archive = tmp_path / "wglink.zip"
    calls: list[dict[str, object]] = []
    real = addin_update._installer

    def recording(root_path: Path):
        module = real(root_path)

        def install(**kwargs):
            calls.append(kwargs)
            target = addins / "WGLink"
            target.mkdir(parents=True)
            (target / "WGLink.py").write_text("# installed\n", encoding="utf-8")
            return "installed", target

        module.install = install
        return module

    monkeypatch.setattr(addin_update, "_installer", recording)
    monkeypatch.setattr(
        addin_update,
        "_verified_shipped_package",
        lambda *_args: (archive, None),
    )

    verdict, detail = addin_update.refresh_wglink(
        root=root, addins_dir=addins, install_absent=True
    )

    assert verdict == "installed"
    assert "restart Fusion" in detail
    assert calls and calls[0]["archive_path"] == archive


def test_an_implicit_first_install_requires_fusion_to_be_installed(tmp_path: Path, monkeypatch) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    # No Fusion folder for this user at all, and no Fusion process.
    addins = tmp_path / "no-fusion" / "API" / "AddIns"
    real = addin_update._installer

    def recording(root_path: Path):
        module = real(root_path)
        module.default_addins_dir = lambda _platform: addins
        return module

    monkeypatch.setattr(addin_update, "_installer", recording)
    monkeypatch.setattr(addin_update, "fusion_process_running", lambda: False)

    verdict, detail = addin_update.refresh_wglink(root=root, install_absent=True)

    assert verdict == "not-detected"
    assert "not installed" in detail


def test_a_verified_package_failure_is_reported_without_installing(tmp_path: Path, monkeypatch) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    calls: list[object] = []
    real = addin_update._installer

    def recording(root_path: Path):
        module = real(root_path)
        module.install = lambda **kwargs: calls.append(kwargs)
        return module

    monkeypatch.setattr(addin_update, "_installer", recording)
    monkeypatch.setattr(
        addin_update,
        "_verified_shipped_package",
        lambda *_args: (None, "the bundled WGLink package failed verification: bad hash"),
    )

    verdict, detail = addin_update.refresh_wglink(
        root=root, addins_dir=addins, install_absent=True
    )

    assert verdict == "unavailable"
    assert "bad hash" in detail
    assert calls == []


def test_a_stale_managed_add_in_is_updated_to_the_pin(tmp_path: Path, monkeypatch) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit="b" * 40, root=root)
    calls: list[dict[str, object]] = []
    monkeypatch.delenv("WG2_BUNDLE", raising=False)

    real = addin_update._installer

    def recording(root_path: Path):
        module = real(root_path)
        def install(**kwargs):
            calls.append(kwargs)
            return "installed", addins / "WGLink"
        module.install = install
        return module

    monkeypatch.setattr(addin_update, "_installer", recording)

    verdict, detail = addin_update.refresh_wglink(root=root, addins_dir=addins)
    assert verdict == "updated"
    assert "restart Fusion" in detail
    assert calls and calls[0]["root"] == root
    assert calls[0]["offline_only"] is False


def test_a_bundled_managed_update_never_fetches_when_its_package_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit="b" * 40, root=root)
    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path / "data"))

    verdict, detail = addin_update.refresh_wglink(root=root, addins_dir=addins)

    assert verdict == "failed"
    assert "does not contain its WGLink package" in detail


def test_a_bundled_managed_update_refuses_a_tampered_shipped_package(
    tmp_path: Path, monkeypatch
) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit="b" * 40, root=root)
    package = root / "integrations" / "wglink" / "packages" / f"wglink-0.3.2-{'a' * 40}.zip"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"not a WGLink zip")
    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path / "data"))

    verdict, detail = addin_update.refresh_wglink(root=root, addins_dir=addins)

    assert verdict == "failed"
    assert "Bundled WGLink package failed verification" in detail


def test_an_installer_failure_is_reported_and_never_raised(tmp_path: Path, monkeypatch) -> None:
    """Startup runs this. A machine that cannot update its add-in still has to
    be a machine that starts."""

    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit="b" * 40, root=root)
    real = addin_update._installer

    def exploding(root_path: Path):
        module = real(root_path)
        def install(**_kwargs):
            raise RuntimeError("no network")
        module.install = install
        return module

    monkeypatch.setattr(addin_update, "_installer", exploding)

    verdict, detail = addin_update.refresh_wglink(root=root, addins_dir=addins)
    assert verdict == "failed"
    assert "no network" in detail


def test_create_app_reconciles_the_add_in_at_boot_and_drains_it_at_shutdown(
    tmp_path: Path,
) -> None:
    """The wiring, not just the decision.

    Every verdict below is reachable only if something actually calls this, and
    the reconciliation is the one part of the round trip with no user action
    behind it. Draining rather than cancelling matters here more than for a
    warmup: this task can be mid-install, and abandoning it would leave a
    staging directory beside the user's add-in.
    """

    from server.app import create_app

    application = create_app(data_dir=tmp_path)
    assert "start_addin_refresh" in {
        handler.__name__ for handler in application.router.on_startup
    }
    assert "shutdown_addin_refresh" in {
        handler.__name__ for handler in application.router.on_shutdown
    }


def test_the_reconciliation_runs_off_the_startup_thread(monkeypatch) -> None:
    """Startup must not wait on file work, however short it usually is."""

    import asyncio

    seen: list[str] = []
    monkeypatch.setattr(
        addin_update, "refresh_and_log", lambda: seen.append("ran") or ("current", "")
    )

    async def drive() -> None:
        await addin_update.start_addin_refresh()
        assert addin_update.addin_refresh.task is not None
        await addin_update.shutdown_addin_refresh()

    asyncio.run(drive())
    assert seen == ["ran"]
