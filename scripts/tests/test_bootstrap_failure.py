"""A failed reinstall must not retain a stamp that claims the venv is ready."""

import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import bootstrap


def test_failed_reinstall_invalidates_the_previous_stamp(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "REPO_ROOT", checkout)
    monkeypatch.setattr(bootstrap, "_fingerprint", lambda: "test-fingerprint")
    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    bootstrap._install_cli_entrypoint(environment)
    stamp = environment / bootstrap.STAMP_NAME
    stamp.write_text('{"fingerprint": "previous proof"}', encoding="utf-8")

    monkeypatch.setattr(bootstrap, "_require_supported_python", lambda: None)
    monkeypatch.setattr(bootstrap, "_validate", lambda *_args, **_kwargs: (False, "forced"))
    monkeypatch.setattr(bootstrap, "_run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1))

    with pytest.raises(RuntimeError, match="Dependency installation failed"):
        bootstrap.bootstrap(environment, force=True)

    assert not stamp.exists()

    # The exceptional path closes its descriptor and releases the OS lock.
    with bootstrap._bootstrap_lock(environment):
        pass


def test_environment_lock_lives_in_git_metadata_and_is_path_keyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    git_dir = checkout / ".git"
    git_dir.mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "REPO_ROOT", checkout)
    first_environment = checkout / ".venv"
    second_environment = checkout / ".other-venv"

    first_lock = bootstrap._bootstrap_lock_path(first_environment)
    second_lock = bootstrap._bootstrap_lock_path(second_environment)
    with bootstrap._bootstrap_lock(first_environment):
        assert first_lock.is_file()

    assert first_lock.parent == git_dir
    assert second_lock.parent == git_dir
    assert first_lock != second_lock
    assert list(checkout.iterdir()) == [git_dir]


def _run_fake_bootstrap(
    checkout_value: str,
    environment_value: str,
    first: bool,
    worker_started: Any,
    first_pip_entered: Any,
    second_pip_entered: Any,
    release_first: Any,
) -> None:
    """Run bootstrap with its pip subprocess replaced by a process barrier."""

    checkout = Path(checkout_value)
    environment = Path(environment_value)
    bootstrap.REPO_ROOT = checkout
    bootstrap._require_supported_python = lambda: None
    bootstrap._fingerprint = lambda: "test-fingerprint"

    def validate(*args: object, **_kwargs: object) -> tuple[bool, str]:
        allow_fast_path = True if len(args) < 3 else bool(args[2])
        return (False, "forced") if allow_fast_path else (True, "ready")

    bootstrap._validate = validate
    bootstrap._write_stamp = lambda *_args, **_kwargs: None
    pip_calls = 0

    def run_pip(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal pip_calls
        pip_calls += 1
        if first and pip_calls == 1:
            first_pip_entered.set()
            if not release_first.wait(15):
                raise TimeoutError("test did not release the first bootstrap")
        elif not first:
            second_pip_entered.set()
        return SimpleNamespace(returncode=0)

    bootstrap._run = run_pip
    worker_started.set()
    bootstrap.bootstrap(environment, force=True)


def test_two_bootstraps_never_run_pip_mutations_concurrently(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()

    context = multiprocessing.get_context("spawn")
    first_started = context.Event()
    second_started = context.Event()
    first_pip_entered = context.Event()
    second_pip_entered = context.Event()
    release_first = context.Event()
    common = (
        str(checkout),
        str(environment),
    )
    first = context.Process(
        target=_run_fake_bootstrap,
        args=(
            *common,
            True,
            first_started,
            first_pip_entered,
            second_pip_entered,
            release_first,
        ),
    )
    second = context.Process(
        target=_run_fake_bootstrap,
        args=(
            *common,
            False,
            second_started,
            first_pip_entered,
            second_pip_entered,
            release_first,
        ),
    )

    first.start()
    assert first_started.wait(10), "first bootstrap did not start"
    assert first_pip_entered.wait(10), "first bootstrap did not enter pip"
    second.start()
    try:
        assert second_started.wait(10), "second bootstrap did not start"
        assert not second_pip_entered.wait(0.5)
    finally:
        release_first.set()
        first.join(15)
        second.join(15)

    assert first.exitcode == 0
    assert second.exitcode == 0
    assert second_pip_entered.is_set()


def test_check_does_not_rewrite_validation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "REPO_ROOT", checkout)
    monkeypatch.setattr(bootstrap, "_fingerprint", lambda: "test-fingerprint")
    monkeypatch.setattr(bootstrap, "_locked_versions", lambda: {})
    monkeypatch.setattr(bootstrap, "_locked_git_commits", lambda: {})
    monkeypatch.setattr(
        bootstrap, "_run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0)
    )
    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    bootstrap._install_cli_entrypoint(environment)
    stamp = environment / bootstrap.STAMP_NAME
    original = json.dumps({"fingerprint": "test-fingerprint"})
    stamp.write_text(original, encoding="utf-8")

    assert bootstrap.main(["--check", "--venv", str(environment)]) == 0

    assert stamp.read_text(encoding="utf-8") == original
    assert not stamp.with_name(f"{bootstrap.STAMP_NAME}.tmp").exists()
