"""Complete synthetic candidate artifacts shared by signing and frozen-client tests."""
import hashlib
import io
import json
from pathlib import Path
import zipfile
from shared import release_assets as names


VERSION = "0.3.6"
RUNTIME = "0123456789ab"
SOURCE_SHA = "a" * 40
ASSETS = (
    ("spa", names.spa_archive_name(VERSION)),
    ("macos", names.app_layer_name(VERSION)),
    ("macos", names.app_manifest_name(VERSION)),
    ("macos", names.user_download_name(names.MACOS_PLATFORM, VERSION)),
    ("windows", names.user_download_name(names.WINDOWS_PLATFORM, VERSION)),
    ("windows", names.installer_name(names.WINDOWS_PLATFORM, VERSION)),
    ("linux", names.user_download_name(names.LINUX_PLATFORM, VERSION)),
    *((directory, names.runtime_layer_name(platform, RUNTIME)) for directory, platform in (
        ("macos", names.MACOS_PLATFORM), ("windows", names.WINDOWS_PLATFORM), ("linux", names.LINUX_PLATFORM))),
)


def _write(path, data):
    path.write_bytes(data)
    path.with_name(names.checksum_name(path.name)).write_text(
        f"{hashlib.sha256(data).hexdigest()}  {path.name}\n", encoding="ascii")


def _runtime_zip(platform, runtime_id=RUNTIME):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("RUNTIME-MANIFEST.json", json.dumps({
            "schemaVersion": 1, "runtimeId": runtime_id, "platform": platform,
        }))
    return buffer.getvalue()


def make_inputs(root: Path, version: str = VERSION):
    for directory in ("spa", "macos", "windows", "linux"):
        (root / directory).mkdir(parents=True)
    for directory, original in ASSETS:
        name = original.replace(VERSION, version)
        data = name.encode()
        if name == names.app_manifest_name(version):
            data = json.dumps({"schemaVersion": 1, "version": version, "runtimeId": RUNTIME,
                               "commit": SOURCE_SHA, "treeSha256": "b" * 64}).encode()
        if name.startswith("update-runtime-"):
            platform = {"macos": names.MACOS_PLATFORM, "windows": names.WINDOWS_PLATFORM,
                        "linux": names.LINUX_PLATFORM}[directory]
            data = _runtime_zip(platform)
        _write(root / directory / name, data)
    return root
