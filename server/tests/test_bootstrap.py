"""Repository-local environment bootstrap invariants."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def _load_bootstrap() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wg2_bootstrap", ROOT / "scripts" / "bootstrap.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validation_checks_exact_installed_git_commits(tmp_path, monkeypatch) -> None:
    bootstrap = _load_bootstrap()
    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    fingerprint = bootstrap._fingerprint()
    (environment / bootstrap.STAMP_NAME).write_text(
        json.dumps({"fingerprint": fingerprint}), encoding="utf-8"
    )
    commands: list[list[str]] = []

    def run(command: list[str], *, quiet: bool = False):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap, "_run", run)
    assert bootstrap._validate(environment, fingerprint) == (True, "ready")
    package_probe = commands[0][-1]
    for name, sha in bootstrap._locked_git_commits().items():
        assert name in package_probe
        assert sha in package_probe
    assert "direct_url.json" in package_probe
    assert "commit_id" in package_probe
    assert "requested_revision" in package_probe


def test_bootstrap_force_reinstalls_git_pins_after_manifest_change(tmp_path, monkeypatch) -> None:
    bootstrap = _load_bootstrap()
    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    validations = iter(((False, "the dependency manifests changed"), (True, "ready")))
    commands: list[list[str]] = []

    monkeypatch.setattr(bootstrap, "_require_supported_python", lambda: None)
    monkeypatch.setattr(bootstrap, "_validate", lambda *_args: next(validations))
    monkeypatch.setattr(bootstrap, "_write_stamp", lambda *_args: None)
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )

    bootstrap.bootstrap(environment)

    pin_installs = [
        command for command in commands if str(ROOT / "server" / "requirements-pins.txt") in command
    ]
    forced_pin_installs = [command for command in pin_installs if "--force-reinstall" in command]
    assert len(forced_pin_installs) == 1
    assert "--no-deps" in forced_pin_installs[0]
