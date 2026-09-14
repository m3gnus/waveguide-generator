"""Keep Fusion's WGLink in step with the Waveguide Generator that pins it.

WG pins an exact add-in commit in ``integrations/wglink/source.json``, but
nothing ever reconciled that pin with what Fusion actually had. The platform
installer installs the add-in; the in-app updater swaps the app and runtime
layers and never touches it. So a WG that updated itself kept talking to
whatever add-in was last installed by hand, with no version check anywhere and
no way for either side to notice -- and the add-in's own ``adapterVersion``
does not move commit to commit, so it could not have noticed either.

This closes that: compare the commit recorded in the installed add-in's marker
with the pin, and update when they differ. A release ships the pinned package
inside the app layer, so the update is a local file copy and needs no network.

WG always uses the add-in it ships: WG and WGLink speak one delivery version
and WG refuses an older add-in (docs/architecture/CAD-OPERATIONS.md, "Delivery
version"). So WG also installs the shipped package where Fusion has no WGLink
yet, and replaces a WGLink that no Waveguide Generator manages -- one copied in
by hand, from before WG managed it. Both happen only where Fusion is installed
for this user, and only from the verified package this build ships.

Two installs are deliberately left alone, each for its own reason. One synced
by ``dev_sync_wglink.py`` belongs to whoever is editing it. One whose marker
names a different Waveguide Generator root is managed by that installation, and
two WG copies fighting over a single add-in is the failure the marker exists to
prevent. WG still refuses either while it is too old, and says why.

**When the add-in changes** (docs/architecture/CAD-OPERATIONS.md, "WGLink
activation"). Changing Fusion's add-in is *activation*, and it follows the
order an app update needs:

- **Only after this start is confirmed.** A WG update is not final until a
  healthy start commits its transaction. Before that the build can still roll
  back, and an add-in it had changed would stay behind with the older WG. The
  signal is the update journal (:func:`startup_confirmed`): while this
  installation has an open one, nothing changes. A healthy start closes it
  through ``commit_transaction``, which ``launchers/statusapp/healthy_start.py``
  calls for the window, ``--browser`` and ``--no-gui`` launches alike, so the
  signal is the same in every launch mode. The only other way a journal goes
  is recovery removing one it cannot trust once it has rolled back or aborted
  (``launchers/apply_update.py``); the build that then starts is the one
  recovery kept. A launch that cannot confirm its build leaves the journal
  open and the add-in alone, and says so. A source checkout is never replaced
  by the updater and has no journal.
- **Never while Fusion is open, or while WG cannot tell.** A fresh WGLink
  heartbeat, a running process, and a process check that fails
  (:func:`fusion_process_state`) all count as open. The change is recorded as
  pending instead (:func:`pending_path`, outside ``<data>/updates/``; the
  updater never touches it), nothing is created in Fusion's add-ins folder,
  and the status says it is pending until Fusion closes.
- **Decided under the installation lock.** The pass reads the start, Fusion,
  the pin, the build, the pending work and the owner while holding
  ``.WGLink-install.lock`` -- the lock the replacement itself holds. It reads
  the pin and build again just before the installer runs, and the installer
  asks about Fusion, the pin and the build once more immediately before it
  moves anything (``should_proceed``).
- **Stale pending work is discarded.** Pending work from another build, pin,
  installation or add-ins folder is superseded and logged, never installed. A
  build that rolled back or switched channel only ever installs its own pin.
- **Who finishes it.** While WG runs, the Fusion status poll the CAD Link UI
  already makes retries a pending activation once Fusion has closed
  (:func:`poll_activation`), and the startup pass retries it about once a
  minute itself, because that poll stops with no CAD folder, in a hidden page
  and under ``--no-gui``. Every start re-checks it once confirmed. When WG is
  not running nothing is promised: closing Fusion alone installs nothing.

The managed add-in a replacement displaces is kept, with its ownership marker,
for a later supported rollback (:func:`retained_previous_path`).

``WG2_WGLINK_REFRESH=0`` turns activation off; the test suite sets it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType
from typing import Any

from server.cadlink.fusion_status import (
    FUSION_CLOSED,
    FUSION_RUNNING,
    FUSION_UNKNOWN,
    fusion_process_state,
    read_fusion_status,
)
from server.platform.paths import app_root, resolve_data_dir
from server.platform.warmup import DRAIN_TIMEOUT_SECONDS, BackgroundWarmup


log = logging.getLogger("wg.cadlink.addin")

#: Written beside the add-in by ``scripts/dev_sync_wglink.py`` in the add-in
#: repository. Its presence means a person is editing this install.
DEV_MARKER = "wglink_dev.json"
INSTALL_MARKER = "wglink_install.json"
#: ``0`` turns activation off.
REFRESH_ENV = "WG2_WGLINK_REFRESH"

#: Activation state, one folder per installation:
#: ``<data>/integrations/wglink/activation/<installation key>/``. Beside the
#: installer's runtime state and never under ``<data>/updates/``, which the app
#: updater's cleanup owns (UPDATE-TRANSACTION-CONTRACT.md §2.4).
ACTIVATION_SUBDIRECTORY = Path("integrations") / "wglink" / "activation"
PENDING_FILENAME = "pending.json"
PENDING_SCHEMA = 1
PENDING_KIND = "wglink-pending-activation"
RETAINED_SUBDIRECTORY = "previous"
#: Fusion's own record of the scripts and add-ins it loads, one per Autodesk user.
REGISTRY_FILENAME = "JSLoadedScriptsinfo"
_MAX_REGISTRY_BYTES = 4 * 1024 * 1024

#: How long the startup pass waits between looks at the update journal.
CONFIRMATION_POLL_FIRST = 0.5
CONFIRMATION_POLL_MAX = 10.0
#: A failed activation is retried by the status poll no sooner than this.
FAILED_RETRY_SECONDS = 60.0
#: While an activation stays pending, the startup pass tries again this often.
ACTIVATION_RETRY_SECONDS = 60.0

#: The verdicts of a pass that changed Fusion's add-in.
ACTIVATED_VERDICTS = frozenset({"installed", "updated", "replaced"})
_CHANGE_VERDICTS = {"install": "installed", "update": "updated", "replace": "replaced"}
#: What the target was when a change was staged, recorded as its ownership.
_OWNERSHIP = {"install": "absent", "update": "managed", "replace": "unmanaged"}
_BUILD_KEYS = ("version", "commit", "runtimeId")
_NEWS = ACTIVATED_VERDICTS | {"failed", "pending", "superseded", "awaiting-startup"}


@dataclass(frozen=True)
class Activation:
    """One pass's verdict, for tests, the log line and the status the UI polls."""

    verdict: str
    detail: str
    #: Why a pending activation was discarded, when this pass discarded one.
    superseded: str | None = None
    #: How Fusion's own registry lists WGLink (:func:`fusion_registration`).
    registration: dict[str, str] | None = None

    def report(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "detail": self.detail,
            "superseded": self.superseded,
            "registration": self.registration,
            "loadedIdentity": loaded_addin_identity(),
        }


@dataclass(frozen=True)
class _Plan:
    verdict: str
    detail: str
    change: str | None = None
    current: str | None = None


def _installer(root: Path) -> ModuleType:
    path = root / "scripts" / "install_wglink.py"
    spec = importlib.util.spec_from_file_location("wg_install_wglink_runtime", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned_commit(root: Path) -> str | None:
    try:
        payload = json.loads(
            (root / "integrations" / "wglink" / "source.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    commit = payload.get("commit")
    return str(commit) if isinstance(commit, str) and commit else None


def installed_commit(target: Path) -> str | None:
    try:
        payload = json.loads((target / INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    commit = payload.get("sourceCommit")
    return str(commit) if isinstance(commit, str) and commit else None


def _other_manager(target: Path) -> str | None:
    """The Waveguide Generator root another installation's marker names, if any."""

    try:
        payload = json.loads((target / INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("managedBy") != "waveguide-generator":
        return None
    other = payload.get("waveguideGeneratorRoot")
    if not isinstance(other, str) or not other:
        return None
    # A marker naming a WG that is no longer there -- the app was moved, or
    # removed -- manages nothing: the add-in is replaced like an unmanaged one.
    if not (Path(other) / "scripts" / "install_wglink.py").is_file():
        return None
    return other


def _fusion_installed(addins_dir: Path, process_running: bool) -> bool:
    """Whether Fusion is installed for this user: its API folder, or a running process."""

    return Path(addins_dir).expanduser().parent.is_dir() or process_running


def _verified_shipped_package(
    root: Path,
    installer: ModuleType,
    pin: str,
) -> tuple[Path | None, str | None]:
    """Return this build's verified, offline WGLink package.

    First-install is deliberately not allowed to fall back to the source fetch:
    startup must never turn an absent optional add-in into a network operation.
    The platform installer is the consent boundary, and release bundles carry
    the package that it is allowed to install.
    """

    try:
        version_payload = json.loads(
            (root / "shared" / "version.json").read_text(encoding="utf-8")
        )
        if not isinstance(version_payload, Mapping):
            return None, "this build has no valid Waveguide Generator version"
        version = version_payload.get("version")
        if not isinstance(version, str) or not version:
            return None, "this build has no valid Waveguide Generator version"
        archive = Path(installer.shipped_package(root, version, pin))
    except (AttributeError, OSError, UnicodeError, ValueError, TypeError) as exc:
        return None, f"the bundled WGLink package could not be located: {exc}"
    if not archive.is_file():
        return None, f"the bundled WGLink package is missing: {archive.name}"
    try:
        installer.verify_package(archive, root=root)
    except Exception as exc:  # noqa: BLE001 - package verification is a user verdict
        return None, f"the bundled WGLink package failed verification: {exc}"
    return archive, None


# -- Identity and places ------------------------------------------------------


def _normalized(path: object) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _resolved(path: Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(path)


def installation_key(root: Path) -> str:
    """A filename-safe name for one installation: the root its add-in marker names."""

    normalized = _normalized(_resolved(Path(root)))
    return hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def activation_dir(data_dir: Path, root: Path) -> Path:
    return Path(data_dir) / ACTIVATION_SUBDIRECTORY / installation_key(root)


def pending_path(data_dir: Path, root: Path) -> Path:
    """This installation's pending-activation record. The updater never touches it."""

    return activation_dir(data_dir, root) / PENDING_FILENAME


def retained_previous_path(data_dir: Path, root: Path) -> Path:
    """Where the managed WGLink a replacement displaced is kept, marker and all.

    Outside Fusion's add-ins folder on purpose: a second ``WGLink`` there would
    be a second registration, which is the failure that breaks the panel.
    """

    return activation_dir(data_dir, root) / RETAINED_SUBDIRECTORY / "WGLink"


def _declared_version(root: Path) -> str | None:
    try:
        payload = json.loads((root / "shared" / "version.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    version = payload.get("version") if isinstance(payload, Mapping) else None
    return version if isinstance(version, str) and version else None


def build_identity(root: Path) -> dict[str, str | None]:
    """This build's ``(version, commit, runtimeId)``: the contract's interim key.

    ``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §2.3. Read from the app
    layer's own ``APP-MANIFEST.json`` by the reader the updater uses. A source
    checkout has no manifest, so its version comes from ``shared/version.json``
    and the other two stay unknown.
    """

    identity: dict[str, str | None] = {key: None for key in _BUILD_KEYS}
    try:
        from launchers.apply_update import read_build_identity

        identity.update(read_build_identity(Path(root)))
    except Exception:  # noqa: BLE001 - an identity of unknowns, never a crash
        pass
    if identity["version"] is None:
        identity["version"] = _declared_version(Path(root))
    return {key: identity.get(key) for key in _BUILD_KEYS}


_running_builds: dict[str, dict[str, str | None]] = {}
_builds_lock = threading.Lock()


def running_build(root: Path) -> dict[str, str | None]:
    """The build this process started as, captured the first time it is asked.

    The startup pass asks before it waits, so this is the code that loaded.
    """

    key = installation_key(root)
    with _builds_lock:
        if key not in _running_builds:
            _running_builds[key] = build_identity(root)
        return dict(_running_builds[key])


def _describe(build: object) -> str:
    if not isinstance(build, Mapping):
        return "an unknown build"
    text = str(build.get("version") or "an unknown version")
    commit = build.get("commit")
    runtime = build.get("runtimeId")
    if isinstance(commit, str) and commit:
        text += f" ({commit[:12]})"
    if isinstance(runtime, str) and runtime:
        text += f", runtime {runtime}"
    return text


def loaded_addin_identity() -> dict[str, str] | None:
    """The build WGLink says it actually loaded. Always ``None`` for now.

    The seam for CAD Link Phase 4, the handshake: WGLink captures its build
    identity when it loads and reports it with a session ID, and that report,
    not the marker on disk, is what "active" means. Until it exists, an
    activated verdict means only that Fusion's next start loads the copy WG
    installed -- never that Fusion is running it.
    """

    return None


# -- The start being confirmed ------------------------------------------------


def startup_confirmed(
    root: Path,
    data_dir: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Whether this start is confirmed, and why. The signal activation waits for.

    An installed bundle is confirmed once no update transaction is open for it.
    A healthy start closes its journal through ``commit_transaction``, called
    by the one healthy-start settlement every launch mode uses
    (``launchers/statusapp/healthy_start.py``): the window once its native
    loop runs, ``--browser`` on its first ready interface, ``--no-gui`` after
    its self-probe of ``/health`` and the interface. So this is the same
    evidence the rollback decision uses, in every mode, and a mode that cannot
    confirm its build keeps the add-in unchanged rather than skipping the check.
    Recovery also removes a journal it cannot trust
    (``launchers/apply_update.py``), but only once it has rolled back or
    aborted, so the build that then starts is the one recovery kept.

    A source checkout, or a layout the updater cannot address, is never
    replaced by the updater and has no transaction to wait for.
    """

    env = os.environ if environ is None else environ
    if env.get("WG2_BUNDLE") != "1":
        return True, "a source checkout has no update transaction to confirm"
    try:
        from launchers.statusapp.healthy_start import open_transaction, resolve_bundle_paths

        paths = resolve_bundle_paths(env, root, data_dir)
        if paths is None:
            return True, "the updater cannot address this layout, so no update transaction is open"
        _bundle, resources, data = paths
        transaction = open_transaction(data, resources)
    except Exception as exc:  # noqa: BLE001 - unreadable means unconfirmed, never confirmed
        return False, f"this start's update transaction could not be read: {exc}"
    if transaction is not None:
        return False, f"{transaction} has not been confirmed by a healthy start"
    return True, "no update transaction is open for this installation"


# -- The pending record -------------------------------------------------------


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write, flush, then rename, so a reader never sees half a record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with temporary:
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary.name, path)
    except BaseException:
        Path(temporary.name).unlink(missing_ok=True)
        raise


def read_pending(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The pending record at ``path``, or why it cannot be used. ``(None, None)``: none."""

    try:
        if path.is_symlink():
            return None, "its record is a symbolic link"
        if not path.is_file():
            return None, None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        return None, f"its record could not be read: {exc}"
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != PENDING_SCHEMA
        or payload.get("kind") != PENDING_KIND
    ):
        return None, "its record is not one this Waveguide Generator understands"
    build = payload.get("build")
    package = payload.get("package")
    if (
        not isinstance(build, dict)
        or set(build) != set(_BUILD_KEYS)
        or any(value is not None and not isinstance(value, str) for value in build.values())
        or not all(
            isinstance(payload.get(key), str) and payload.get(key)
            for key in ("waveguideGeneratorRoot", "requiredCommit", "target")
        )
        or payload.get("change") not in _CHANGE_VERDICTS
        or (
            package is not None
            and not (
                isinstance(package, dict)
                and isinstance(package.get("path"), str)
                and isinstance(package.get("sha256"), str)
            )
        )
    ):
        return None, "its record is incomplete"
    return payload, None


def _discard_pending(path: Path, reason: str) -> str:
    """Remove superseded pending work, and log why. Returns the report's line."""

    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove the superseded WGLink activation %s: %s", path, exc)
    log.info("WGLink activation superseded -- %s", reason)
    return f"a pending WGLink activation was discarded: {reason}"


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove the finished WGLink activation %s: %s", path, exc)


def _stale_reason(
    record: Mapping[str, Any],
    *,
    root: Path,
    build: Mapping[str, str | None],
    pin: str,
    target: Path,
) -> str | None:
    """Why pending work can no longer be this installation's, or ``None``."""

    if _normalized(record["waveguideGeneratorRoot"]) != _normalized(root):
        return f"it was staged by the Waveguide Generator at {record['waveguideGeneratorRoot']}"
    staged = {key: record["build"].get(key) for key in _BUILD_KEYS}
    if staged != dict(build):
        return f"it was staged by build {_describe(staged)}, and this is {_describe(build)}"
    if record["requiredCommit"] != pin:
        return (
            f"it was staged for WGLink {str(record['requiredCommit'])[:12]}, "
            f"and this build pins {pin[:12]}"
        )
    if _normalized(record["target"]) != _normalized(target):
        return f"it was staged for {record['target']}, and Fusion's WGLink is now {target}"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_entry(archive: Path, root: Path) -> dict[str, str]:
    try:
        name = archive.resolve().relative_to(root).as_posix()
    except ValueError:
        name = str(archive.resolve())
    return {"path": name, "sha256": _sha256(archive)}


def _package_changed(package: Mapping[str, str], root: Path) -> str | None:
    """Why the package staged for activation is no longer the one verified, or ``None``."""

    path = Path(package["path"])
    path = path if path.is_absolute() else root / path
    try:
        if _sha256(path) == package["sha256"]:
            return None
    except OSError:
        return f"the WGLink package staged for activation is missing: {path.name}"
    return f"the WGLink package staged for activation changed after it was verified: {path.name}"


def _stage_pending(
    path: Path,
    existing: Mapping[str, Any] | None,
    *,
    root: Path,
    build: Mapping[str, str | None],
    pin: str,
    target: Path,
    plan: _Plan,
    package: dict[str, str] | None,
) -> None:
    """Record the activation this build owes, keeping when it was first owed."""

    record: dict[str, Any] = {
        "schema": PENDING_SCHEMA,
        "kind": PENDING_KIND,
        "waveguideGeneratorRoot": str(root),
        "installation": installation_key(root),
        "build": dict(build),
        "requiredCommit": pin,
        "target": str(target),
        "ownership": _OWNERSHIP[str(plan.change)],
        "installedCommit": plan.current,
        "change": plan.change,
        "package": package,
        "reason": "Fusion is running",
    }
    if existing is not None:
        unchanged = all(existing.get(key) == value for key, value in record.items())
        if unchanged and isinstance(existing.get("stagedAt"), str):
            return
    record["stagedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json(path, record)


# -- Fusion's own registry, read only -----------------------------------------


def _registered_wglinks(registry: Path) -> list[tuple[Path, object]] | None:
    try:
        if registry.stat().st_size > _MAX_REGISTRY_BYTES:
            return None
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    scripts = payload.get("loadedScripts") if isinstance(payload, Mapping) else None
    if not isinstance(scripts, list):
        return None
    found: list[tuple[Path, object]] = []
    for entry in scripts:
        if not isinstance(entry, Mapping) or entry.get("isRemoved") is True:
            continue
        raw = entry.get("path")
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw)
        folder = path.parent if path.suffix.lower() == ".py" else path
        if entry.get("name") != "WGLink" and folder.name != "WGLink":
            continue
        found.append((folder, entry.get("runOnStartup")))
    return found


def _registration_finding(entries: list[tuple[Path, object]], target: Path) -> dict[str, str]:
    folder_name = target.parent
    if not entries:
        return {
            "state": "unregistered",
            "detail": "Fusion has not registered WGLink yet; it reads the add-in's own "
            "manifest the first time it loads it.",
        }
    if len(entries) > 1:
        return {
            "state": "duplicate",
            "detail": f"Fusion has WGLink registered {len(entries)} times, and two "
            "registrations load two copies that break each other's panel. In Fusion, open "
            "Utilities → Scripts and Add-Ins and keep only the WGLink in "
            f"{folder_name}.",
        }
    folder, run_on_startup = entries[0]
    if _normalized(_resolved(folder)) != _normalized(_resolved(target)):
        return {
            "state": "elsewhere",
            "detail": f"Fusion loads WGLink from {folder}, not from the copy WG manages at "
            f"{target}, so what WG installs is not what Fusion runs. In Fusion, open "
            f"Utilities → Scripts and Add-Ins and keep only the WGLink in {folder_name}.",
        }
    if run_on_startup is not True:
        return {
            "state": "manual",
            "detail": "Fusion's own record does not start WGLink with Fusion, and that "
            "record overrides the add-in's manifest. In Fusion, tick Run on Startup for "
            "WGLink under Utilities → Scripts and Add-Ins.",
        }
    return {"state": "registered", "detail": "Fusion starts the WGLink copy WG manages."}


#: Registry findings that change whether Fusion loads what WG activates.
REGISTRATION_PROBLEMS = frozenset({"duplicate", "elsewhere", "manual"})


def fusion_registration(target: Path) -> dict[str, str] | None:
    """How Fusion's own registry lists WGLink. Read only, and never a verdict.

    Fusion loads add-ins from ``<Fusion>/<user>/JSLoadedScriptsinfo``, and for
    an add-in it already knows, that record's ``runOnStartup`` overrides the
    manifest. One add-in registered twice loads as two modules that break each
    other's panel. WG never edits the file -- Fusion rewrites it from memory
    when it quits, and it is Fusion's -- so what matters for activation is
    reported, not repaired. ``None`` when there is no registry to read, which
    claims nothing.
    """

    base = Path(target).parent.parent.parent
    try:
        registries = sorted(base.glob(f"*/{REGISTRY_FILENAME}"))
    except OSError:
        return None
    findings = [
        _registration_finding(entries, Path(target))
        for registry in registries
        if (entries := _registered_wglinks(registry)) is not None
    ]
    for finding in findings:
        if finding["state"] in REGISTRATION_PROBLEMS:
            return finding
    return findings[0] if findings else None


# -- One activation pass ------------------------------------------------------


def _change_text(plan: _Plan, pin: str, target: Path) -> str:
    if plan.change == "install":
        return f"install WGLink {pin[:12]} at {target}"
    if plan.change == "replace":
        return f"replace a WGLink no Waveguide Generator manages with {pin[:12]}"
    was = plan.current[:12] if plan.current else "an unrecorded commit"
    return f"update WGLink from {was} to {pin[:12]}"


def _activated_text(plan: _Plan, pin: str, installed: Path) -> str:
    later = "Fusion loads it the next time it starts"
    if plan.change == "install":
        return f"installed WGLink {pin[:12]} at {installed}; {later}"
    if plan.change == "replace":
        return f"replaced a WGLink no Waveguide Generator managed with {pin[:12]}; {later}"
    was = plan.current[:12] if plan.current else "an unrecorded commit"
    return f"updated WGLink from {was} to {pin[:12]}; {later}"


def _plan(
    target: Path,
    root: Path,
    installer: ModuleType,
    pin: str,
    *,
    install_absent: bool,
    fusion_installed: Callable[[], bool],
) -> _Plan:
    """What this build's pin asks of Fusion's WGLink as it is now. Reads only."""

    if not (target.exists() or target.is_symlink()):
        if not install_absent:
            return _Plan("absent", "no WGLink is installed for Fusion; installation was not requested")
        if not fusion_installed():
            return _Plan(
                "not-detected",
                "Fusion 360 is not installed for this user; WGLink was not installed",
            )
        return _Plan("installed", "", change="install")
    if target.is_symlink() or not target.is_dir():
        return _Plan("external", f"{target} is an external WGLink target; left alone")
    if (target / DEV_MARKER).is_file():
        return _Plan("developer", f"{target} is a developer sync; left alone")
    if not installer.is_managed_target(target, root):
        other = _other_manager(target)
        if other is not None:
            return _Plan(
                "external",
                f"{target} is managed by another Waveguide Generator at {other}; left alone",
            )
        return _Plan("replaced", "", change="replace")
    current = installed_commit(target)
    if current == pin:
        return _Plan("current", f"WGLink is at the pinned {pin[:12]}")
    return _Plan("updated", "", change="update", current=current)


def _fusion_state_value(value: object) -> str:
    """``True`` or ``running``, ``False`` or ``closed``; anything else cannot tell."""

    if value is True or value == FUSION_RUNNING:
        return FUSION_RUNNING
    if value is False or value == FUSION_CLOSED:
        return FUSION_CLOSED
    return FUSION_UNKNOWN


def _live_fusion_state(data_dir: Path) -> str:
    """Fusion as activation must see it.

    A fresh WGLink heartbeat is open whatever the process check says, and a
    process check that cannot tell is ``unknown`` (:func:`fusion_process_state`),
    which activation treats as open too.
    """

    try:
        heartbeat = read_fusion_status(
            Path(data_dir), current_design_hash="", current_formula="", design_id=None
        )
    except Exception:  # noqa: BLE001 - no heartbeat is not a reason to fail
        heartbeat = {}
    if heartbeat.get("running"):
        return FUSION_RUNNING
    return fusion_process_state()


def _pending_text(plan: _Plan, pin: str, target: Path, state: str) -> str:
    will = f"WG will {_change_text(plan, pin, target)} once Fusion is closed"
    if state == FUSION_UNKNOWN:
        return (
            "WGLink activation is pending until Fusion closes: WG could not tell whether "
            f"Fusion is running, so it treats Fusion as open; {will}"
        )
    return f"WGLink activation is pending until Fusion closes: {will}"


def _moved_under(root: Path, pin: str, build: Mapping[str, str | None]) -> str | None:
    """Why this pass is no longer the installed build's decision, or ``None``."""

    now_pin = pinned_commit(root)
    if now_pin != pin:
        return (
            f"this build's WGLink pin changed to {(now_pin or 'none')[:12]} while WG was "
            f"about to install {pin[:12]}; nothing was replaced"
        )
    now_build = build_identity(root)
    if now_build != dict(build):
        return (
            f"the installed Waveguide Generator changed to {_describe(now_build)} while WG "
            "was about to install WGLink; nothing was replaced, and its next start decides"
        )
    return None


def activate_wglink(
    *,
    root: Path | None = None,
    addins_dir: Path | None = None,
    data_dir: Path | None = None,
    install_absent: bool = True,
    fusion_running: Callable[[], object] | None = None,
    confirmed: Callable[[], tuple[bool, str]] | None = None,
) -> Activation:
    """One activation pass: bring Fusion's WGLink to this build's pin. Never raises.

    An absent add-in is installed from the verified shipped package where
    Fusion is installed for this user; ``install_absent=False`` leaves it
    absent. An explicit ``addins_dir`` is treated as a test/setup override of
    that check. ``fusion_running`` answers ``True`` or ``"running"``, ``False``
    or ``"closed"``, and anything else for "cannot tell"; by default a fresh
    WGLink heartbeat or :func:`fusion_process_state` answers. ``confirmed``
    defaults to :func:`startup_confirmed`.
    """

    try:
        return _activate(
            root=root,
            addins_dir=addins_dir,
            data_dir=data_dir,
            install_absent=install_absent,
            fusion_running=fusion_running,
            confirmed=confirmed,
        )
    except Exception as exc:  # noqa: BLE001 - never let this stop the app starting
        return Activation("failed", f"WGLink activation stopped: {exc}")


def _activate(
    *,
    root: Path | None,
    addins_dir: Path | None,
    data_dir: Path | None,
    install_absent: bool,
    fusion_running: Callable[[], object] | None,
    confirmed: Callable[[], tuple[bool, str]] | None,
) -> Activation:
    root = (root or app_root()).resolve()
    data = resolve_data_dir(data_dir)
    explicit_addins_dir = addins_dir is not None
    is_confirmed = (
        confirmed if confirmed is not None else (lambda: startup_confirmed(root, data))
    )

    def fusion_state() -> str:
        if fusion_running is not None:
            return _fusion_state_value(fusion_running())
        return _live_fusion_state(data)

    try:
        installer = _installer(root)
    except Exception as exc:  # noqa: BLE001 - a WG that cannot read its own installer
        return Activation("unavailable", f"the WGLink installer is not readable: {exc}")

    directory = addins_dir or installer.default_addins_dir(installer._platform_name())
    if directory is None:
        return Activation("unsupported", "Fusion is not supported on this platform")

    ready, why = is_confirmed()
    if not ready:
        return Activation(
            "awaiting-startup", f"WGLink is changed only once this start is confirmed: {why}"
        )

    # The build this process runs, captured the first time anything asks. What
    # is on disk is read, and compared with it, only where it decides.
    started = running_build(root)
    addins = Path(directory).expanduser().resolve()
    target = addins / "WGLink"
    record_file = pending_path(data, root)
    bundled = bool(getattr(installer, "_bundled", lambda: False)())
    superseded: list[str] = []

    def finish(verdict: str, detail: str) -> Activation:
        activation = Activation(verdict, detail, "; ".join(superseded) or None)
        if verdict in {"awaiting-startup", "unsupported"}:
            return activation
        try:
            registration = fusion_registration(target)
        except Exception:  # noqa: BLE001 - a diagnosis, never a reason to fail
            registration = None
        return replace(activation, registration=registration)

    def inputs() -> Activation | tuple[str, dict[str, str | None], dict[str, Any] | None]:
        """The pin, the build and this build's pending work, as they are now."""

        pin = pinned_commit(root)
        if pin is None:
            return finish("unavailable", "this build declares no WGLink pin")
        build = build_identity(root)
        if build != started:
            # Only the build that is installed may decide. If the app on disk
            # is no longer the code this process runs, touch nothing.
            return finish(
                "superseded",
                f"the installed Waveguide Generator is now {_describe(build)}, not "
                f"{_describe(started)}, which this process runs; its next start decides WGLink",
            )
        record, problem = read_pending(record_file)
        if problem is not None:
            superseded.append(_discard_pending(record_file, problem))
        elif record is not None:
            stale = _stale_reason(record, root=root, build=build, pin=pin, target=target)
            if stale is not None:
                superseded.append(_discard_pending(record_file, stale))
                record = None
        return pin, build, record

    def plan_now(pin: str, state: str) -> _Plan:
        return _plan(
            target, root, installer, pin,
            install_absent=install_absent,
            fusion_installed=lambda: explicit_addins_dir
            or _fusion_installed(addins, state == FUSION_RUNNING),
        )

    def unchanged(plan: _Plan, record: Mapping[str, Any] | None) -> Activation:
        if record is not None:
            if plan.verdict in {"external", "developer"}:
                # The owner changed while the work waited. The choice the
                # newly adopted owner made wins over old pending work.
                superseded.append(_discard_pending(record_file, plan.detail))
            else:
                _remove_quietly(record_file)
        return finish(plan.verdict, plan.detail)

    def stage(
        plan: _Plan,
        pin: str,
        build: Mapping[str, str | None],
        record: Mapping[str, Any] | None,
        state: str,
    ) -> Activation:
        """Record the activation this build owes. Nothing in Fusion's folder changes."""

        shipped_only = plan.change in {"install", "replace"}
        package: dict[str, str] | None = None
        if shipped_only or bundled:
            archive, package_error = _verified_shipped_package(root, installer, pin)
            if package_error is not None or archive is None:
                return finish(
                    "unavailable" if shipped_only else "failed",
                    package_error or "the bundled WGLink package is unavailable",
                )
            package = _package_entry(archive, root)
        _stage_pending(
            record_file, record,
            root=root, build=build, pin=pin, target=target, plan=plan, package=package,
        )
        return finish("pending", _pending_text(plan, pin, target, state))

    if not addins.is_dir():
        # No add-ins folder: nothing to recover. Only a first install that runs
        # creates it. One that Fusion may be open for is recorded under the
        # data directory alone, without the lock, which would create both the
        # folder and its lock file.
        read = inputs()
        if isinstance(read, Activation):
            return read
        pin, build, record = read
        state = fusion_state()
        plan = plan_now(pin, state)
        if plan.change is None:
            return unchanged(plan, record)
        if state != FUSION_CLOSED:
            return stage(plan, pin, build, record, state)

    with installer._operation_lock(addins):
        # Everything that decides a replacement is read here, under the lock
        # the replacement itself holds (the updater review §2.5).
        ready, why = is_confirmed()
        if not ready:
            return finish(
                "awaiting-startup", f"WGLink is changed only once this start is confirmed: {why}"
            )
        read = inputs()
        if isinstance(read, Activation):
            return read
        pin, build, record = read
        state = fusion_state()
        if state == FUSION_CLOSED:
            # The platform installer journals the two renames that replace
            # WGLink. Recover before looking at the target: after a process
            # dies just after moving the old target to ``previous``, the target
            # is absent but a perfectly valid managed install is recoverable.
            try:
                installer._recover_install_transaction(addins, root)
            except Exception as exc:  # noqa: BLE001 - startup must never raise
                return finish("failed", f"could not recover the WGLink installation: {exc}")
        plan = plan_now(pin, state)
        if plan.change is None:
            return unchanged(plan, record)
        if state != FUSION_CLOSED:
            return stage(plan, pin, build, record, state)

        # Fusion is closed, this start is confirmed, and this is the lock the
        # replacement holds. Only this build's own package can be installed.
        shipped_only = plan.change in {"install", "replace"}
        kwargs: dict[str, Any] = {
            "root": root,
            "addins_dir": addins,
            "data_dir": data,
            "python": Path(sys.executable),
            "retain_previous": retained_previous_path(data, root),
        }
        if shipped_only:
            # A first install, and replacing an add-in nobody manages, take
            # only the verified package this build ships; never a fetch.
            archive, package_error = _verified_shipped_package(root, installer, pin)
            if package_error is not None or archive is None:
                return finish(
                    "unavailable", package_error or "the bundled WGLink package is unavailable"
                )
            kwargs["archive_path"] = archive
            kwargs["replace_external"] = plan.change == "replace"
        else:
            # A packaged app must never turn activation into a network fetch.
            # Source checkouts retain the existing fetch path.
            kwargs["offline_only"] = bundled
        staged_package = record.get("package") if record is not None else None
        if isinstance(staged_package, Mapping):
            changed = _package_changed(staged_package, root)
            if changed is not None:
                return finish("failed", changed)
        # The last reads before the installer runs; it asks again immediately
        # before it moves anything.
        moved = _moved_under(root, pin, build)
        if moved is not None:
            return finish("superseded", moved)
        deferred: list[tuple[str, str]] = []

        def should_proceed() -> bool:
            now = fusion_state()
            if now != FUSION_CLOSED:
                deferred.append((now, ""))
                return False
            moved_now = _moved_under(root, pin, build)
            if moved_now is not None:
                deferred.append(("moved", moved_now))
                return False
            return True

        kwargs["should_proceed"] = should_proceed
        try:
            status, installed = installer._install_unlocked(**kwargs)
        except Exception as exc:  # noqa: BLE001 - never let this stop the app starting
            if plan.change == "install":
                return finish("failed", f"could not install WGLink to {target}: {exc}")
            return finish("failed", f"could not {_change_text(plan, pin, target)}: {exc}")
        if status == "deferred":
            kind, reason = deferred[-1] if deferred else (FUSION_UNKNOWN, "")
            if kind == "moved":
                return finish("superseded", reason)
            return stage(plan, pin, build, record, kind)
        if status != "installed":
            suffix = " for an absent add-in" if plan.change == "install" else ""
            return finish("failed", f"the WGLink installer returned {status!r}{suffix}")
        _remove_quietly(record_file)
        installed_target = Path(installed) if installed is not None else target
        return finish(plan.verdict, _activated_text(plan, pin, installed_target))


def refresh_wglink(**kwargs: Any) -> tuple[str, str]:
    """:func:`activate_wglink`, as ``(verdict, detail)``."""

    activation = activate_wglink(**kwargs)
    return activation.verdict, activation.detail


# -- Who runs a pass ----------------------------------------------------------

#: What this process last decided, for the status the UI polls.
_report: dict[str, Any] | None = None
#: One pass at a time: the startup pass and the status poll's retry share it.
_pass_lock = threading.Lock()
_retry_not_before = 0.0
_retry_task: asyncio.Task[Any] | None = None
_startup_data_dir: Path | None = None
_startup_waiting = False


def _disabled() -> bool:
    return os.environ.get(REFRESH_ENV, "").strip() == "0"


def _record(activation: Activation) -> None:
    global _report, _retry_not_before
    previous = _report
    _report = activation.report()
    _retry_not_before = (
        time.monotonic() + FAILED_RETRY_SECONDS if activation.verdict == "failed" else 0.0
    )
    if (
        previous is not None
        and previous.get("verdict") == activation.verdict
        and previous.get("detail") == activation.detail
    ):
        return
    # A change, or a reason to act, is news; everything else is the ordinary
    # state of a machine and belongs at debug so a normal start stays quiet.
    (log.info if activation.verdict in _NEWS else log.debug)(
        "WGLink activation: %s -- %s", activation.verdict, activation.detail
    )


def refresh_and_log(data_dir: Path | None = None) -> tuple[str, str]:
    """Run one activation pass and record it for the status the UI polls."""

    if _disabled():
        activation = Activation("disabled", f"{REFRESH_ENV}=0")
        _record(activation)
    else:
        with _pass_lock:
            activation = activate_wglink(data_dir=data_dir)
            # Recorded before the lock is released, so the next pass to take
            # it reads this verdict, not the one it was scheduled on.
            _record(activation)
    return activation.verdict, activation.detail


#: The verdicts each retry is for: the startup pass retries only pending work;
#: the status poll also retries a failure, after its backoff.
_STARTUP_RETRY = frozenset({"pending"})
_POLL_RETRY = frozenset({"pending", "failed"})


def _retry_pass(still: frozenset[str], data_dir: Path | None = None) -> tuple[str, str] | None:
    """A retry pass, run only if the latest verdict is still one of ``still``.

    Checked under the pass lock. A retry woken or scheduled on "pending" must
    not run after another pass has activated: a second pass would find the
    pin current and replace "updated" with "current", and the status saying
    Fusion loads the new copy at its next start would vanish. Returns ``None``
    when it did not run.
    """

    if _disabled():
        return None
    with _pass_lock:
        if (last_refresh() or {}).get("verdict") not in still:
            return None
        activation = activate_wglink(data_dir=data_dir)
        _record(activation)
    return activation.verdict, activation.detail


def last_refresh() -> dict[str, Any] | None:
    """What this process last decided about Fusion's WGLink, or None before it decided."""

    return None if _report is None else dict(_report)


async def _activate_after_confirmed_start() -> None:
    """The startup pass: wait until this start is confirmed, then run.

    While the activation stays pending it runs again about once a minute
    (``ACTIVATION_RETRY_SECONDS``), sharing the pass lock with the status
    poll's retry, and it ends once the work is activated, superseded or
    failed, or when WG stops.
    """

    global _startup_waiting
    data_dir = _startup_data_dir
    if _disabled():
        refresh_and_log(data_dir)
        return
    root = app_root().resolve()
    data = resolve_data_dir(data_dir)
    # Capture the build this process runs before anything is compared with it.
    await asyncio.to_thread(running_build, root)
    delay = CONFIRMATION_POLL_FIRST
    _startup_waiting = True
    try:
        while True:
            ready, why = await asyncio.to_thread(startup_confirmed, root, data)
            if ready:
                break
            _record(
                Activation(
                    "awaiting-startup",
                    f"WGLink is changed only once this start is confirmed: {why}",
                )
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, CONFIRMATION_POLL_MAX)
    finally:
        _startup_waiting = False
    await asyncio.to_thread(refresh_and_log, data_dir)
    # The status poll retries only while the CAD Link UI polls: not with no
    # CAD folder, not in a hidden page, and not under --no-gui with no
    # browser. So this pass keeps a pending activation going itself, slowly.
    while (last_refresh() or {}).get("verdict") == "pending":
        _startup_waiting = True
        try:
            await asyncio.sleep(ACTIVATION_RETRY_SECONDS)
        finally:
            _startup_waiting = False
        if (last_refresh() or {}).get("verdict") != "pending":
            break  # the poll's pass settled it while this one slept
        if _pass_lock.locked() or (_retry_task is not None and not _retry_task.done()):
            continue  # the poll's own pass is running, and it reports
        # Checked once more under the pass lock, which closes the window
        # between the check above and taking it.
        await asyncio.to_thread(_retry_pass, _STARTUP_RETRY, data_dir)


#: The process-wide startup pass. One installed add-in per machine, so one task
#: per process, started at boot and drained at shutdown like every other.
addin_refresh = BackgroundWarmup("wglink-activation", _activate_after_confirmed_start)


async def start_addin_refresh(data_dir: Path | None = None) -> None:
    """Start the startup pass. It waits for this start to be confirmed; startup does not."""

    global _startup_data_dir
    _startup_data_dir = Path(data_dir) if data_dir is not None else None
    await addin_refresh.start()


async def poll_activation(*, fusion_open: bool, data_dir: Path | None = None) -> None:
    """The Fusion status poll's turn: finish a pending activation once Fusion has closed.

    Called by every ``/api/cadlink/fusion-status`` request, which the CAD Link
    UI makes every few seconds while it is open. That existing poll is what
    retries; nothing runs for this on its own. It never waits for the pass --
    the next poll reports it.
    """

    global _retry_task
    if _disabled() or fusion_open:
        return
    report = _report
    if report is None or report.get("verdict") not in {"pending", "failed"}:
        return
    if time.monotonic() < _retry_not_before or _pass_lock.locked():
        return
    if _retry_task is not None and not _retry_task.done():
        return
    _retry_task = asyncio.create_task(
        asyncio.to_thread(_retry_pass, _POLL_RETRY, data_dir), name="wg2-wglink-activation"
    )


async def shutdown_addin_refresh() -> None:
    """Finish an activation pass still running when the server stops.

    Draining rather than cancelling matters here more than for a warmup: a
    pass can be mid-install, and abandoning it would leave a staging directory
    beside the user's add-in (the installer's journal recovers it at the next
    pass). A startup pass that is only waiting -- for its start to be
    confirmed, or between retries of a pending activation -- has nothing to
    drain, so it is cancelled at once.
    """

    global _retry_task
    task = addin_refresh.task
    if task is not None and not task.done() and _startup_waiting:
        task.cancel()
        await asyncio.wait({task})
    await addin_refresh.stop()
    retry, _retry_task = _retry_task, None
    if retry is not None and not retry.done():
        done, _pending = await asyncio.wait({retry}, timeout=DRAIN_TIMEOUT_SECONDS)
        if not done:
            retry.cancel()


__all__ = [
    "ACTIVATED_VERDICTS",
    "Activation",
    "REGISTRATION_PROBLEMS",
    "activate_wglink",
    "addin_refresh",
    "build_identity",
    "fusion_registration",
    "installed_commit",
    "last_refresh",
    "loaded_addin_identity",
    "pending_path",
    "pinned_commit",
    "poll_activation",
    "read_pending",
    "refresh_and_log",
    "refresh_wglink",
    "retained_previous_path",
    "running_build",
    "shutdown_addin_refresh",
    "start_addin_refresh",
    "startup_confirmed",
]
