#!/usr/bin/env python3
"""Validate complete candidate outputs and stage the release signing subjects.

Inputs are the unmodified macos/windows/linux/spa artifact directories. The SPA
is verified but is not a signing subject, matching the release manifest contract.
This helper holds no signing key and performs no network or publishing operation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_manifest import _load  # noqa: E402
from shared import release_assets  # noqa: E402
from shared.safe_names import collision_key, validate_relative_name  # noqa: E402


def _verify_runtime_identity(path: Path, platform: str, runtime_id: str) -> None:
    """Read the root manifest that extract_layer_archive stages for the updater."""
    name = "RUNTIME-MANIFEST.json"
    limit = 64 * 1024
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [entry for entry in archive.infolist() if entry.filename == name]
            if len(entries) != 1:
                raise ValueError(f"Expected exactly one root {name} in {path.name}")
            entry = entries[0]
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (entry.orig_filename != name or entry.is_dir() or mode not in (0, stat.S_IFREG)
                    or entry.file_size > limit):
                raise ValueError(f"Invalid runtime manifest ZIP entry in {path.name}")
            with archive.open(entry) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"Oversized runtime manifest in {path.name}")
            identity = json.loads(data.decode("utf-8"))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f"Invalid runtime archive {path.name}: {exc}") from exc
    if (not isinstance(identity, dict) or type(identity.get("schemaVersion")) is not int
            or identity["schemaVersion"] != 1 or identity.get("runtimeId") != runtime_id
            or identity.get("platform") != platform):
        raise ValueError(f"Runtime archive identity does not match app and platform: {path.name}")


def stage(version: str, root: Path, out: Path, expected_sha: str) -> None:
    """Refuse incomplete or altered inputs before creating a fresh output."""
    if not release_assets.VERSION_RE.fullmatch(version):
        raise ValueError("Invalid candidate version")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("Expected source SHA must be a full lowercase 40-hex commit")
    directories = {"macos", "windows", "linux", "spa"}
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Candidate input root must be a real directory")
    if {path.name for path in root.iterdir()} != directories:
        raise ValueError("Expected exactly macos/windows/linux/spa input directories")
    inventory = {}
    keys = set()
    for directory in sorted(directories):
        folder = root / directory
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError(f"Input directory is not a real directory: {folder}")
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Input is not a regular file: {path}")
            name = validate_relative_name(path.name, what="candidate asset")
            key = collision_key(name)
            if key in keys:
                raise ValueError(f"Duplicate or colliding candidate asset: {name}")
            keys.add(key)
            inventory[(directory, name)] = path

    identity_path = inventory.get(("macos", release_assets.app_manifest_name(version)))
    if identity_path is None:
        raise ValueError("Missing canonical app identity manifest")
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    if not isinstance(identity, dict) or type(identity.get("schemaVersion")) is not int or identity["schemaVersion"] != 1:
        raise ValueError("Invalid app identity schema")
    if identity.get("version") != version:
        raise ValueError("App identity version does not match candidate")
    for field, length in (("runtimeId", 12), ("commit", 40), ("treeSha256", 64)):
        if not isinstance(identity.get(field), str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", identity[field]):
            raise ValueError(f"Invalid app identity {field}")
    if identity["commit"] != expected_sha:
        raise ValueError("App identity commit does not match the workflow source SHA")
    runtime_id = identity["runtimeId"]
    assets = {
        ("spa", release_assets.spa_archive_name(version)),
        ("macos", release_assets.app_layer_name(version)),
        ("macos", release_assets.app_manifest_name(version)),
        ("windows", release_assets.installer_name(release_assets.WINDOWS_PLATFORM, version)),
    }
    for directory, platform in (("macos", release_assets.MACOS_PLATFORM),
                                ("windows", release_assets.WINDOWS_PLATFORM),
                                ("linux", release_assets.LINUX_PLATFORM)):
        assets.add((directory, release_assets.user_download_name(platform, version)))
        assets.add((directory, release_assets.runtime_layer_name(platform, runtime_id)))
    expected = assets | {(directory, release_assets.checksum_name(name)) for directory, name in assets}
    if set(inventory) != expected:
        raise ValueError(f"Candidate inventory mismatch; missing={sorted(expected - inventory.keys())}, "
                         f"unexpected={sorted(inventory.keys() - expected)}")

    manifest = _load()
    digests = {}
    for directory, name in sorted(assets):
        digest = manifest.sha256_file(inventory[(directory, name)])
        sidecar = inventory[(directory, release_assets.checksum_name(name))]
        if sidecar.read_text(encoding="ascii") != f"{digest}  {name}\n":
            raise ValueError(f"Invalid checksum sidecar for {name}")
        digests[name] = digest

    for directory, platform in (("macos", release_assets.MACOS_PLATFORM),
                                ("windows", release_assets.WINDOWS_PLATFORM),
                                ("linux", release_assets.LINUX_PLATFORM)):
        name = release_assets.runtime_layer_name(platform, runtime_id)
        _verify_runtime_identity(inventory[(directory, name)], platform, runtime_id)

    # Never merge a new candidate into stale output from another run.
    out.mkdir(parents=True, exist_ok=False)
    signed = []
    for directory, name in sorted(assets):
        if directory == "spa":
            continue
        destination = out / name
        shutil.copyfile(inventory[(directory, name)], destination)
        manifest.verify_file(digests, name, destination)
        signed.append(destination)
    data = manifest.build_manifest(f"v{version}", signed)
    tag, entries = manifest.parse_manifest(data)
    if tag != f"v{version}" or set(entries) != {path.name for path in signed}:
        raise ValueError("Staged manifest inventory mismatch")
    for name in entries:
        manifest.verify_file(entries, name, out / name)
    (out / "SHA256SUMS").write_bytes(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("stage")
    command.add_argument("--version", required=True)
    command.add_argument("--root", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--expected-sha", required=True)
    args = parser.parse_args(argv)
    try:
        stage(args.version, args.root, args.out, args.expected_sha)
    except (OSError, ValueError) as exc:
        print(f"candidate_manifest: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
