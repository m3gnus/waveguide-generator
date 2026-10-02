"""Restart coordination for the signed full-installer client.

The old UpdateService remains an injectable receiving-bridge seam. New sends
always use this facade and never select an app/runtime layer transaction.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import stat

from .installer_client import InstallerClient, VerifiedInstaller
from .installer_checker import InstallerClientError
from .restart import RestartApproval, RestartRelease, revoke_request
from .service import UpdateBuildNotHeldBack, UpdateChannelUnavailable, UpdateInstallUnavailable


class InstallerUpdateService:
    def __init__(self, *, running_version: str, data_dir: Path, repo_root: Path,
                 update_request_path: Path | None = None, settings=None,
                 restart_approval: RestartApproval | None = None, client=None,
                 **_legacy_options) -> None:
        self.running_version = running_version
        self.data_dir = Path(data_dir).resolve()
        self.update_request_path = Path(update_request_path) if update_request_path is not None else None
        self.restart_approval = restart_approval or RestartApproval()
        self.client = client or InstallerClient(running_version=running_version,
            data_dir=self.data_dir, repo_root=repo_root, settings=settings)
        self._lock = threading.RLock()
        self._published_approval: int | None = None
        self._request_identity: tuple[int, int] | None = None
        self._closed = False
        self.restart_approval.add_release_observer(self._released)

    def start_checker(self) -> None:
        self.client.start_checker()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            unpublished = self._published_approval if self._request_identity is None else None
        # Shutdown for an accepted handoff must leave the published request
        # intact. Its reader owns it now; cancellation only stops new work.
        self.client.close()
        if unpublished is not None:
            self.restart_approval.release("the update service closed before publication", approval=unpublished)

    def channel(self) -> str:
        return self.client.channel()

    def set_channel(self, channel: str) -> str:
        if self.restart_approval.pending is not None:
            raise UpdateChannelUnavailable("An installer handoff is pending.")
        try:
            return self.client.set_channel(channel)
        except InstallerClientError as exc:
            raise UpdateChannelUnavailable(str(exc)) from exc

    def get_status(self, *, force: bool = False) -> dict:
        # Expiry invokes the exact writer's cancellation before taking the
        # progress snapshot, so the first response cannot still report ready.
        pending = self.restart_approval.pending
        result = self.client.get_status(force=force)
        result["restartPending"] = pending
        if self.update_request_path is None or pending is not None:
            result["canInstall"] = False
        return result

    def diagnostic_logs(self) -> dict:
        path = self.data_dir / "update-install" / "install.log"
        result = {"tailBytes": 32 * 1024, "logs": {"install.log": None}}
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                return result
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    return result
                stream.seek(max(0, opened.st_size - 32 * 1024))
                tail = stream.read(32 * 1024).decode("utf-8", "replace")
            after = path.lstat()
            if not stat.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
                return result
            result["logs"]["install.log"] = tail.replace(str(Path.home()), "~")
            return result
        except OSError:
            return result

    def request_install(self) -> dict:
        if self.update_request_path is None:
            raise UpdateInstallUnavailable("This application has no installer handoff owner.")
        if self.restart_approval.pending is not None:
            raise UpdateInstallUnavailable("An installer handoff is already pending.")
        with self._lock:
            if self._closed:
                raise UpdateInstallUnavailable("The update service is shutting down.")
        try:
            return self.client.request_download(self._publish)
        except InstallerClientError as exc:
            raise UpdateInstallUnavailable(str(exc)) from exc

    def reset_download(self) -> dict:
        if self.restart_approval.pending is not None:
            raise UpdateInstallUnavailable("An installer handoff is pending.")
        try:
            return self.client.reset_download()
        except InstallerClientError as exc:
            raise UpdateInstallUnavailable(str(exc)) from exc

    def lift_suppression(self, _build: dict) -> bool:
        # Receiving-bridge retries exist only on explicitly injected legacy
        # services. A new send cannot turn a held app/runtime layer into work.
        raise UpdateBuildNotHeldBack("This installer client has no held layer transaction.")

    def _publish(self, downloaded: VerifiedInstaller) -> None:
        approval = self.restart_approval.approve(downloaded.version)
        temporary: Path | None = None
        try:
            with self._lock:
                if self._closed:
                    raise InstallerClientError("The update service is shutting down.")
                self._published_approval = approval
            # Rehash outside both locks. Status, close and exact-approval release
            # must stay responsive while a large installer is being read.
            verified = self.client.verified_installer()
            self.restart_approval.expire_if_due()
            with self._lock:
                if (self._closed or self._published_approval != approval
                        or verified != downloaded or self.restart_approval.current() != approval):
                    raise InstallerClientError("The installer approval changed before handoff.")
                request = self.update_request_path
                assert request is not None
                payload = {"schemaVersion": 2, "kind": "full_installer",
                    "version": verified.version, "tag": verified.tag,
                    "fromVersion": self.running_version, "platform": verified.platform,
                    "installer": str(verified.path), "installRoot": str(verified.install_root),
                    "size": verified.size, "sha256": verified.sha256,
                    "approval": approval, "readyAtEpoch": time.time() + 0.75,
                    "expiresAtEpoch": time.time() + (self.restart_approval.remaining() or 0)}
                if request.exists() or request.is_symlink():
                    raise InstallerClientError("An earlier update request is still present.")
                fd, filename = tempfile.mkstemp(prefix=".installer-request-", dir=request.parent)
                temporary = Path(filename)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                if self.restart_approval.current() != approval:
                    raise InstallerClientError("The installer approval expired before handoff.")
                # Same-directory hardlink is an atomic no-clobber publication:
                # a concurrently written request is never overwritten.
                def publish() -> None:
                    os.link(temporary, request)
                    info = request.lstat()
                    self._request_identity = (info.st_dev, info.st_ino)
                if not self.restart_approval.publish_if_current(approval, publish):
                    raise InstallerClientError("The installer approval was released before publication.")
        except Exception:
            self.restart_approval.release("the verified installer handoff could not be published", approval=approval)
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _released(self, event: RestartRelease) -> None:
        with self._lock:
            if event.approval != self._published_approval:
                return
            self._published_approval = None
            request = self.update_request_path
            if request is not None and self._request_identity is not None:
                try:
                    info = request.lstat()
                    if (info.st_dev, info.st_ino) == self._request_identity:
                        revoke_request(request)
                except FileNotFoundError:
                    pass
            self._request_identity = None
            self.client.cancel_download(event.reason)
