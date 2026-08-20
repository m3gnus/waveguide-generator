#!/usr/bin/env python3
"""Build the pinned, source-preserving WGLink release payload."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_SPEC = REPO_ROOT / "integrations" / "wglink" / "source.json"
VERSION_FILE = REPO_ROOT / "shared" / "version.json"
ARCHIVE_ROOT = "wglink"
SOURCE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
RUNTIME_CONTRACT = 'PACKAGED_RUNTIME_FILE = "wglink_runtime.json"'
FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


class PackageError(RuntimeError):
    """The requested source is not the reviewed WGLink payload."""


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"Could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"{label} must be a JSON object: {path}")
    return value


def source_spec(path: Path = SOURCE_SPEC) -> dict[str, object]:
    value = _load_json(path, "WGLink source specification")
    if value.get("schema") != 1:
        raise PackageError("WGLink source specification must use schema 1")
    commit = value.get("commit")
    if not isinstance(commit, str) or SOURCE_COMMIT_RE.fullmatch(commit) is None:
        raise PackageError("WGLink source specification needs one full Git commit")
    for name in ("repository", "license", "addinVersion"):
        if not isinstance(value.get(name), str) or not str(value[name]).strip():
            raise PackageError(f"WGLink source specification has no {name}")
    return value


def declared_version(path: Path = VERSION_FILE) -> str:
    value = _load_json(path, "Waveguide Generator version")
    version = value.get("version")
    if not isinstance(version, str) or not version:
        raise PackageError(f"{path} has no version")
    return version


def _git_head(source_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
    )
    head = completed.stdout.strip().lower()
    if completed.returncode != 0 or SOURCE_COMMIT_RE.fullmatch(head) is None:
        raise PackageError(
            f"Could not identify the hornlab-fusion-addin checkout at {source_root}"
        )
    return head


def _source_files(source_root: Path) -> dict[str, Path]:
    addin = source_root / "fusion-addins" / "WGLink"
    resampler = source_root / "scripts" / "wglink_resample.py"
    license_file = source_root / "LICENSE"
    required = (
        addin / "WGLink.py",
        addin / "WGLink.manifest",
        addin / "wglink_core.py",
        resampler,
        license_file,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise PackageError("WGLink source is incomplete: " + ", ".join(missing))
    if RUNTIME_CONTRACT not in (addin / "wglink_core.py").read_text(encoding="utf-8"):
        raise PackageError(
            "WGLink source does not implement the packaged-runtime contract pinned by WG"
        )

    files: dict[str, Path] = {}
    for path in sorted(addin.rglob("*")):
        if path.is_symlink():
            raise PackageError(f"WGLink source contains a symlink: {path}")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(addin).as_posix()
        files[f"{ARCHIVE_ROOT}/fusion-addins/WGLink/{relative}"] = path
    files[f"{ARCHIVE_ROOT}/scripts/wglink_resample.py"] = resampler
    files[f"{ARCHIVE_ROOT}/LICENSE"] = license_file
    return files


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_entry(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, data


def build_package(
    source_root: Path,
    output: Path,
    *,
    spec: dict[str, object] | None = None,
    version: str | None = None,
    observed_commit: str | None = None,
) -> Path:
    source_root = source_root.resolve()
    spec = source_spec() if spec is None else dict(spec)
    version = declared_version() if version is None else version
    observed_commit = (observed_commit or _git_head(source_root)).lower()
    expected_commit = str(spec.get("commit", "")).lower()
    if observed_commit != expected_commit:
        raise PackageError(
            "Refusing to package an unpinned hornlab-fusion-addin checkout: "
            f"expected {expected_commit}, found {observed_commit}"
        )

    manifest = _load_json(
        source_root / "fusion-addins" / "WGLink" / "WGLink.manifest",
        "WGLink manifest",
    )
    if manifest.get("version") != spec.get("addinVersion"):
        raise PackageError(
            "WGLink manifest version does not match integrations/wglink/source.json"
        )

    source_files = _source_files(source_root)
    members = {name: path.read_bytes() for name, path in source_files.items()}
    provenance = {
        "schema": 1,
        "package": "waveguide-generator/WGLink",
        "waveguideGeneratorVersion": version,
        "sourceRepository": spec["repository"],
        "sourceCommit": expected_commit,
        "sourceLicense": spec["license"],
        "addinVersion": spec["addinVersion"],
        "files": {name: _sha256(data) for name, data in sorted(members.items())},
    }
    provenance_data = (
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w") as archive:
            for name, data in sorted(members.items()):
                archive.writestr(*_zip_entry(name, data))
            archive.writestr(
                *_zip_entry(f"{ARCHIVE_ROOT}/provenance.json", provenance_data)
            )
        temporary_path.replace(output)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = build_package(args.source_root, args.output)
    except (OSError, PackageError, subprocess.SubprocessError) as exc:
        print(f"Could not build WGLink package: {exc}")
        return 2
    print(f"Built {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
