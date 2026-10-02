"""bundle-install.sh --update: replace an existing installation, never create one."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Before anything below: module-level parametrize lists name POSIX signals
# (SIGHUP, SIGQUIT) that Windows Python does not define, so a skip mark is too
# late there; collection would fail on import.
if sys.platform == "win32":
    pytest.skip("POSIX shell installer", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "installers" / "linux" / "bundle-install.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell installer")


@pytest.fixture(autouse=True)
def check_generated_shell_shims(tmp_path: Path):
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and "bin" in path.parent.name:
            if path.read_bytes().startswith((b"#!/bin/sh\n", b"#!/bin/bash\n")):
                result = subprocess.run(["/bin/sh", "-n", str(path)], capture_output=True, text=True)
                assert result.returncode == 0, f"{path.name}: {result.stderr}"


def installer_process_signals() -> None:
    """Use catchable signals even when the suite broker started with SIG_IGN.

    POSIX sh cannot install a handler for a signal ignored on entry. Set the
    installer launch contract explicitly instead of inheriting the test runner.
    """
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT):
        signal.signal(sig, signal.SIG_DFL)


def process_snapshot() -> dict[int, tuple[int, int, int, int]]:
    """Live PID -> (parent, group, session, birth), including foreign groups."""
    result = {}
    if sys.platform == 'darwin':
        import ctypes

        lib = ctypes.CDLL('/usr/lib/libproc.dylib')
        pids = (ctypes.c_int * (lib.proc_listallpids(None, 0) + 128))()
        count = lib.proc_listallpids(pids, ctypes.sizeof(pids))
        for pid in pids[:count]:
            info = (ctypes.c_uint32 * 34)()
            # PROC_PIDTBSDINFO includes start timeval; status 5 is SZOMB.
            if lib.proc_pidinfo(pid, 3, 0, info, ctypes.sizeof(info)) != ctypes.sizeof(info) or info[1] == 5:
                continue
            try:
                birth = (info[30] | info[31] << 32) * 1_000_000 + (info[32] | info[33] << 32)
                result[pid] = (info[4], info[25], os.getsid(pid), birth)
            except ProcessLookupError:
                pass
    else:
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rpartition(')')[2].split()
                if fields[0] != 'Z':
                    result[int(path.parent.name)] = (*map(int, fields[1:4]), int(fields[19]))
            except (FileNotFoundError, ProcessLookupError):
                pass
    return result


class InstallerFamily:
    """Record ownership at launch; assert before teardown and clean only our PIDs."""

    def __init__(self, proc) -> None:
        self.pid = proc.pid
        self.group = os.getpgid(proc.pid)
        self.session = os.getsid(proc.pid)
        assert self.group == self.session == self.pid
        row = process_snapshot().get(self.pid)
        self.birth = row[3] if row else None
        self.descendants = {self.pid: self.birth}

    def live(self) -> set[int]:
        rows = process_snapshot()
        root = rows.get(self.pid)
        same_session = not (root and root[3] != self.birth and root[2] == self.session)
        while True:
            owned = {pid for pid, birth in self.descendants.items() if pid in rows and rows[pid][3] == birth}
            # A group number can be reused in another session. Session identity
            # covers every original group, including children that enable monitor mode.
            children = {pid: birth for pid, (parent, _group, session, birth) in rows.items()
                        if parent in owned or (same_session and session == self.session)}
            if all(self.descendants.get(pid) == birth for pid, birth in children.items()):
                break
            self.descendants.update(children)
        return owned

    def assert_gone(self) -> None:
        deadline = time.monotonic() + .5
        while live := self.live():
            if time.monotonic() >= deadline:
                pytest.fail(f'installer left live processes: {sorted(live)} (group {self.group}, session {self.session})')
            time.sleep(.01)

    def stop(self) -> None:
        # Stop the session leader last, keeping descendants signalable until
        # their own KILL has been sent.
        for pid in sorted(self.live(), key=lambda pid: pid == self.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize('reused_session', (100, 99))
def test_installer_family_does_not_adopt_reused_pids(monkeypatch, reused_session) -> None:
    rows = {100: (99, 100, 100, 1), 101: (100, 100, 100, 2)}
    monkeypatch.setattr(sys.modules[__name__], 'process_snapshot', lambda: rows.copy())
    monkeypatch.setattr(os, 'getpgid', lambda pid: 100)
    monkeypatch.setattr(os, 'getsid', lambda pid: 100)
    killed = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: killed.append(pid))
    family = InstallerFamily(type('Proc', (), {'pid': 100})())
    assert family.live() == {100, 101}
    # Both a descendant PID and the original session leader may be reused.
    rows.update({100: (99, 100, reused_session, 3), 101: (100, 100, reused_session, 4)})
    assert not family.live()
    family.stop()
    assert not killed


def make_tarball(root: Path, version: str) -> Path:
    """The extracted tarball layout: the installer and uninstaller beside the app folder."""
    root.mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "install.sh")
    (root / "uninstall.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    app = root / "waveguide-generator"
    (app / "app").mkdir(parents=True)
    (app / "runtime").mkdir()
    (app / "runtime" / "bin").mkdir()
    # Model the bundled interpreter used to reset optional-tool signals.
    interpreter = app / "runtime" / "bin" / "python3.13"
    interpreter.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    interpreter.chmod(0o755)
    (app / "app" / "APP-MANIFEST.json").write_text("{}", encoding="utf-8")
    (app / "runtime" / "RUNTIME-MANIFEST.json").write_text("{}", encoding="utf-8")
    (app / "version.txt").write_text(version, encoding="utf-8")
    launcher = app / "waveguide-generator"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    (app / "waveguide-generator.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Waveguide Generator\n"
        "Exec=@INSTALL_DIR@/waveguide-generator\nIcon=waveguide-generator\n",
        encoding="utf-8",
    )
    (app / "waveguide-generator.png").write_bytes(b"png")
    return root


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    return {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / "share")}


def adapt_link_shim(directory: Path) -> None:
    """Let existing move hooks also intercept file transfers via ln -P."""
    path = directory / "ln"
    if path.exists():
        return
    real_ln = shutil.which("ln")
    path.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "-P" ]; then\nshift\n[ "$1" != "--" ] || shift\n'
        f'exec "{directory / "mv"}" -n -- "$@" </dev/null\nfi\n'
        f'exec "{real_ln}" "$@"\n'
    )
    path.chmod(0o755)


def run(tarball: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    first_path = Path(env["PATH"].split(os.pathsep)[0])
    if (first_path / "mv").is_file():
        adapt_link_shim(first_path)
    return subprocess.run(
        ["/bin/bash", str(tarball / "install.sh"), "--skip-checks", *args], preexec_fn=installer_process_signals,
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
    )


def installed_dir(env: dict[str, str]) -> Path:
    return Path(env["XDG_DATA_HOME"]) / "waveguide-generator"


def test_update_replaces_an_existing_install_and_starts_nothing(tmp_path: Path, env: dict[str, str]) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    (installed_dir(env) / "obsolete.txt").write_text("gone after the update", encoding="utf-8")
    launched = tmp_path / "launched"
    new = make_tarball(tmp_path / "v2", "two")
    (new / "waveguide-generator" / "waveguide-generator").write_text(f'#!/bin/sh\ntouch "{launched}"\n', encoding="utf-8")

    result = run(new, env, "--update")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "two"
    assert not (installed_dir(env) / "obsolete.txt").exists()
    assert sorted(p.name for p in installed_dir(env).parent.glob(".waveguide-generator.*")) == []
    assert not launched.exists()


def test_update_refuses_when_nothing_is_installed(tmp_path: Path, env: dict[str, str]) -> None:
    result = run(make_tarball(tmp_path / "v2", "two"), env, "--update")
    assert result.returncode == 1
    assert "no Waveguide Generator installation" in result.stdout
    assert not installed_dir(env).exists()


def test_update_refuses_a_directory_that_is_not_our_install(tmp_path: Path, env: dict[str, str]) -> None:
    foreign = installed_dir(env)
    foreign.mkdir(parents=True)
    (foreign / "keep.txt").write_text("mine", encoding="utf-8")
    result = run(make_tarball(tmp_path / "v2", "two"), env, "--update")
    assert result.returncode == 1
    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_update_rolls_back_when_the_commit_fails(tmp_path: Path, env: dict[str, str]) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    shim = tmp_path / "bin"
    shim.mkdir()
    real_mv = shutil.which("mv")
    # Fail the rename that puts the staged tree into place, after the old one moved aside.
    (shim / "mv").write_text(
        '#!/bin/bash\ncase "$*" in *".install."*) echo injected >&2; exit 1;; esac\n'
        f'exec "{real_mv}" "$@"\n',
        encoding="utf-8",
    )
    (shim / "mv").chmod(0o755)
    result = run(make_tarball(tmp_path / "v2", "two"), {**env, "PATH": f"{shim}{os.pathsep}{env['PATH']}"}, "--update")
    assert result.returncode == 1
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "one"
    assert sorted(p.name for p in installed_dir(env).parent.glob(".waveguide-generator.*")) == []


def integration_paths(env: dict[str, str]) -> dict[str, Path]:
    data = Path(env["XDG_DATA_HOME"])
    return {
        "desktop": data / "applications" / "waveguide-generator.desktop",
        "icon": data / "icons" / "hicolor" / "512x512" / "apps" / "waveguide-generator.png",
        "desktop_owner": data / "applications" / ".waveguide-generator.owner",
        "icon_owner": data / "icons" / "hicolor" / "512x512" / "apps" / ".waveguide-generator.owner",
        "link": Path(env["HOME"]) / ".local" / "bin" / "waveguide-generator",
    }


def late_failure_env(tmp_path: Path, env: dict[str, str], *, restore_failure: str | None = None) -> dict[str, str]:
    """Fail the final link rename, optionally fail one of the restore renames."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    patterns = {
        "application": ".previous.",
        "desktop": ".desktop.backup.",
        "icon": ".icon.backup.",
        "desktop_owner": "applications/.waveguide-generator.owner.backup.",
        "icon_owner": "apps/.waveguide-generator.owner.backup.",
        "link": ".link.backup.",
    }
    real_mv = shutil.which("mv")
    failure = (
        f'case "$source" in *"{patterns[restore_failure]}"*) exit 1;; esac\n'
        if restore_failure else ""
    )
    (bin_dir / "mv").write_text(
        '#!/bin/sh\nsource=""\nfor arg do case "$arg" in -*) ;; *) source="$arg"; break;; esac; done\n'
        'case "$source" in *".link.new."*) exit 1;; esac\n' + failure
        + f'exec "{real_mv}" "$@"\n', encoding="utf-8",
    )
    (bin_dir / "mv").chmod(0o755)
    adapt_link_shim(bin_dir)
    return {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}


def test_a_late_failure_restores_the_app_and_all_desktop_integration(tmp_path: Path, env: dict[str, str]) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    paths = integration_paths(env)
    # Distinct old contents expose a failed restore even if defaults would match.
    for name, path in paths.items():
        if name != "link":
            path.write_text(f"old {name}", encoding="utf-8")
    original_link = os.readlink(paths["link"])
    result = run(make_tarball(tmp_path / "v2", "two"), late_failure_env(tmp_path, env), "--update")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Restored the previous installation and desktop integration" in result.stdout
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "one"
    for name, path in paths.items():
        if name != "link":
            assert path.read_text(encoding="utf-8") == f"old {name}"
    assert os.readlink(paths["link"]) == original_link
    assert not list(Path(env["HOME"]).rglob("*.backup.*"))
    assert not list(installed_dir(env).parent.glob(".waveguide-generator.*"))


@pytest.mark.parametrize("artifact", ("application", "desktop", "icon", "desktop_owner", "icon_owner", "link"))
def test_incomplete_rollback_returns_3_and_preserves_the_named_backup(
    tmp_path: Path, env: dict[str, str], artifact: str,
) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    paths = integration_paths(env)
    old_path = installed_dir(env) if artifact == "application" else paths[artifact]
    old_contents = None if artifact in ("application", "link") else old_path.read_bytes()
    old_link = os.readlink(old_path) if artifact == "link" else None
    result = run(
        make_tarball(tmp_path / "v2", "two"),
        late_failure_env(tmp_path, env, restore_failure=artifact), "--update",
    )
    assert result.returncode == 3, result.stdout + result.stderr
    backup_lines = [line for line in result.stderr.splitlines() if line.startswith("Its backup remains at: ")]
    assert len(backup_lines) == 1, result.stderr
    backup = Path(backup_lines[0].removeprefix("Its backup remains at: "))
    assert backup.parent == old_path.parent
    assert not old_path.exists() and not old_path.is_symlink()
    if artifact == "application":
        assert (backup / "version.txt").read_text(encoding="utf-8") == "one"
    elif artifact == "link":
        assert backup.is_symlink() and os.readlink(backup) == old_link
    else:
        assert backup.read_bytes() == old_contents
    assert "rollback was incomplete" in result.stderr
    assert "Restored the previous installation" not in result.stdout


@pytest.mark.parametrize("interrupt", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
def test_a_signal_during_swap_returns_1_after_restoring_the_app(
    tmp_path: Path, env: dict[str, str], interrupt: signal.Signals,
) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    new = make_tarball(tmp_path / "v2", "two")
    paused = tmp_path / "paused"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_mv = shutil.which("mv")
    (bin_dir / "mv").write_text(
        f'#!/bin/sh\ncase "$*" in *".install."*) touch "{paused}"; exec sleep 60;; esac\n'
        f'exec "{real_mv}" "$@"\n', encoding="utf-8",
    )
    (bin_dir / "mv").chmod(0o755)
    proc = subprocess.Popen(
        ["/bin/bash", str(new / "install.sh"), "--skip-checks", "--update"], preexec_fn=installer_process_signals,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL,
        env={**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not paused.exists():
            assert proc.poll() is None and time.monotonic() < deadline, "swap never reached"
            time.sleep(0.05)
        assert not installed_dir(env).exists()
        os.killpg(proc.pid, interrupt)
        output, _ = proc.communicate(timeout=15)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
    assert proc.returncode == 1, output
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "one"
    assert not list(installed_dir(env).parent.glob(".waveguide-generator.*"))


def test_update_with_unicode_and_escaped_desktop_paths(tmp_path: Path, env: dict[str, str]) -> None:
    prefix = tmp_path / 'Mes  Apps é "quoted" $dollar `tick` \\slash'
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--prefix", str(prefix), "--no-launch").returncode == 0
    result = run(make_tarball(tmp_path / "v2", "two"), env, "--prefix", str(prefix), "--update")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (prefix / "waveguide-generator" / "version.txt").read_text(encoding="utf-8") == "two"
    entry = integration_paths(env)["desktop"].read_text(encoding="utf-8")
    assert 'Mes  Apps é' in entry
    assert '\\\\"quoted\\\\"' in entry and '\\\\$dollar' in entry and '\\\\`tick\\\\`' in entry
    assert '\\\\\\\\slash' in entry


@pytest.mark.parametrize("supports_t", (False, True))
def test_a_directory_racing_the_restore_is_not_reported_as_restored(
    tmp_path: Path, env: dict[str, str], supports_t: bool,
) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    failure_env = late_failure_env(tmp_path, env)
    real_mv = shutil.which("mv")
    t_option = '[ "$1" = "-T" ] && exit 1\n' if not supports_t else (
        'no_nesting=0\nif [ "$1" = "-T" ]; then no_nesting=1; shift; fi\n'
    )
    safe_move = 'if [ "$no_nesting" = 1 ] && [ -e "$2" ]; then exit 1; fi\n' if supports_t else ''
    # Emulate GNU -T on BSD too; the false case forces the portable fallback.
    (tmp_path / "bin" / "mv").write_text(
        '#!/bin/sh\n' + t_option + '[ "$1" = "-n" ] && shift\n[ "$1" = "--" ] && shift\n'
        'case "$1" in *".link.new."*) exit 1;; *".previous."*)\n'
        'mkdir -p "$2/app"\necho racer > "$2/app/APP-MANIFEST.json"\n;; esac\n'
        + safe_move + f'exec "{real_mv}" "$@"\n', encoding="utf-8",
    )
    (tmp_path / "bin" / "mv").chmod(0o755)
    result = run(make_tarball(tmp_path / "v2", "two"), failure_env, "--update")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Restored the previous installation" not in result.stdout
    lines = [line for line in result.stderr.splitlines() if line.startswith("Its backup remains at: ")]
    assert len(lines) == 1, result.stderr
    backup = Path(lines[0].removeprefix("Its backup remains at: "))
    expected_parent = installed_dir(env).parent if supports_t else installed_dir(env)
    assert backup.parent == expected_parent
    assert (backup / "version.txt").read_text(encoding="utf-8") == "one"
    assert not (installed_dir(env) / "version.txt").exists()
    assert (installed_dir(env) / "app" / "APP-MANIFEST.json").read_text(encoding="utf-8").strip() == "racer"


@pytest.mark.parametrize("interrupt", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
def test_a_signal_during_rollback_allows_the_old_installation_to_be_restored(
    tmp_path: Path, env: dict[str, str], interrupt: signal.Signals,
) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    failure_env = late_failure_env(tmp_path, env)
    new = make_tarball(tmp_path / "v2", "two")
    paused = tmp_path / "rollback-paused"
    release = tmp_path / "rollback-release"
    real_mv = shutil.which("mv")
    (tmp_path / "bin" / "mv").write_text(
        '#!/bin/sh\nsource=""\nfor arg do case "$arg" in -*) ;; *) source="$arg"; break;; esac; done\n'
        'case "$source" in *".link.new."*) exit 1;; *".previous."*)\n'
        f'touch "{paused}"\nwhile [ ! -e "{release}" ]; do sleep 0.05; done\n;; esac\n'
        f'exec "{real_mv}" "$@"\n', encoding="utf-8",
    )
    (tmp_path / "bin" / "mv").chmod(0o755)
    proc = subprocess.Popen(
        ["/bin/bash", str(new / "install.sh"), "--skip-checks", "--update"], preexec_fn=installer_process_signals,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL,
        env=failure_env, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not paused.exists():
            assert proc.poll() is None and time.monotonic() < deadline, "rollback never reached"
            time.sleep(0.05)
        assert not installed_dir(env).exists()
        os.killpg(proc.pid, interrupt)
        release.touch()
        output, _ = proc.communicate(timeout=15)
    finally:
        release.touch()
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
    assert proc.returncode == 1, output
    assert "Restored the previous installation and desktop integration" in output
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "one"
    assert not list(Path(env["HOME"]).rglob("*.backup.*"))
    assert not list(installed_dir(env).parent.glob(".waveguide-generator.*"))


# Deterministic pauses use the same temporary-file/process-group mechanism as
# the earlier mv-shim tests. Instrument only the shipped copy, never the source.
BOUNDARIES = (
    "staged", "prepared", "displace_intent", "post_displace", "displaced",
    "install_intent", "post_install", "installed", "evacuate_intent",
    "post_evacuate", "evacuated", "restore_intent", "post_restore", "restored",
)
RECOVERY_BOUNDARIES = frozenset(BOUNDARIES[8:])


def instrument_boundaries(script: Path, row: int, boundary: str, paused: Path, release: Path, side: str = "after") -> None:
    import re

    body = script.read_text(encoding="utf-8")
    hook = (
        'state_boundary() {\n'
        f'    [ "$1:$2" = "{row}:{boundary}" ] || return 0\n'
        f'    touch "{paused}"\n'
        f'    while [ ! -e "{release}" ]; do sleep 0.01; done\n'
        '}\n'
    )
    body = body.replace("set -u\n", "set -u\n" + hook, 1)
    row_expr = '"$i"' if "STATE[i]=" in body else "0"
    def state_hook(m):
        if m[3] == "staged":
            return m[0]
        hook_line = f"{m[1]}state_boundary {row_expr} {m[3]}"
        state_line = f"{m[1]}{m[2]}"
        return f"{hook_line}\n{state_line}" if side == "before" else f"{state_line}\n{hook_line}"
    body = re.sub(
        r"^(\s*)(STATE(?:\[i\])?=([a-z_]+))$",
        state_hook,
        body, flags=re.MULTILINE,
    )
    needle = '    same_object "${STAGED[i]}" "${NEW_ID[i]}" || fail' if row_expr != "0" else 'same_object "$STAGED_PATH" "$NEW_ID" || fail'
    body = body.replace(needle, f'    state_boundary {row_expr} staged\n' + needle, 1)
    if boundary == "cleanup_entry":
        body = body.replace("cleanup() {\n", "cleanup() {\n    state_boundary 0 cleanup_entry\n", 1)
    if boundary in RECOVERY_BOUNDARIES:
        body = body.replace("COMMITTED=1\n", "exit 1\n", 1)
    elif boundary == "committed":
        body = body.replace("COMMITTED=1\n", "COMMITTED=1\nstate_boundary 0 committed\n", 1)
    script.write_text(body, encoding="utf-8")


def state_move_shim(bin_dir: Path, paths: list[Path], row: int, boundary: str, paused: Path, release: Path) -> None:
    """Pause after the real rename but before the installer can record its result."""
    bin_dir.mkdir(exist_ok=True)
    real_mv = shutil.which("mv")
    wrapper = bin_dir / "mv"
    wrapper.write_text(
        f'#!{sys.executable}\n'
        'import pathlib, subprocess, sys, time\n'
        f'paths = {[str(p) for p in paths]!r}\n'
        'args = [arg for arg in sys.argv[1:] if not arg.startswith("-")]\n'
        'source, destination = args[-2:]\n'
        'phase, index = "", -1\n'
        'if source in paths:\n'
        '    index = paths.index(source)\n'
        '    phase = "post_displace" if (".backup." in destination or ".previous." in destination) else "post_evacuate"\n'
        'elif destination in paths:\n'
        '    index = paths.index(destination)\n'
        '    phase = "post_restore" if (".backup." in source or ".previous." in source) else "post_install"\n'
        f'result = subprocess.run([{real_mv!r}, *sys.argv[1:]])\n'
        f'if result.returncode == 0 and (index, phase) == ({row}, {boundary!r}):\n'
        f'    pathlib.Path({str(paused)!r}).touch()\n'
        f'    while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.01)\n'
        'sys.exit(result.returncode)\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)
    adapt_link_shim(bin_dir)


def signal_paused_process(command: list[str], env: dict[str, str], paused: Path, release: Path,
                          interrupt: signal.Signals = signal.SIGTERM, *, burst: bool = False) -> tuple[int, str]:
    proc = subprocess.Popen(
        command, preexec_fn=installer_process_signals, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    family = InstallerFamily(proc)
    try:
        deadline = time.monotonic() + 15
        while not paused.exists():
            family.live()
            assert proc.poll() is None and time.monotonic() < deadline, "boundary never reached"
            time.sleep(0.01)
        os.killpg(proc.pid, interrupt)
        release.touch()
        if burst:
            # Keep the group-interruption probe, then spread further signals
            # across recovery without repeatedly killing its move children.
            for _ in range(39):
                try:
                    os.kill(proc.pid, interrupt)
                except ProcessLookupError:
                    break
                time.sleep(.0005)
        output, _ = proc.communicate(timeout=15)
        family.assert_gone()
    finally:
        release.touch()
        family.stop()
        # Kill only the session we created, also cleaning any surviving shim child.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        family.assert_gone()
    return proc.returncode, output


def noninteractive_move_shim(bin_dir: Path, log: Path, fail_source: str, cleanup_marker: Path) -> None:
    """Inspect actual argv/fd 0 while the installer itself receives a pipe."""
    bin_dir.mkdir()
    real_mv = shutil.which("mv")
    wrapper = bin_dir / "mv"
    wrapper.write_text(
        f'#!{sys.executable}\n'
        'import os, pathlib, signal, stat, subprocess, sys\n'
        'args = sys.argv[1:]\n'
        'fd = os.fstat(0)\n'
        'safe = "-n" in args and stat.S_ISCHR(fd.st_mode) and fd.st_rdev == os.stat("/dev/null").st_rdev\n'
        f'cleanup = pathlib.Path({str(cleanup_marker)!r}).exists()\n'
        'signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT) if cleanup else (signal.SIGHUP, signal.SIGTERM)\n'
        'safe = safe and all((signal.getsignal(sig) == signal.SIG_IGN) == cleanup for sig in signals)\n'
        f'with open({str(log)!r}, "a") as stream: stream.write(str(safe) + " " + repr(args) + (" cleanup" if cleanup else " forward") + "\\n")\n'
        'if not safe: sys.exit(91)\n'
        'source = [arg for arg in args if not arg.startswith("-")][-2]\n'
        f'if {fail_source!r} in source: sys.exit(1)\n'
        f'sys.exit(subprocess.run([{real_mv!r}, *args]).returncode)\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)
    adapt_link_shim(bin_dir)


# The full matrix (6 rows x 14 boundaries x 2 sides, 168 installs) runs with
# WG_STRESS=1 (the on-demand installer-stress workflow and branch evidence).
# The default suite keeps every boundary on both sides, rotating through the
# rows, so each row and each boundary is still exercised: 28 installs.
STRESS = os.environ.get("WG_STRESS") == "1"
ROW_BOUNDARY_CASES = (
    [(row, boundary) for row in range(6) for boundary in BOUNDARIES]
    if STRESS
    else [(index % 6, boundary) for index, boundary in enumerate(BOUNDARIES)]
)


@pytest.mark.parametrize("row,boundary", ROW_BOUNDARY_CASES)
@pytest.mark.parametrize("side", ("before", "after"))
@pytest.mark.slow
def test_every_row_recovers_at_every_state_boundary(tmp_path: Path, env: dict[str, str], row: int, boundary: str, side: str) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    paths = [installed_dir(env), *integration_paths(env).values()]
    # Snapshot contents AND inodes: a superficially identical replacement is not restoration.
    identities = [p.lstat().st_ino for p in paths]
    contents = [p.read_bytes() for p in paths[1:-1]]
    link = os.readlink(paths[-1])
    new = make_tarball(tmp_path / "v2", "two")
    paused, release = tmp_path / "paused", tmp_path / "release"
    instrument_boundaries(new / "install.sh", row, boundary, paused, release, side)
    bin_dir = tmp_path / "bin"
    state_move_shim(bin_dir, paths, row, boundary, paused, release)
    code, output = signal_paused_process(
        ["/bin/bash", str(new / "install.sh"), "--skip-checks", "--update"],
        {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}, paused, release, burst=True,
    )
    assert code == 1, output
    assert (paths[0] / "version.txt").read_text() == "one"
    assert [p.lstat().st_ino for p in paths] == identities
    assert [p.read_bytes() for p in paths[1:-1]] == contents
    assert os.readlink(paths[-1]) == link
    assert not list(Path(env["HOME"]).rglob("*.backup.*"))
    assert not list(paths[0].parent.glob(".waveguide-generator.*"))
    assert not any(p.name.startswith(".waveguide-generator.") and p.is_dir()
                   for p in paths[0].rglob("*"))


def test_signal_after_commit_keeps_the_complete_new_installation(tmp_path: Path, env: dict[str, str]) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    for name, path in integration_paths(env).items():
        if name != "link":
            path.write_text(f"old {name}")
    new = make_tarball(tmp_path / "v2", "two")
    paused, release = tmp_path / "paused", tmp_path / "release"
    instrument_boundaries(new / "install.sh", 0, "committed", paused, release)
    code, output = signal_paused_process(
        ["/bin/bash", str(new / "install.sh"), "--skip-checks", "--update"], env, paused, release,
    )
    assert code == 0, output
    target = installed_dir(env)
    assert (target / "version.txt").read_text() == "two"
    assert all(p.is_file() for p in list(integration_paths(env).values())[:-1])
    paths = integration_paths(env)
    assert paths["icon"].read_bytes() == b"png"
    assert 'Exec="' + str(target / "waveguide-generator") + '"' in paths["desktop"].read_text()
    assert paths["desktop_owner"].read_text().strip() == str(target)
    assert paths["icon_owner"].read_text().strip() == str(target)
    assert os.readlink(paths["link"]) == str(target / "waveguide-generator")
    assert not list(target.rglob(".waveguide-generator.*"))


@pytest.mark.parametrize("interrupt", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
def test_signal_after_displacement_before_bookkeeping(tmp_path: Path, env: dict[str, str], interrupt: signal.Signals) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    new = make_tarball(tmp_path / "v2", "two")
    paused, release = tmp_path / "paused", tmp_path / "release"
    bin_dir = tmp_path / "bin"
    state_move_shim(bin_dir, [installed_dir(env), *integration_paths(env).values()], 0, "post_displace", paused, release)
    code, output = signal_paused_process(
        ["/bin/bash", str(new / "install.sh"), "--skip-checks", "--update"],
        {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}, paused, release, interrupt,
    )
    assert code == 1, output
    assert (installed_dir(env) / "version.txt").read_text() == "one"
    assert not list(installed_dir(env).parent.glob(".waveguide-generator.*"))


@pytest.mark.parametrize("artifact", ("application", "desktop", "icon", "desktop_owner", "icon_owner", "link"))
@pytest.mark.parametrize("supports_t", (False, True))
def test_each_restored_object_rejects_nesting(tmp_path: Path, env: dict[str, str], artifact: str, supports_t: bool) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    target = installed_dir(env) if artifact == "application" else integration_paths(env)[artifact]
    old_inode = target.lstat().st_ino
    failure_env = late_failure_env(tmp_path, env)
    real_mv = shutil.which("mv")
    bin_dir = tmp_path / "bin"
    # Model -T's refusal on BSD while the unsupported case runs the real BSD mv.
    (bin_dir / "mv").write_text(
        '#!/bin/sh\nno_nesting=0\n'
        + ('[ "$1" = "-T" ] && exit 1\n' if not supports_t else
           'if [ "$1" = "-T" ]; then no_nesting=1; shift; fi\n')
        + '[ "$1" = "-n" ] && shift\n[ "$1" = "--" ] && shift\n'
        'case "$1" in *".link.new."*) exit 1;; esac\n'
        f'if [ "$2" = "{target}" ] && case "$1" in *.backup.*|*.previous.*) true;; *) false;; esac; then\n'
        'mkdir -p "$2"\necho racer > "$2/keep.txt"\n'
        '[ "$no_nesting" = 0 ] || exit 1\nfi\n'
        f'exec "{real_mv}" -f "$@" </dev/null\n', encoding="utf-8",
    )
    (bin_dir / "mv").chmod(0o755)
    result = run(make_tarball(tmp_path / "v2", "two"), failure_env, "--update")
    assert result.returncode == 3, result.stdout + result.stderr
    lines = [s for s in result.stderr.splitlines() if s.startswith("Its backup remains at: ")]
    assert len(lines) == 1, result.stderr
    backup = Path(lines[0].removeprefix("Its backup remains at: "))
    assert backup.lstat().st_ino == old_inode
    assert backup.parent == (target.parent if supports_t and artifact == "application" else target)
    assert (target / "keep.txt").read_text().strip() == "racer"
    assert "Restored the previous installation" not in result.stdout


def test_every_move_is_no_clobber_and_has_null_stdin(tmp_path: Path, env: dict[str, str]) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    new = make_tarball(tmp_path / "v2", "two")
    log = tmp_path / "moves"
    bin_dir = tmp_path / "bin"
    cleanup_marker = tmp_path / "cleanup-started"
    script = new / "install.sh"
    body = script.read_text()
    assert body.count('    CLEANING=1\n') == 1
    script.write_text(body.replace('    CLEANING=1\n', f'    CLEANING=1\n    : > {str(cleanup_marker)!r}\n', 1))
    noninteractive_move_shim(bin_dir, log, ".link.new.", cleanup_marker)
    result = subprocess.run(
        ["/bin/bash", str(new / "install.sh"), "--skip-checks", "--update"], preexec_fn=installer_process_signals,
        env={**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"},
        input="must never reach mv\n", capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) >= 20 and all(s.startswith("True ") for s in calls), calls
    assert any(s.endswith(" forward") for s in calls) and any(s.endswith(" cleanup") for s in calls), calls
    assert (installed_dir(env) / "version.txt").read_text() == "one"


@pytest.mark.parametrize("interrupt", (None, signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
@pytest.mark.parametrize("blocked_step", ("evacuate", "restore"))
@pytest.mark.slow
def test_blocked_recovery_has_a_deadline_and_names_the_real_backup(tmp_path: Path, env: dict[str, str], blocked_step: str, interrupt: signal.Signals | None) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    new = make_tarball(tmp_path / "v2", "two")
    script = new / "install.sh"
    target = installed_dir(env)
    command = ["/bin/bash", str(script), "--skip-checks", "--update"]
    process_env = env
    # Force rollback with all new rows installed, and accelerate the SAME watchdog.
    body = script.read_text().replace("COMMITTED=1\n", "exit 1\n", 1).replace("sleep 5 >/dev/null 2>&1 &", "sleep 1 >/dev/null 2>&1 &", 1)
    script.write_text(body)
    old_inode = target.stat().st_ino
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "blocked-pids"
    real_mv = shutil.which("mv")
    wrapper = bin_dir / "mv"
    wrapper.write_text(
        f'#!{sys.executable}\n'
        'import os, pathlib, signal, subprocess, sys, time\n'
        'source, destination = [a for a in sys.argv[1:] if not a.startswith("-")][-2:]\n'
        f'blocked = ({blocked_step!r} == "restore" and destination == {str(target)!r} and (".backup." in source or ".previous." in source)) or ({blocked_step!r} == "evacuate" and source == {str(target)!r} and (".install." in destination or ".new." in destination))\n'
        'if blocked:\n'
        '    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM): signal.signal(sig, signal.SIG_IGN)\n'
        f'    with open({str(log)!r}, "a") as stream: stream.write(str(os.getpid()) + "\\n")\n'
        '    time.sleep(60)\n'
        f'sys.exit(subprocess.run([{real_mv!r}, *sys.argv[1:]]).returncode)\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)
    adapt_link_shim(bin_dir)
    proc = subprocess.Popen(command, preexec_fn=installer_process_signals, env={**process_env, "PATH": f"{bin_dir}{os.pathsep}{process_env['PATH']}"},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    family = InstallerFamily(proc)
    started = time.monotonic()
    try:
        if interrupt is not None:
            deadline = time.monotonic() + 5
            while not log.exists():
                family.live()
                assert proc.poll() is None and time.monotonic() < deadline
                time.sleep(0.01)
            os.killpg(proc.pid, interrupt)
        output, _ = proc.communicate(timeout=10)
        family.assert_gone()
        assert proc.returncode == 3, output
        assert time.monotonic() - started < 8
        prefix = 'Its backup remains at: '
        lines = [s.removeprefix(prefix) for s in output.splitlines() if s.startswith(prefix)]
        assert len(lines) == 1, output
        backup = Path(lines[0])
        assert backup.stat().st_ino == old_inode
        assert (backup / "version.txt").read_text() == "one"
        assert len(log.read_text().splitlines()) == 2
        for child in map(int, log.read_text().splitlines()):
            with pytest.raises(ProcessLookupError):
                os.kill(child, 0)
    finally:
        family.stop()
        proc.communicate()
        family.assert_gone()


def test_real_mv_never_waits_for_a_read_only_overwrite_prompt(tmp_path: Path, env: dict[str, str]) -> None:
    import pty

    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    new = make_tarball(tmp_path / "v2", "two")
    script = new / "install.sh"
    target = installed_dir(env)
    command = ["/bin/bash", str(script), "--skip-checks", "--update"]
    process_env = env
    desktop = integration_paths(env)["desktop"]
    old_desktop = desktop.read_bytes()
    reached = tmp_path / "restore-reached"
    displaced = tmp_path / "desktop-displaced"
    real_mv = shutil.which("mv")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "mv"
    wrapper.write_text(
        '#!/bin/sh\nsource=""\nfor arg do case "$arg" in -*) ;; *) source="$arg"; break;; esac; done\n'
        'case "$source" in *.link.new.*) exit 1;; esac\n'
        f'[ "$source" != "{desktop}" ] || touch "{displaced}"\n'
        f'case "$source" in *.desktop.backup.*) touch "{reached}"; printf racer > "{desktop}"; chmod 400 "{desktop}";; esac\n'
        f'exec "{real_mv}" "$@"\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)
    adapt_link_shim(bin_dir)
    subprocess.run(["/bin/sh", "-n", str(wrapper)], check=True, capture_output=True)
    master, slave = pty.openpty()
    proc = subprocess.Popen(command, preexec_fn=installer_process_signals, env={**process_env, "PATH": f"{bin_dir}{os.pathsep}{process_env['PATH']}"},
                            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    os.close(slave)
    try:
        output, _ = proc.communicate(timeout=10)
        assert proc.returncode == 3, output
        assert (target / "version.txt").read_text() == "one"
        assert reached.exists() and displaced.exists(), output
        assert desktop.read_bytes() == b"racer"
        backups = list(desktop.parent.glob(".waveguide-generator.desktop.backup.*"))
        assert len(backups) == 1 and backups[0].read_bytes() == old_desktop
        assert "override" not in output.lower()
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        os.close(master)


@pytest.mark.parametrize("row", range(6))
@pytest.mark.parametrize("race", ("recreated_live", "backup_directory"))
def test_each_displacement_reconciles_the_actual_old_object(tmp_path: Path, env: dict[str, str], row: int, race: str) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    paths = [installed_dir(env), *integration_paths(env).values()]
    target = paths[row]
    old_inode = target.lstat().st_ino
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_mv = shutil.which("mv")
    # Force the BSD fallback so a raced backup directory can really nest a row.
    (bin_dir / "mv").write_text(
        '#!/bin/sh\n[ "$1" = "-T" ] && exit 1\n'
        '[ "$1" = "-n" ] && shift\n[ "$1" = "--" ] && shift\n'
        f'if [ "$1" = "{target}" ]; then\n'
        + ('mkdir -p "$2"\n' if race == "backup_directory" else '')
        + f'"{real_mv}" -f "$@" </dev/null || exit $?\n'
        + ('mkdir -p "$1"\necho racer > "$1/keep.txt"\n' if race == "recreated_live" else '')
        + 'exit 0\nfi\n'
        # The old object is recoverable, but simulate restore failure to exercise reporting.
        f'if [ "$2" = "{target}" ]; then\n'
        'case "$1" in *.backup.*|*.previous.*|*.backup.*/*|*.previous.*/*) exit 1;; esac\nfi\n'
        f'exec "{real_mv}" -f "$@" </dev/null\n', encoding="utf-8",
    )
    (bin_dir / "mv").chmod(0o755)
    result = run(make_tarball(tmp_path / "v2", "two"), {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}, "--update")
    assert result.returncode == 3, result.stdout + result.stderr
    lines = [s.removeprefix("Its backup remains at: ") for s in result.stderr.splitlines()
             if s.startswith("Its backup remains at: ")]
    assert len(lines) == 1, result.stderr
    backup = Path(lines[0])
    assert backup.lstat().st_ino == old_inode
    assert (backup.parent == target.parent) == (race == "recreated_live")
    if race == "recreated_live":
        assert (target / "keep.txt").read_text().strip() == "racer"
    else:
        assert backup.name == target.name
        assert not target.exists() and not target.is_symlink()
    assert "Restored the previous installation" not in result.stdout


@pytest.mark.parametrize("row", range(6))
@pytest.mark.parametrize("boundary", ("install_intent", "post_install", "installed"))
@pytest.mark.slow
def test_first_install_interruption_removes_every_new_row(tmp_path: Path, env: dict[str, str], row: int, boundary: str) -> None:
    new = make_tarball(tmp_path / "v2", "two")
    paths = [installed_dir(env), *integration_paths(env).values()]
    paused, release = tmp_path / "paused", tmp_path / "release"
    instrument_boundaries(new / "install.sh", row, boundary, paused, release)
    bin_dir = tmp_path / "bin"
    state_move_shim(bin_dir, paths, row, boundary, paused, release)
    code, output = signal_paused_process(
        ["/bin/bash", str(new / "install.sh"), "--skip-checks", "--no-launch"],
        {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}, paused, release,
    )
    assert code == 1, output
    assert all(not p.exists() and not p.is_symlink() for p in paths)
    assert not list(paths[0].parent.glob(".waveguide-generator.*"))


def test_update_through_a_target_symlink_stages_beside_the_resolved_target(tmp_path: Path, env: dict[str, str]) -> None:
    elsewhere = tmp_path / "elsewhere"
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--prefix", str(elsewhere), "--no-launch").returncode == 0
    actual = elsewhere / "waveguide-generator"
    installed_dir(env).symlink_to(actual, target_is_directory=True)
    log = tmp_path / "application-moves"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_mv = shutil.which("mv")
    (bin_dir / "mv").write_text(
        '#!/bin/sh\nsource=""\nfor arg do case "$arg" in -*) ;; *) source="$arg"; break;; esac; done\n'
        f'case "$source" in *.install.*/waveguide-generator|"{actual}") printf "%s\\n" "$@" >> "{log}";; esac\n'
        f'exec "{real_mv}" "$@"\n', encoding="utf-8",
    )
    (bin_dir / "mv").chmod(0o755)
    result = run(make_tarball(tmp_path / "v2", "two"), {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}, "--update")
    assert result.returncode == 0, result.stdout + result.stderr
    assert installed_dir(env).is_symlink()
    assert (actual / "version.txt").read_text() == "two"
    moves = [Path(s) for s in log.read_text().splitlines() if s.startswith("/")]
    assert len(moves) == 4
    assert moves[1].parent == actual.parent and moves[2].parent.parent == actual.parent
    assert not list(actual.parent.glob(".waveguide-generator.*"))


@pytest.mark.parametrize("artifact", ("desktop", "icon", "desktop_owner", "icon_owner"))
@pytest.mark.parametrize("kind", ("directory", "broken_symlink"))
def test_rollback_preserves_the_exact_preexisting_integration_object(tmp_path: Path, env: dict[str, str], artifact: str, kind: str) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    path = integration_paths(env)[artifact]
    path.unlink()
    if kind == "directory":
        path.mkdir()
        (path / "keep.txt").write_text("old directory")
    else:
        path.symlink_to(tmp_path / "missing")
    old_inode = path.lstat().st_ino
    result = run(make_tarball(tmp_path / "v2", "two"), late_failure_env(tmp_path, env), "--update")
    assert result.returncode == 1, result.stdout + result.stderr
    assert path.lstat().st_ino == old_inode
    if kind == "directory":
        assert list(path.iterdir()) == [path / "keep.txt"]
        assert (path / "keep.txt").read_text() == "old directory"
    else:
        assert path.is_symlink() and os.readlink(path) == str(tmp_path / "missing")
    assert not list(Path(env["HOME"]).rglob("*.backup.*"))
