#!/usr/bin/env python3
"""Read-only, stopped-app evidence for a signed full-installer payload.

This compares artifacts and files. It never installs, extracts, launches, or
claims that native updater lifecycle/security scenarios have been qualified.
Runs with the standard library and the repository's compiled signing trust.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
MAX_JSON = 16 << 20
MAX_MANIFEST = 1 << 20
MAX_ENTRIES = 200_000
PLATFORMS = ("macos-arm64", "windows-x86_64", "linux-x86_64")
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
NATIVE_SCENARIOS_OWED = [
    "actual v0.3.2 to bridge B through the old updater",
    "actual bridge B to next RC through the full-installer updater",
    "native crash/kill, corruption and rollback recovery matrix",
    "live process/mutex and updater/install exclusion",
    "macOS Gatekeeper/quarantine or Windows SmartScreen/installer behavior",
    "WGLink consent, developer ownership and other-installation controls",
]


class EvidenceError(ValueError):
    """Input is unsafe, unstable, malformed, or does not match the candidate."""


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def compiled_verifier(manifest: bytes, signature: bytes, tag: str) -> dict[str, str]:
    # Avoid server.updates.__init__, which imports the web framework. No caller
    # key override: the same compiled key(s) shipped to the client are trusted.
    updates = ROOT / "server" / "updates"
    sys.path.insert(0, str(updates))
    try:
        module = load_module("wg_qualification_manifest", updates / "manifest.py")
        return module.verify_manifest(manifest, signature, tag)
    finally:
        sys.path.remove(str(updates))


def pid_is_running(pid: int) -> bool:
    # Uses OpenProcess/WaitForSingleObject on Windows, rather than os.kill(0).
    return load_module("wg_qualification_instance", ROOT / "server" / "platform" / "instance.py").pid_is_running(pid)


def identity(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def junction(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)) and not stat.S_ISLNK(info.st_mode)


def plain_path(path: Path) -> Path:
    """Reject links/junctions in nominated roots and input-file parents."""
    path = Path(os.path.abspath(path))
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or junction(info):
            raise EvidenceError(f"symlink/junction in nominated path: {cursor}")
    return path


def regular_open(path: Path):
    plain_path(path.parent)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or junction(before):
        raise EvidenceError(f"not a regular file: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    handle = os.fdopen(fd, "rb")
    if identity(before) != identity(os.fstat(handle.fileno())):
        handle.close()
        raise EvidenceError(f"file changed before read: {path}")
    return handle, before


def read_bytes(path: Path, limit: int) -> bytes:
    handle, before = regular_open(path)
    with handle:
        if before.st_size > limit:
            raise EvidenceError(f"file too large: {path}")
        data = handle.read(limit + 1)
        if len(data) > limit or identity(before) != identity(os.fstat(handle.fileno())):
            raise EvidenceError(f"file changed or exceeded limit: {path}")
    if identity(before) != identity(path.lstat()):
        raise EvidenceError(f"file changed after read: {path}")
    return data


def read_object(path: Path, limit: int = MAX_JSON) -> dict:
    try:
        value = json.loads(read_bytes(path, limit))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"JSON must be an object: {path}")
    return value


def hash_file(path: Path) -> dict:
    handle, before = regular_open(path)
    digest = hashlib.sha256()
    with handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
        if identity(before) != identity(os.fstat(handle.fileno())):
            raise EvidenceError(f"file changed while hashing: {path}")
    if identity(before) != identity(path.lstat()):
        raise EvidenceError(f"file changed after hashing: {path}")
    return {"kind": "file", "size": before.st_size, "sha256": digest.hexdigest()}


def safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise EvidenceError("invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("..", ".") or ":" in part for part in value.split("/")):
        raise EvidenceError(f"unsafe relative path: {value}")
    if path.as_posix() != value:
        raise EvidenceError(f"noncanonical relative path: {value}")
    return path


def safe_link(relative: str, target: str) -> None:
    if not isinstance(target, str) or not target or "\\" in target or "\x00" in target or ":" in target:
        raise EvidenceError(f"invalid symlink target at {relative}")
    if PurePosixPath(target).is_absolute():
        raise EvidenceError(f"absolute symlink at {relative}")
    parts = list(PurePosixPath(relative).parent.parts)
    for part in target.split("/"):
        if part == "..":
            if not parts:
                raise EvidenceError(f"escaping symlink at {relative}")
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)


def tree_entries(root: Path, *, exclude_update: bool = False, control: bool = False) -> dict:
    root = plain_path(root)
    if not root.is_dir():
        raise EvidenceError(f"not a directory: {root}")
    result = {}
    observed = {root: identity(root.lstat())}

    def walk(directory: Path):
        before = directory.lstat()
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda item: item.name)
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            safe_relative(relative)
            if exclude_update and relative == "update-install":
                continue
            info = path.lstat()
            observed[path] = identity(info)
            if junction(info):
                raise EvidenceError(f"junction/reparse point: {path}")
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                # Ownership controls may deliberately link to a development
                # checkout. Record their literal target; never follow it.
                if not control:
                    safe_link(relative, target)
                result[relative] = {"kind": "symlink", "target": target}
                if identity(info) != identity(path.lstat()):
                    raise EvidenceError(f"symlink changed: {path}")
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = {"kind": "directory"}
                walk(path)
            elif stat.S_ISREG(info.st_mode):
                result[relative] = hash_file(path)
            else:
                raise EvidenceError(f"special file: {path}")
            if len(result) > MAX_ENTRIES:
                raise EvidenceError("too many tree entries")
        if identity(before) != identity(directory.lstat()):
            raise EvidenceError(f"directory changed while scanning: {directory}")

    walk(root)
    for path, prior in observed.items():
        if prior != identity(path.lstat()):
            raise EvidenceError(f"tree changed while scanning: {path}")
    return result


def stopped(data_dir: Path, pids: list[int], probe: Callable[[int], bool] = pid_is_running) -> None:
    plain_path(data_dir)
    lock = data_dir / "locks" / "server.pid"
    if os.path.lexists(lock):
        info = read_object(lock, 4096)
        pid = info.get("pid")
        if type(pid) is not int or pid <= 0:
            raise EvidenceError("server.pid has no valid PID")
        pids = [*pids, pid]
    for pid in pids:
        if type(pid) is not int or pid <= 0:
            raise EvidenceError("stopped PID must be a positive integer")
        if probe(pid):
            raise EvidenceError(f"declared/server PID is still running: {pid}")


def snapshot(data_dir: Path, external: dict[str, Path], pids: list[int], installed: Path, platform: str) -> dict:
    if platform not in PLATFORMS:
        raise EvidenceError("unsupported platform")
    stopped(data_dir, pids)
    installed = plain_path(installed)
    prior_app = read_object(installed / payload_relative(platform) / "app" / "APP-MANIFEST.json", MAX_MANIFEST)
    prior_version = prior_app.get("version")
    if not isinstance(prior_version, str) or not VERSION.fullmatch(prior_version):
        raise EvidenceError("invalid pre-upgrade version")
    prior = {"root": str(installed), "platform": platform,
             "identity": payload_identity(installed, platform, prior_version),
             "entries": payload_entries(installed, platform)}
    stopped(data_dir, pids)
    roots = {"data": {"path": str(plain_path(data_dir)), "entries": tree_entries(data_dir, exclude_update=True)}}
    for name, path in sorted(external.items()):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name == "data":
            raise EvidenceError("external controls need unique names other than data")
        roots[name] = {"path": str(plain_path(path)), "entries": tree_entries(path, control=True)}
    observed_roots = [installed, *[Path(item["path"]) for item in roots.values()]]
    for index, root in enumerate(observed_roots):
        if any(root == other or root in other.parents or other in root.parents for other in observed_roots[index + 1:]):
            raise EvidenceError("installed/data/control roots must not overlap")
    stopped(data_dir, pids)
    return {"schemaVersion": 1, "createdAt": utc_now(), "stoppedAttested": True,
            "stoppedPids": pids, "dataExclusions": ["update-install"], "roots": roots,
            "beforeInstalled": prior}


def validate_entries(entries) -> None:
    if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
        raise EvidenceError("invalid snapshot entries")
    for name, entry in entries.items():
        safe_relative(name)
        if not isinstance(entry, dict):
            raise EvidenceError("invalid snapshot entry")
        kind = entry.get("kind")
        if kind == "file":
            if set(entry) != {"kind", "size", "sha256"} or type(entry["size"]) is not int or entry["size"] < 0 or not isinstance(entry["sha256"], str) or not HEX64.fullmatch(entry["sha256"]):
                raise EvidenceError("invalid snapshot file")
        elif kind == "symlink":
            if set(entry) != {"kind", "target"} or not isinstance(entry["target"], str):
                raise EvidenceError("invalid snapshot symlink")
        elif kind != "directory" or set(entry) != {"kind"}:
            raise EvidenceError("invalid snapshot entry kind")


def compare(expected: dict, actual: dict, label: str) -> None:
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
    if missing or extra or changed:
        raise EvidenceError(f"{label} differs: missing={missing[:8]}, extra={extra[:8]}, changed={changed[:8]}")


def verify_snapshot(value: dict) -> dict:
    roots = value.get("roots")
    pids = value.get("stoppedPids")
    if value.get("schemaVersion") != 1 or value.get("stoppedAttested") is not True or value.get("dataExclusions") != ["update-install"] or not isinstance(roots, dict) or "data" not in roots or not isinstance(pids, list):
        raise EvidenceError("invalid snapshot contract")
    for name, item in roots.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not isinstance(item, dict) or not isinstance(item.get("path"), str) or not Path(item["path"]).is_absolute():
            raise EvidenceError("invalid snapshot root")
        validate_entries(item.get("entries"))
    prior = value.get("beforeInstalled")
    if not isinstance(prior, dict) or not isinstance(prior.get("root"), str) or not Path(prior["root"]).is_absolute() or prior.get("platform") not in PLATFORMS or not isinstance(prior.get("identity"), dict):
        raise EvidenceError("invalid pre-upgrade payload snapshot")
    validate_entries(prior.get("entries"))
    data = Path(roots["data"]["path"])
    stopped(data, pids)
    for name, item in roots.items():
        compare(item["entries"], tree_entries(Path(item["path"]), exclude_update=name == "data", control=name != "data"), f"preserved {name}")
    stopped(data, pids)
    return {name: {"entryCount": len(item["entries"]), "treeSha256": entries_digest(item["entries"])} for name, item in roots.items()}


def entries_digest(entries: dict) -> str:
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def payload_relative(platform: str) -> str:
    return "Contents/Resources" if platform == "macos-arm64" else "."


def payload_entries(root: Path, platform: str) -> dict:
    """The §6 app/runtime scope, including every manifest and empty directory."""
    result = {}
    for area in ("app", "runtime"):
        prefix = (PurePosixPath(payload_relative(platform)) / area).as_posix()
        result[prefix] = {"kind": "directory"}
        result.update({f"{prefix}/{name}": entry for name, entry in tree_entries(root / prefix).items()})
    return result


def payload_identity(root: Path, platform: str, version: str) -> dict:
    payload = root / payload_relative(platform)
    app = read_object(payload / "app" / "APP-MANIFEST.json", MAX_MANIFEST)
    runtime = read_object(payload / "runtime" / "RUNTIME-MANIFEST.json", MAX_MANIFEST)
    commit = app.get("commit")
    runtime_id = app.get("runtimeId")
    if app.get("schemaVersion") != 1 or app.get("version") != version or not isinstance(commit, str) or not COMMIT.fullmatch(commit) or not isinstance(app.get("treeSha256"), str) or not HEX64.fullmatch(app["treeSha256"]):
        raise EvidenceError("invalid app manifest identity")
    if not isinstance(runtime_id, str) or not runtime_id or len(runtime_id) > 256 or runtime.get("schemaVersion") != 1 or runtime.get("runtimeId") != runtime_id or runtime.get("platform") != platform:
        raise EvidenceError("invalid/mismatched runtime manifest identity")
    source = app.get("sourceCommit")
    if source is not None and (not isinstance(source, str) or not COMMIT.fullmatch(source)):
        raise EvidenceError("invalid app source commit")
    return {"version": version, "commit": commit, "sourceCommit": source, "runtimeId": runtime_id,
            "appManifest": app, "runtimeManifest": runtime}


def linux_archive_entries(artifact: Path) -> dict:
    """Hash tar members in place; do not extract or follow links/hardlinks."""
    result = {}
    handle, before = regular_open(artifact)
    with handle, tarfile.open(fileobj=handle, mode="r|gz") as archive:
        for member in archive:
            name = member.name.rstrip("/")
            safe_relative(name)
            if name in result:
                raise EvidenceError(f"duplicate tar entry: {name}")
            if member.isdir():
                result[name] = {"kind": "directory"}
            elif member.issym():
                safe_link(name, member.linkname)
                result[name] = {"kind": "symlink", "target": member.linkname}
            elif member.isreg():
                digest = hashlib.sha256()
                size = 0
                with archive.extractfile(member) as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        digest.update(chunk)
                        size += len(chunk)
                if size != member.size:
                    raise EvidenceError(f"truncated tar member: {name}")
                result[name] = {"kind": "file", "size": size, "sha256": digest.hexdigest()}
            else:
                raise EvidenceError(f"unsupported tar member (including hardlink/device): {name}")
            if len(result) > MAX_ENTRIES:
                raise EvidenceError("too many tar entries")
        if identity(before) != identity(os.fstat(handle.fileno())):
            raise EvidenceError("artifact changed while reading archive")
    if identity(before) != identity(artifact.lstat()):
        raise EvidenceError("artifact changed after reading archive")
    # Reject an entry underneath a link/file even if it appears earlier/later.
    for name in result:
        for parent in PurePosixPath(name).parents:
            if parent.as_posix() in result and result[parent.as_posix()]["kind"] != "directory":
                raise EvidenceError(f"tar entry under non-directory: {name}")
    prefix = "waveguide-generator/"
    if result.get("waveguide-generator") != {"kind": "directory"}:
        raise EvidenceError("Linux archive lacks waveguide-generator directory")
    payload = {name[len(prefix):]: entry for name, entry in result.items() if name.startswith(prefix)}
    # A link inside this bundle must remain within this bundle, not install.sh.
    for name, entry in payload.items():
        if entry["kind"] == "symlink":
            safe_link(name, entry["target"])
    return payload


def outcome_record(path: Path, from_version: str, version: str) -> dict:
    if not os.path.lexists(path):
        return {"recorded": False, "reason": "no outcome journal; file comparison does not prove updater execution"}
    value = read_object(path, MAX_MANIFEST)
    if value.get("from") != from_version or value.get("to") != version or value.get("result") not in ("ok", "installed", "failed", "rollback_incomplete") or any(not isinstance(value.get(key), str) or not value[key] or len(value[key]) > 8192 for key in ("when", "log")):
        raise EvidenceError("outcome journal identity/shape does not match this transition")
    # Preserve producer spelling and fields; do not infer rollback or retained
    # previous payload from result alone. Never consume/delete this journal.
    return {"recorded": True, "raw": value, "sha256": hash_file(path)["sha256"]}


def verify(*, saved: dict, artifact: Path, manifest: Path, signature: Path, tag: str,
           platform: str, candidate: Path, installed: Path, extraction_method: str,
           from_version: str, removed_app: list[str], removed_runtime: list[str],
           outcome: Path | None = None, require_removals: bool = False,
           verifier: Callable = compiled_verifier) -> dict:
    if platform not in PLATFORMS or not tag.startswith("v") or not VERSION.fullmatch(tag[1:]) or not VERSION.fullmatch(from_version):
        raise EvidenceError("unsupported platform or noncanonical release version/tag")
    if tag.endswith("-updates"):
        raise EvidenceError("companion update-layer tag is not a full-installer release")
    version = tag[1:]
    if not extraction_method.strip() or len(extraction_method) > 4096:
        raise EvidenceError("record the independent extraction method")
    if require_removals and (not removed_app or not removed_runtime):
        raise EvidenceError("nominate both removed .py and runtime-package paths")
    if not all(safe_relative(name).suffix == ".py" for name in removed_app):
        raise EvidenceError("removed app fixture must name a .py module")
    for name in removed_runtime:
        safe_relative(name)
    preserved = verify_snapshot(saved)
    artifact = plain_path(artifact)
    suffix = {"macos-arm64": "macos-arm64.dmg", "windows-x86_64": "windows-x86_64-setup.exe", "linux-x86_64": "linux-x86_64.tar.gz"}[platform]
    if artifact.name != f"Waveguide.Generator-{version}-{suffix}":
        raise EvidenceError("artifact name does not match the full installer/platform/tag")
    manifest_bytes = read_bytes(manifest, MAX_MANIFEST)
    signature_bytes = read_bytes(signature, 64)
    if len(signature_bytes) != 64:
        raise EvidenceError("signature is not 64 raw bytes")
    entries = verifier(manifest_bytes, signature_bytes, tag)
    artifact_hash = hash_file(artifact)
    if entries.get(artifact.name) != artifact_hash["sha256"]:
        raise EvidenceError("artifact does not match signed checksum")
    candidate = plain_path(candidate)
    installed = plain_path(installed)
    if candidate == installed or candidate in installed.parents or installed in candidate.parents:
        raise EvidenceError("candidate and installed roots must be independent")
    for item in saved["roots"].values():
        other = Path(item["path"])
        if any(root == other or root in other.parents or other in root.parents for root in (candidate, installed)):
            raise EvidenceError("payload roots overlap preserved controls/data")
    prior = saved["beforeInstalled"]
    if prior["root"] != str(installed) or prior["platform"] != platform or prior["identity"].get("version") != from_version:
        raise EvidenceError("pre-upgrade installed snapshot does not match this transition/root")
    expected = payload_entries(candidate, platform)
    actual = payload_entries(installed, platform)
    candidate_inodes = set()
    for name, entry in expected.items():
        if entry["kind"] == "file":
            info = (candidate / name).lstat()
            candidate_inodes.add((info.st_dev, info.st_ino))
    for name, entry in actual.items():
        if entry["kind"] == "file":
            info = (installed / name).lstat()
            if info.st_ino and (info.st_dev, info.st_ino) in candidate_inodes:
                raise EvidenceError("golden and installed payloads share a hardlinked file")
    identity_expected = payload_identity(candidate, platform, version)
    identity_actual = payload_identity(installed, platform, version)
    if identity_expected != identity_actual:
        raise EvidenceError("installed app/runtime manifests differ")
    compare(expected, actual, "installed payload")
    binding = {"kind": "owner-attested", "method": extraction_method,
               "limitation": "owner must attest this independently extracted tree came from this exact signed artifact"}
    if platform == "linux-x86_64":
        compare(linux_archive_entries(artifact), tree_entries(candidate), "Linux tar payload versus candidate")
        binding = {"kind": "mechanical-tar-payload", "method": "streamed tar member bytes and literal symlink targets; no extraction"}
    removed = {}
    for area, paths in (("app", removed_app), ("runtime", removed_runtime)):
        for relative in paths:
            before_name = (PurePosixPath(payload_relative(platform)) / area / relative).as_posix()
            before_entry = prior["entries"].get(before_name)
            if not isinstance(before_entry, dict) or before_entry["kind"] != ("file" if area == "app" else "directory"):
                raise EvidenceError(f"removed fixture was not present before in {area}: {relative}")
            target = installed / payload_relative(platform) / area / relative
            golden = candidate / payload_relative(platform) / area / relative
            if os.path.lexists(target) or os.path.lexists(golden):
                raise EvidenceError(f"removed fixture still exists in {area}: {relative}")
        removed[area] = paths
    journal = outcome or Path(saved["roots"]["data"]["path"]) / "update-install" / "outcome.json"
    outcome_value = outcome_record(journal, from_version, version)
    # Recheck preserved roots after the potentially long payload/archive hash.
    if preserved != verify_snapshot(saved):
        raise EvidenceError("preserved snapshot changed during verification")
    if hash_file(artifact) != artifact_hash:
        raise EvidenceError("artifact changed during verification")
    return {"schemaVersion": 1, "createdAt": utc_now(), "classification": "artifact-verification-only",
            "artifactComparisonPassed": True, "nativeQualificationComplete": False,
            "nativeScenariosOwed": NATIVE_SCENARIOS_OWED, "platform": platform,
            "transition": {"from": from_version, "to": version, "tag": tag},
            "artifact": {"name": artifact.name, **artifact_hash},
            "signedManifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "signatureSha256": hashlib.sha256(signature_bytes).hexdigest(),
            "payload": {"scope": "app-and-runtime", "entryCount": len(expected), "treeSha256": entries_digest(expected),
                        "entries": expected, "identity": identity_expected, "binding": binding},
            "removedFixturesAbsent": removed, "removalProofComplete": bool(removed_app and removed_runtime),
            "beforeIdentity": prior["identity"], "beforePayloadTreeSha256": entries_digest(prior["entries"]),
            "preserved": preserved, "outcome": outcome_value}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_new(path: Path, value: dict, protected: list[Path]) -> None:
    parent = plain_path(path.parent)
    destination = parent / path.name
    if any(destination == root or root in destination.parents for root in protected):
        raise EvidenceError("evidence output must be outside the observed roots")
    # New evidence only; do not clobber journals, snapshots, or links.
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="record stopped data and named ownership controls")
    snap.add_argument("--data-dir", type=Path, required=True)
    snap.add_argument("--installed-root", type=Path, required=True)
    snap.add_argument("--platform", choices=PLATFORMS, required=True)
    snap.add_argument("--external", action="append", default=[], metavar="NAME=DIR")
    snap.add_argument("--stopped-pid", action="append", type=int, default=[])
    snap.add_argument("--stopped", action="store_true", required=True, help="attest all WG/helper writers are stopped")
    snap.add_argument("--out", type=Path, required=True)
    check = sub.add_parser("verify", help="compare installed files with independent signed candidate")
    for name in ("snapshot", "artifact", "manifest", "signature", "candidate-root", "installed-root", "out"):
        check.add_argument(f"--{name}", type=Path, required=True)
    for name in ("tag", "from-version", "extraction-method"):
        check.add_argument(f"--{name}", required=True)
    check.add_argument("--platform", choices=PLATFORMS, required=True)
    check.add_argument("--removed-app", action="append", default=[], metavar="RELATIVE.py")
    check.add_argument("--removed-runtime", action="append", default=[], metavar="RELATIVE-PACKAGE")
    check.add_argument("--require-removals", action="store_true", help="B to next RC requires both pre-existing removed fixtures")
    check.add_argument("--outcome", type=Path)
    check.add_argument("--stopped", action="store_true", required=True, help="attest all WG/helper writers are stopped again")
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            external = {}
            for value in args.external:
                name, separator, path = value.partition("=")
                if not separator or name in external:
                    raise EvidenceError("external control must be unique NAME=DIR")
                external[name] = Path(path)
            result = snapshot(args.data_dir, external, args.stopped_pid, args.installed_root, args.platform)
            protected = [args.installed_root.absolute(), *[Path(item["path"]) for item in result["roots"].values()]]
        else:
            saved = read_object(args.snapshot)
            result = verify(saved=saved, artifact=args.artifact, manifest=args.manifest,
                            signature=args.signature, tag=args.tag, platform=args.platform,
                            candidate=args.candidate_root, installed=args.installed_root,
                            extraction_method=args.extraction_method, from_version=args.from_version,
                            removed_app=args.removed_app, removed_runtime=args.removed_runtime,
                            outcome=args.outcome, require_removals=args.require_removals)
            protected = [args.candidate_root.absolute(), args.installed_root.absolute(),
                         *[Path(item["path"]) for item in saved["roots"].values()]]
        write_new(args.out, result, protected)
    except (EvidenceError, OSError, ValueError, EOFError, tarfile.TarError) as exc:
        print(f"qualify_installer_artifact: {exc}", file=sys.stderr)
        return 1
    print(f"Recorded {args.command} evidence: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
