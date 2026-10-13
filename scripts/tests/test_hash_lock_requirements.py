from pathlib import Path
from importlib.metadata import PackageNotFoundError, distribution
from types import SimpleNamespace

from packaging.markers import default_environment
from packaging.requirements import Requirement
import pytest

from scripts import bootstrap, build_bundle, hash_lock_requirements as hashing


def fixture(root: Path, runtime: str = "example==1.0\n") -> None:
    server = root / "server"
    server.mkdir()
    lock = "example==1.0\npytest==9.0.3\n"
    hashes = {"example==1.0": ["a" * 64], "pytest==9.0.3": ["b" * 64]}
    (server / "requirements-lock.txt").write_text(lock)
    (server / "requirements-runtime.txt").write_text(runtime)
    (server / "requirements-hashed.txt").write_text(hashing.render(lock, hashes))
    (server / "requirements-runtime-hashed.txt").write_text(hashing.render(hashing.runtime_lock(lock), {"example==1.0": ["a" * 64]}))


def test_committed_hash_locks_match_exact_pins() -> None:
    hashing.check()


def test_runtime_lock_excludes_only_development_toolchain(tmp_path: Path) -> None:
    fixture(tmp_path)
    hashing.check(tmp_path)
    assert "pytest==" not in (tmp_path / "server/requirements-runtime-hashed.txt").read_text()


def test_changed_direct_runtime_requirement_is_not_silently_ignored(tmp_path: Path) -> None:
    fixture(tmp_path, "example==2.0\n")
    with pytest.raises(ValueError, match="does not match"):
        hashing.check(tmp_path)


def test_missing_hash_and_extra_unpinned_requirement_are_refused(tmp_path: Path) -> None:
    fixture(tmp_path)
    path = tmp_path / "server/requirements-hashed.txt"
    path.write_text(path.read_text().replace("a" * 64, "invalid"))
    with pytest.raises(ValueError, match="hash"):
        hashing.check(tmp_path)
    with pytest.raises(ValueError, match="exact distribution"):
        hashing.pins("example>=1\n")


@pytest.mark.parametrize("system,platform", [("Windows", "win32"), ("Linux", "linux"), ("Darwin", "darwin")])
def test_available_metadata_runtime_closure_is_pinned_for_platform(system: str, platform: str) -> None:
    environment = {**default_environment(), "platform_system": system, "sys_platform": platform, "extra": ""}
    locked = [Requirement(pin) for pin in hashing.pins(hashing.runtime_lock(hashing.LOCK.read_text()))]
    active = {req.name.lower().replace("_", "-"): req for req in locked if req.marker is None or req.marker.evaluate(environment)}
    extras = {Requirement(pin).name.lower().replace("_", "-"): Requirement(pin).extras for pin in hashing.pins((hashing.ROOT / "server/requirements-runtime.txt").read_text())}
    checked = set()
    for name in active:
        try:
            installed = distribution(name)
        except PackageNotFoundError:
            # Platform-only distributions unavailable on this host are checked
            # by the same test in that platform's installed CI environment.
            continue
        for raw in installed.requires or ():
            req = Requirement(raw)
            if req.marker and not any(req.marker.evaluate({**environment, "extra": extra}) for extra in {"", *extras.get(name, ())}):
                continue
            dependency = req.name.lower().replace("_", "-")
            assert dependency in active, (system, name, raw)
            assert all(req.specifier.contains(str(spec.version)) for spec in active[dependency].specifier), (name, raw)
        checked.add(name)
    assert "click" in checked


def test_force_does_not_replace_hashed_closure_with_unhashed_dev_artifacts(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    environment = tmp_path / "venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(bootstrap, "REPO_ROOT", checkout)
    monkeypatch.setattr(bootstrap, "_require_supported_python", lambda: None)
    monkeypatch.setattr(bootstrap, "_fingerprint", lambda: "proof")
    monkeypatch.setattr(bootstrap, "_validate", lambda *args: (len(args) > 2, "forced"))
    monkeypatch.setattr(bootstrap, "_write_stamp", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "_install_cli_entrypoint", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "_remove_undeclared_distributions", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "_warn_when_gui_unavailable", lambda *_args: None)
    commands = []
    monkeypatch.setattr(bootstrap, "_run", lambda command: commands.append(command) or SimpleNamespace(returncode=0))
    bootstrap._bootstrap_locked(environment, force=True)
    hashed = next(command for command in commands if "--require-hashes" in command)
    assert "--force-reinstall" in hashed
    vcs = next(command for command in commands if "--no-deps" in command)
    assert "--require-hashes" not in vcs and "-c" not in vcs
    development = next(command for command in commands if command[-1].endswith("requirements-dev.txt"))
    assert "--force-reinstall" not in development
    assert command_constraint(development).name == "requirements-lock.txt"


def command_constraint(command: list[str]) -> Path:
    return Path(command[command.index("-c") + 1])


def test_hash_refresh_invalidates_runtime_recipe(tmp_path: Path) -> None:
    for relative in (build_bundle.WINDOWS_NATIVE_SOURCE, build_bundle.WINDOWS_NATIVE_HOOK_SOURCE, "server/requirements-runtime-hashed.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("initial")
    initial = build_bundle.runtime_recipe(tmp_path)
    (tmp_path / "server/requirements-runtime-hashed.txt").write_text("reviewed new hashes")
    assert initial != build_bundle.runtime_recipe(tmp_path)


def test_force_inventory_accepts_generated_hash_continuations() -> None:
    # Inventory must parse generated pins, never treat --hash as a package.
    names = bootstrap._declared_distribution_names()
    assert "numpy" in names and "colorama" in names and "pytest" in names
