"""Full-installer receiving dispatch, separate from the B/B+1 layer bridge.

Preparation runs while the launcher still owns its Python. The detached
helper uses only the host shell or Inno setup, waits for that launcher's PID,
and never runs Python out of the installation it is replacing.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

from shared.release_assets import LINUX_PLATFORM, MACOS_PLATFORM, WINDOWS_PLATFORM, VERSION_RE, UPDATES_TAG_SUFFIX, is_build_stamp, installer_name, windows_setup_name
from launchers.update_lock import ordinary_path, resolved_path

MAX_REQUEST = 16 * 1024
MAX_ARCHIVE_MEMBERS = 250000
MAX_UNPACKED_BYTES = 8 * 1024**3
MAX_ARCHIVE_PATH_LENGTH = 4096
MAX_ARCHIVE_LINK_HOPS = 40
MAX_ARCHIVE_VIRTUAL_NODES = MAX_ARCHIVE_MEMBERS
MAX_ARCHIVE_VIRTUAL_COMPONENTS = MAX_ARCHIVE_MEMBERS * 16
MAX_ARCHIVE_GRAPH_WORK = MAX_ARCHIVE_MEMBERS * 256


@dataclass(frozen=True)
class FullInstallerRequest:
    version: str
    from_version: str
    installer: Path
    install_root: Path
    platform: str
    sha256: str
    size: int
    expires_at_epoch: float
    approval: int


def _plain_path(path: Path) -> bool:
    return path.is_absolute() and Path(ordinary_path(str(path))) == resolved_path(path) and not path.is_symlink()


def _root(repo_root: Path, platform: str) -> Path:
    app = Path(ordinary_path(str(repo_root.absolute())))
    if not _plain_path(app) or app.name != "app":
        raise ValueError("the launcher is outside an installed app layer")
    if platform == MACOS_PLATFORM:
        if app.parent.name != "Resources" or app.parent.parent.name != "Contents":
            raise ValueError("the launcher is outside a macOS application")
        root = app.parent.parent.parent
        if root.suffix != ".app" or "/AppTranslocation/" in str(root):
            raise ValueError("the macOS destination is unsupported")
    elif platform in (LINUX_PLATFORM, WINDOWS_PLATFORM):
        root = app.parent
    else:
        raise ValueError("the installer platform is unsupported")
    if not _plain_path(root) or not root.is_dir():
        raise ValueError("the installed destination changed")
    return root


def _open_regular(path: Path):
    """Open without following a link or waiting on a raced FIFO/device."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("the installer input is not a regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        opened = os.fstat(descriptor)
        after = path.lstat()
        if (not stat.S_ISREG(opened.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(after.st_mode)):
            raise ValueError("the installer input changed while opening")
        return os.fdopen(descriptor, "rb"), opened
    except BaseException:
        os.close(descriptor)
        raise


def _same_file(path: Path, info) -> bool:
    after = path.lstat()
    return stat.S_ISREG(after.st_mode) and (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)


class InstallerStartBlocked(RuntimeError):
    """The independent full installer has not established a safe startup."""


def guard_full_installer_start(repo_root: Path, *, environ=None) -> None:
    """Early B-app fallback for native entries retained by the layer bridge.

    The macOS C/Linux root launcher may still be the earlier bridge's bytes.
    This runs before desktop/controller imports while the old root is intact.
    It uses the same independent shell entry as the new pre-runtime guard.
    """
    env = dict(os.environ if environ is None else environ)
    if env.get("WG2_BUNDLE") != "1" or sys.platform not in ("darwin", "linux"):
        return
    platform = MACOS_PLATFORM if sys.platform == "darwin" else LINUX_PLATFORM
    try:
        root = _root(Path(repo_root), platform)
    except ValueError:
        return  # source/portable layout has no full-installer owner
    lock = root.parent / ("." + root.name + ".install.lock")
    recovery = root.parent / ("." + root.name + ".installer-recovery")
    candidates = (lock, lock.with_name(lock.name + ".journal"), recovery)
    if not any(path.exists() or path.is_symlink() for path in candidates):
        return
    entry = recovery / "recover.sh"
    try:
        if not _plain_path(recovery) or not recovery.is_dir() or not stat.S_ISREG(entry.lstat().st_mode):
            raise ValueError("the exact external recovery entry is missing or linked")
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        result = subprocess.run(["/bin/sh", str(entry), str(root)], cwd=recovery,
            env=env, stdin=subprocess.DEVNULL, timeout=120, check=False)
        if result.returncode:
            raise ValueError("an installer owner is live or its recovery evidence is ambiguous")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise InstallerStartBlocked(f"Installation cannot start yet: {exc}. Preserve the installer journal and backups.") from exc

def _verify(request: FullInstallerRequest) -> None:
    stream, info = _open_regular(request.installer)
    with stream:
        if info.st_size != request.size:
            raise ValueError("the downloaded installer was replaced or truncated")
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != request.sha256:
        raise ValueError("the downloaded installer checksum changed")
    if not _same_file(request.installer, info):
        raise ValueError("the downloaded installer changed while verifying")


def _unexpired(request: FullInstallerRequest) -> None:
    if time.time() >= request.expires_at_epoch:
        raise ValueError("the installer restart approval expired before helper launch")


def consume_full_installer_request(path: Path, *, repo_root: Path, data_dir: Path,
                                   now: float | None = None) -> tuple[bool, FullInstallerRequest | None]:
    """Return (handled, request); an unready new request never reaches the bridge."""
    # statusapp's package exports its controller; import the receiving error
    # lazily so that the additive controller dispatch does not form a cycle.
    from launchers.statusapp.updater import UpdateHandoffError
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False, None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST:
        try:
            if not path.is_dir():
                path.unlink()
        except OSError:
            pass
        raise UpdateHandoffError("The update request is not a bounded regular file.")
    try:
        stream, opened = _open_regular(path)
        with stream:
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("the request changed while opening")
            raw = stream.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST or not _same_file(path, opened):
            raise ValueError("the request changed or exceeded its size limit")
    except (OSError, ValueError) as exc:
        raise UpdateHandoffError(f"The update request changed while reading: {exc}.") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return False, None  # legacy reader owns malformed bridge requests
    if not isinstance(value, dict) or value.get("kind") != "full_installer":
        return False, None
    def remove() -> None:
        current = path.lstat()
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("the request was replaced while it was read")
        path.unlink()
    try:
        if value.get("schemaVersion") != 2:
            raise ValueError("unsupported installer request schema")
        version, previous = value["version"], value["fromVersion"]
        if (not isinstance(version, str) or VERSION_RE.fullmatch(version) is None
                or version.endswith(UPDATES_TAG_SUFFIX) or is_build_stamp("v" + version)
                or value["tag"] != "v" + version):
            raise ValueError("invalid installer version")
        if not isinstance(previous, str) or len(previous) > 100:
            raise ValueError("invalid previous version")
        platform = value["platform"]
        asset = Path(ordinary_path(str(Path(value["installer"]))))
        root = Path(ordinary_path(str(Path(value["installRoot"]))))
        data = resolved_path(data_dir)
        expected = windows_setup_name(version) if platform == WINDOWS_PLATFORM else installer_name(platform, version)
        if not expected or asset.name != expected or not _plain_path(asset):
            raise ValueError("invalid installer asset path")
        if asset.parent != data / "update-install" / version:
            raise ValueError("the installer is outside its download directory")
        if root != _root(repo_root, platform):
            raise ValueError("the installer request names a different destination")
        if any(ord(ch) < 32 for item in (str(root), str(data), str(asset), previous) for ch in item):
            raise ValueError("installer paths contain control characters")
        size = value["size"]
        checksum = value["sha256"]
        if type(size) is not int or not 0 < size <= 8 * 1024**3 or not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise ValueError("invalid installer proof")
        ready = value.get("readyAtEpoch", 0)
        if type(ready) not in (int, float) or not 0 <= ready < 10**12:
            raise ValueError("invalid installer request delay")
        expiry, approval = value["expiresAtEpoch"], value["approval"]
        if type(expiry) not in (int, float) or not ready < expiry < 10**12 or type(approval) is not int or approval <= 0:
            raise ValueError("invalid installer approval lifetime")
        observed = time.time() if now is None else now
        if observed >= expiry:
            raise ValueError("the installer restart approval expired")
        if ready > observed:
            return True, None
        request = FullInstallerRequest(version, previous, asset, root, platform, checksum, size, float(expiry), approval)
        asset_info = asset.lstat()
        if not stat.S_ISREG(asset_info.st_mode) or asset_info.st_size != size:
            raise ValueError("the installer is not a regular file of the verified size")
        remove()
        return True, request
    except (KeyError, OSError, TypeError, ValueError) as exc:
        try:
            remove()
        except (OSError, ValueError):
            pass
        raise UpdateHandoffError(f"The full installer request is invalid: {exc}.") from exc


def _validate_linux_members(members: list[tarfile.TarInfo]) -> None:
    """Resolve the bounded archive graph without creating any filesystem object."""
    # Physical virtual paths include implicit directories created by extraction.
    nodes: dict[tuple[str, ...], tuple[str, tuple[str, ...], bool]] = {(): ("directory", (), False)}
    stored_components = 0
    work = 0

    def spend(amount: int) -> None:
        nonlocal work
        work += amount
        if work > MAX_ARCHIVE_GRAPH_WORK:
            raise ValueError("the installer archive exceeds its graph budget")

    def store(path: tuple[str, ...], node: tuple[str, tuple[str, ...], bool]) -> None:
        nonlocal stored_components
        if path not in nodes:
            stored_components += len(path)
            if len(nodes) >= MAX_ARCHIVE_VIRTUAL_NODES or stored_components > MAX_ARCHIVE_VIRTUAL_COMPONENTS:
                raise ValueError("the installer archive exceeds its graph budget")
        nodes[path] = node

    def parts(name: str) -> tuple[str, ...]:
        spend(len(name))
        if not name or len(name) > MAX_ARCHIVE_PATH_LENGTH or PurePosixPath(name).is_absolute():
            raise ValueError("the installer archive contains an unsafe path or link")
        return PurePosixPath(name).parts

    def resolve(components: tuple[str, ...], *, follow_final: bool = True,
                directory_links: bool = False) -> tuple[str, ...]:
        pending = deque(components)
        resolved: list[str] = []
        hops = 0
        directory_targets = 0
        while pending:
            spend(len(resolved) + 1)
            component = pending.popleft()
            if component is None:
                if nodes.get(tuple(resolved), (None, (), False))[0] != "directory":
                    raise ValueError("the installer archive traverses a dangling directory link")
                directory_targets -= 1
                continue
            if component == "..":
                if not resolved:
                    raise ValueError("the installer archive link escapes its destination")
                resolved.pop()
                continue
            path = (*resolved, component)
            node = nodes.get(path)
            if directory_targets and node is None:
                raise ValueError("the installer archive traverses a dangling directory link")
            if node and node[0] == "symlink" and (follow_final or pending):
                hops += 1
                if hops > MAX_ARCHIVE_LINK_HOPS:
                    raise ValueError("the installer archive contains cyclic or excessive links")
                # Expand the link before processing a later '..'; normpath here
                # would incorrectly accept a -> '.', x -> 'a/../outside'.
                spend(len(node[1]))
                if directory_links:
                    # A parent symlink cannot create its missing target when
                    # makedirs encounters the already existing dangling link.
                    pending.appendleft(None)
                    directory_targets += 1
                pending.extendleft(reversed(node[1]))
                continue
            resolved.append(component)
            if pending and node and node[0] != "directory":
                raise ValueError("the installer archive path traverses a non-directory")
        return tuple(resolved)

    for member in members:
        name = parts(member.name)
        parent = resolve(name[:-1], directory_links=True)
        for depth in range(1, len(parent) + 1):
            spend(depth)
            path = parent[:depth]
            existing = nodes.get(path)
            if existing and existing[0] != "directory":
                raise ValueError("the installer archive path has a non-directory parent")
            if existing is None:
                store(path, ("directory", (), False))
        path = (*parent, name[-1])
        existing = nodes.get(path)
        if existing and not (member.isdir() and existing[0] == "directory" and not existing[2]):
            raise ValueError("the installer archive repeats a resolved path")
        if member.issym():
            target = parts(member.linkname)
            store(path, ("symlink", target, True))
            resolve(path)
        elif member.islnk():
            # Hardlinks are archive-root relative, not symlink-parent relative.
            # Require an already available regular source (including a previous
            # hardlink); a hardlink to a symlink is not a directory alias.
            target = resolve(parts(member.linkname), follow_final=False, directory_links=True)
            if nodes.get(target, (None, (), False))[0] != "file":
                raise ValueError("the installer archive hardlink lacks a regular target")
            store(path, ("file", (), True))
        else:
            store(path, ("directory" if member.isdir() else "file", (), True))
    # Future symlink declarations can change an earlier target's resolution.
    for path, node in nodes.items():
        if node[0] == "symlink":
            resolve(path)


def _extract_linux(installer: Path, destination: Path) -> None:
    """Validate all paths before extracting any member into a private directory."""
    stream, info = _open_regular(installer)
    with stream, tarfile.open(fileobj=stream, mode="r:gz") as archive:
        members, names = [], set()
        total = 0
        for member in archive:
            if len(members) >= MAX_ARCHIVE_MEMBERS:
                raise ValueError("the installer archive has too many members")
            total += max(0, member.size)
            if total > MAX_UNPACKED_BYTES:
                raise ValueError("the installer archive is too large")
            # Validate every path before extracting any object. Iterating and
            # limiting here also bounds the table itself, not just extraction.
            # Linux tar names use POSIX syntax even when validated on Windows.
            relative = PurePosixPath(member.name)
            if (len(member.name) > MAX_ARCHIVE_PATH_LENGTH or relative.is_absolute()
                    or ".." in relative.parts or not relative.parts):
                raise ValueError("the installer archive contains an unsafe path")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError("the installer archive contains a special file")
            name = "/".join(relative.parts)
            if name in names:
                raise ValueError("the installer archive repeats a path")
            names.add(name)
            tarfile.data_filter(member, str(destination))
            members.append(member)
        _validate_linux_members(members)
        if shutil.disk_usage(destination).free < total + 64 * 1024**2:
            raise ValueError("there is not enough free space to extract the installer")
        archive.extractall(destination, members=members, filter="data")
    if not _same_file(installer, info):
        raise ValueError("the installer archive changed during extraction")
    entry = destination / "install.sh"
    if not entry.is_file() or entry.is_symlink() or not (destination / "waveguide-generator").is_dir():
        raise ValueError("the installer archive lacks its binary installer")


def _sync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def launch_full_installer(repo_root: Path, request: FullInstallerRequest, parent_pid: int,
                          *, data_dir: Path, environ=None, platform_name: str | None = None) -> None:
    """Start an independent helper; raising leaves the launcher able to restart."""
    from launchers.statusapp.updater import UpdateHandoffError
    try:
        install_root = Path(ordinary_path(str(request.install_root)))
        if install_root != _root(repo_root, request.platform):
            raise ValueError("the exact installation destination changed")
        _unexpired(request)
        _verify(request)
        _unexpired(request)
        data = resolved_path(data_dir)
        work = data / "update-install"
        if not _plain_path(work) or not work.is_dir():
            raise ValueError("the helper directory is not a regular directory")
        if (work / "lock").exists() or (work / "lock").is_symlink():
            raise ValueError("an installer helper lock needs review before another update")
        log, outcome = work / "install.log", work / "outcome.json"
        for output in (log, outcome):
            try:
                output_info = output.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(output_info.st_mode):
                raise ValueError("the helper output path is not a regular file")
        env = dict(os.environ if environ is None else environ)
        if request.platform == MACOS_PLATFORM:
            env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        if request.platform == WINDOWS_PLATFORM:
            # Inno itself is the external helper. Its SetupMutex is acquired
            # before WAITPID/renames; no Python from the bundle is launched.
            command = [str(request.installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                f"/DIR={install_root}", f"/WAITPID={parent_pid}", f"/OUTCOME={outcome}", f"/WGLOG={log}", "/RELAUNCH"]
            _unexpired(request)
            subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x00000008 | 0x00000200 | 0x08000000, close_fds=True)
            return
        prepared = Path(tempfile.mkdtemp(prefix="helper-", dir=work))
        recovery: Path | None = None
        recovery_identity: tuple[int, int] | None = None
        try:
            script = prepared / "install-helper.sh"
            shutil.copyfile(repo_root / "launchers" / "installer-helper.sh", script)
            script.chmod(0o700)
            payload = prepared / "payload"
            payload.mkdir(mode=0o700)
            if request.platform == LINUX_PLATFORM:
                _extract_linux(request.installer, payload)
            elif request.platform != MACOS_PLATFORM:
                raise ValueError("no helper exists for this platform")
            journal = request.install_root.parent / ("." + request.install_root.name + ".install.lock.journal")
            metadata = json.dumps({"from": request.from_version, "to": request.version, "log": str(log)}, separators=(",", ":"))[:-1]
            recovery = request.install_root.parent / ("." + request.install_root.name + ".installer-recovery")
            recovery.mkdir(mode=0o700)  # never overwrite a surviving recovery entry
            recovery_info = recovery.lstat()
            recovery_identity = (recovery_info.st_dev, recovery_info.st_ino)
            recovery_entry = recovery / "recover.sh"
            shutil.copyfile(repo_root / "launchers" / "installer-recovery.sh", recovery_entry)
            recovery_entry.chmod(0o700)
            recovery_logger = recovery / "log"
            packaged_logger = repo_root.parent / "runtime" / "bin" / "wg-installer-log"
            if not packaged_logger.is_file() or packaged_logger.is_symlink():
                raise ValueError("the bundled native installer logger is missing or linked")
            shutil.copyfile(packaged_logger, recovery_logger)
            recovery_logger.chmod(0o700)
            root_info = request.install_root.stat()
            config = recovery / "config"
            configuration = ["WG-INSTALL-RECOVERY-1", str(request.install_root),
                f"{root_info.st_dev}:{root_info.st_ino}", str(work), metadata, request.platform,
                env.get("HOME", str(Path.home())), env.get("XDG_DATA_HOME", "")]
            if any("\n" in field or "\r" in field for field in configuration):
                raise ValueError("the recovery configuration contains a control character")
            with config.open("x", encoding="utf-8") as stream:
                stream.write("\n".join(configuration) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            config.chmod(0o600)
            # Persist both configuration and external entry before detachment.
            for durable in (recovery_entry, recovery_logger):
                _sync_file(durable)
            _sync_directory(recovery)
            _unexpired(request)
            subprocess.Popen(["/bin/sh", str(script), request.platform, str(request.installer),
                str(payload), str(request.install_root), str(parent_pid), str(work), metadata,
                request.sha256, json.dumps(str(journal)), str(recovery)],
                cwd=prepared, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        except Exception:
            shutil.rmtree(prepared)
            if recovery is not None and recovery_identity is not None:
                try:
                    current = recovery.lstat()
                    if stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == recovery_identity:
                        shutil.rmtree(recovery)
                except FileNotFoundError:
                    pass
            raise
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise UpdateHandoffError(f"The full installer helper could not start: {exc}.") from exc
