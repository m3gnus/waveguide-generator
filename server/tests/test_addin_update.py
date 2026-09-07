"""WGLink is reconciled with this build's pin, and three installs are not."""

from __future__ import annotations

import json
from pathlib import Path

from server.cadlink import addin_update


def _wg_root(tmp_path: Path, commit: str) -> Path:
    root = tmp_path / "wg"
    (root / "integrations" / "wglink").mkdir(parents=True)
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


def test_an_add_in_with_no_marker_is_left_alone(tmp_path: Path) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit=None, root=None)

    assert addin_update.refresh_wglink(root=root, addins_dir=addins)[0] == "external"


def test_nothing_is_installed_when_fusion_has_no_add_in(tmp_path: Path) -> None:
    """WG reconciles an add-in the user has; it does not install one they never
    asked for."""

    root = _wg_root(tmp_path, "a" * 40)
    assert addin_update.refresh_wglink(root=root, addins_dir=tmp_path / "AddIns")[0] == "absent"


def test_a_stale_managed_add_in_is_updated_to_the_pin(tmp_path: Path, monkeypatch) -> None:
    root = _wg_root(tmp_path, "a" * 40)
    addins = tmp_path / "AddIns"
    _installed(addins, commit="b" * 40, root=root)
    calls: list[dict[str, object]] = []

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
