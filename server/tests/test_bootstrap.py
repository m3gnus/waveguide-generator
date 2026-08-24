"""Repository-local environment bootstrap invariants."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from contextlib import nullcontext
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
    bootstrap._install_cli_entrypoint(environment)
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


def _ready_environment(bootstrap, tmp_path) -> Path:
    """A venv-shaped directory complete enough for the evidence fast path."""

    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"not really an interpreter")
    site_packages = (
        environment / "Lib" / "site-packages"
        if os.name == "nt"
        else environment / "lib" / "python3.13" / "site-packages"
    )
    site_packages.mkdir(parents=True)
    (site_packages / "fastapi-0.1.dist-info").mkdir()
    (site_packages / "uvicorn-0.2.dist-info").mkdir()
    for name, sha in bootstrap._locked_git_commits().items():
        dist_info = site_packages / f"{name.replace('-', '_')}-0.1.0.dist-info"
        dist_info.mkdir()
        (dist_info / "direct_url.json").write_text(
            json.dumps(
                {
                    "url": f"https://example.invalid/{name}.git",
                    "vcs_info": {
                        "vcs": "git",
                        "commit_id": sha,
                        "requested_revision": sha,
                    },
                }
            ),
            encoding="utf-8",
        )
    bootstrap._install_cli_entrypoint(environment)
    return environment


def test_an_unchanged_environment_validates_without_starting_a_subprocess(
    tmp_path, monkeypatch
) -> None:
    """The launcher runs --check on every start; it used to cost ~1.9 s of it.

    Two interpreter starts on Windows are two CreateProcess pairs plus the
    antivirus tax, for a question the stamp can already answer.
    """

    bootstrap = _load_bootstrap()
    environment = _ready_environment(bootstrap, tmp_path)
    fingerprint = bootstrap._fingerprint()
    bootstrap._write_stamp(environment, fingerprint)

    commands: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    assert bootstrap._validate(environment, fingerprint) == (True, "ready")
    assert commands == [], "the fast path must not spawn anything"


def test_installing_a_package_out_of_band_defeats_the_fast_path(tmp_path, monkeypatch) -> None:
    """The whole point of the slow path is catching a hand-modified venv."""

    bootstrap = _load_bootstrap()
    environment = _ready_environment(bootstrap, tmp_path)
    fingerprint = bootstrap._fingerprint()
    bootstrap._write_stamp(environment, fingerprint)

    site_packages = bootstrap._site_packages(environment)
    assert site_packages is not None
    (site_packages / "somethingelse-9.9.dist-info").mkdir()

    commands: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    assert bootstrap._validate(environment, fingerprint) == (True, "ready")
    assert commands, "a changed distribution set must fall through to the full check"


def test_changing_a_pinned_git_commit_defeats_the_fast_path(tmp_path, monkeypatch) -> None:
    bootstrap = _load_bootstrap()
    environment = _ready_environment(bootstrap, tmp_path)
    fingerprint = bootstrap._fingerprint()
    bootstrap._write_stamp(environment, fingerprint)

    site_packages = bootstrap._site_packages(environment)
    assert site_packages is not None
    name = next(iter(bootstrap._locked_git_commits()))
    direct_url = site_packages / f"{name.replace('-', '_')}-0.1.0.dist-info" / "direct_url.json"
    metadata = json.loads(direct_url.read_text(encoding="utf-8"))
    metadata["vcs_info"]["commit_id"] = "0" * 40
    direct_url.write_text(json.dumps(metadata), encoding="utf-8")

    commands: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    assert bootstrap._validate(environment, fingerprint) == (True, "ready")
    assert commands, "changed Git provenance must fall through to the full check"


def test_a_replaced_interpreter_defeats_the_fast_path(tmp_path, monkeypatch) -> None:
    bootstrap = _load_bootstrap()
    environment = _ready_environment(bootstrap, tmp_path)
    fingerprint = bootstrap._fingerprint()
    bootstrap._write_stamp(environment, fingerprint)

    python = bootstrap._venv_python(environment)
    python.write_bytes(b"a different interpreter entirely")

    commands: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    assert bootstrap._validate(environment, fingerprint) == (True, "ready")
    assert commands, "a replaced interpreter must fall through to the full check"


def test_post_install_verification_never_trusts_the_stamp(tmp_path, monkeypatch) -> None:
    """It is the check that caught Windows being unable to satisfy uvloop.

    A stamp written seconds earlier by the same run proves nothing about what
    pip actually installed, so the fast path must be unavailable there.
    """

    bootstrap = _load_bootstrap()
    environment = _ready_environment(bootstrap, tmp_path)
    fingerprint = bootstrap._fingerprint()
    bootstrap._write_stamp(environment, fingerprint)

    commands: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    bootstrap._validate(environment, fingerprint, False)
    assert commands, "the post-install check must always run the real probe"


def test_the_stamp_records_the_evidence_the_fast_path_needs(tmp_path) -> None:
    bootstrap = _load_bootstrap()
    environment = _ready_environment(bootstrap, tmp_path)
    bootstrap._write_stamp(environment, bootstrap._fingerprint())
    stamp = json.loads((environment / bootstrap.STAMP_NAME).read_text(encoding="utf-8"))
    evidence = stamp["venvEvidence"]
    assert evidence["distributionCount"] == 2 + len(bootstrap._locked_git_commits())
    assert evidence["pythonSize"] > 0
    assert evidence["distributions"]
    assert evidence["gitDirectUrls"]
    assert evidence["cliEntrypoints"]


def test_the_bootstrap_installs_a_repository_aware_wg_command(tmp_path) -> None:
    bootstrap = _load_bootstrap()
    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()

    bootstrap._install_cli_entrypoint(environment)

    assert bootstrap._validate_cli_entrypoint(environment) == (True, "ready")
    contents = "\n".join(
        path.read_text(encoding="utf-8")
        for path in bootstrap._cli_entrypoint_files(environment)
    )
    assert repr(str(ROOT)) in contents
    assert "server.cli" in contents


def test_the_posix_wg_command_runs_when_the_environment_path_contains_spaces(
    tmp_path,
) -> None:
    if os.name == "nt":
        return
    bootstrap = _load_bootstrap()
    environment = tmp_path / "checkout with spaces" / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)

    bootstrap._install_cli_entrypoint(environment)

    completed = subprocess.run(
        [str(environment / "bin" / "wg"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage: wg" in completed.stdout


def test_check_restamps_an_environment_after_successful_slow_validation(
    tmp_path, monkeypatch
) -> None:
    bootstrap = _load_bootstrap()
    environment = _ready_environment(bootstrap, tmp_path)
    fingerprint = bootstrap._fingerprint()
    (environment / bootstrap.STAMP_NAME).write_text(
        json.dumps({"fingerprint": fingerprint}), encoding="utf-8"
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(bootstrap, "_fingerprint", lambda: fingerprint)
    monkeypatch.setattr(bootstrap, "_bootstrap_lock", lambda _environment: nullcontext())
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )

    assert bootstrap.main(["--check", "--venv", str(environment)]) == 0
    assert len(commands) == 2
    stamp = json.loads((environment / bootstrap.STAMP_NAME).read_text(encoding="utf-8"))
    assert stamp["venvEvidence"] == bootstrap._venv_evidence(environment)

    assert bootstrap.main(["--check", "--venv", str(environment)]) == 0
    assert len(commands) == 2, "the second check should use the newly recorded fast path"


def test_bootstrap_force_reinstalls_git_pins_after_manifest_change(tmp_path, monkeypatch) -> None:
    bootstrap = _load_bootstrap()
    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    validations = iter(((False, "the dependency manifests changed"), (True, "ready")))
    commands: list[list[str]] = []

    monkeypatch.setattr(bootstrap, "_require_supported_python", lambda: None)
    # This test owns dependency-command semantics, not lock placement. Keep its
    # mutations inside tmp_path even when the checkout's .git is read-only.
    monkeypatch.setattr(bootstrap, "_bootstrap_lock", lambda _environment: nullcontext())
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


def test_bootstrap_force_reinstalls_all_declared_packages_and_removes_extras(
    tmp_path, monkeypatch
) -> None:
    bootstrap = _load_bootstrap()
    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    validations = iter(((True, "ready"), (True, "ready")))
    commands: list[list[str]] = []

    monkeypatch.setattr(bootstrap, "_require_supported_python", lambda: None)
    monkeypatch.setattr(bootstrap, "_bootstrap_lock", lambda _environment: nullcontext())
    monkeypatch.setattr(bootstrap, "_validate", lambda *_args: next(validations))
    monkeypatch.setattr(bootstrap, "_write_stamp", lambda *_args: None)
    monkeypatch.setattr(
        bootstrap,
        "_installed_distribution_names",
        lambda _python: bootstrap._declared_distribution_names() | {"obsolete-package"},
    )
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )

    bootstrap.bootstrap(environment, force=True)

    install_commands = [command for command in commands if "install" in command]
    assert len(install_commands) == 3
    assert all("--force-reinstall" in command for command in install_commands)
    locked_install = next(
        command
        for command in install_commands
        if str(ROOT / "server" / "requirements-lock.txt") in command
    )
    lock_index = locked_install.index(str(ROOT / "server" / "requirements-lock.txt"))
    assert locked_install[lock_index - 1] == "-r"
    assert [
        command
        for command in commands
        if "uninstall" in command and "obsolete-package" in command
    ]
