"""Installed 0.3.4 and 0.3.5 clients still discover this build's full installers.

Those clients find an update with their own ``installer_checker`` code. This
runs that code, extracted from the release tags, against a release shaped the
way this checkout's ``release.yml`` publishes one: the manifest comes from the
workflow's own signing step, and the public release holds exactly the three
installers plus ``SHA256SUMS`` and ``SHA256SUMS.sig``. No ``update-app`` or
``update-runtime`` layer exists any more; the released checkers must not need one.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from server.tests._frozen_installers import ROOT, extract_release
from server.tests.test_updates_signing import PUBLIC_HEX, SEED, _sign
from server.updates import manifest
from shared import release_assets

RELEASE_ROOT = "https://github.com/m3gnus/waveguide-generator/releases/download"


def _workflow_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str) -> bytes:
    """Run the release workflow's signing-inventory code over every built asset."""
    jobs = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))["jobs"]
    run = next(step["run"] for step in jobs["sign"]["steps"]
               if step.get("name") == "Write and sign SHA256SUMS, then verify it")
    code = run.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    work = tmp_path / "sign"
    (work / "shared").mkdir(parents=True)
    (work / "shared" / "version.json").write_text(json.dumps({"version": version}))
    # Everything the build jobs hand the signer, including the SPA's sidecar.
    built = {
        "spa": [release_assets.spa_archive_name(version), release_assets.checksum_name(release_assets.spa_archive_name(version))],
        "macos": [release_assets.installer_name(release_assets.MACOS_PLATFORM, version)],
        "windows": [release_assets.windows_setup_name(version),
                    release_assets.installer_name(release_assets.WINDOWS_PLATFORM, version)],
        "linux": [release_assets.installer_name(release_assets.LINUX_PLATFORM, version)],
    }
    for job, names in built.items():
        (work / "build/sign-inputs" / job).mkdir(parents=True)
        for name in names:
            (work / "build/sign-inputs" / job / name).write_bytes(name.encode())

    def write_manifest(command, **kwargs):
        Path(command[6]).write_bytes(manifest.build_manifest(command[4], [Path(path) for path in command[7:]]))

    with monkeypatch.context() as patch:
        patch.chdir(work)
        patch.setenv("RELEASE_TAG", "v" + version)
        patch.setattr(subprocess, "run", write_manifest)
        exec(compile(code, "release signing inventory", "exec"), {})
    data = (work / "build/signed/SHA256SUMS").read_bytes()
    _, entries = manifest.parse_manifest(data)
    assert not any(name.startswith(("update-app-", "update-runtime-")) for name in entries)
    return data


_CHECK = r'''
import io, json, sys, types
frozen, fixture_path, test_key, channel, version = sys.argv[1:]
sys.path.insert(0, frozen)
# The released package __init__ mounts the whole API. Only the checker and
# what it imports run on a client during discovery, so skip that __init__ and
# load those modules from the released tree.
package = types.ModuleType("server.updates")
package.__path__ = [frozen + "/server/updates"]
sys.modules["server.updates"] = package
from server.updates import installer_checker, manifest
from shared import release_assets
for module in (installer_checker, manifest, release_assets):
    assert module.__file__.startswith(frozen), module.__file__
fixture = json.load(open(fixture_path))
# The released build trusts exactly the production key this build signs with.
assert list(manifest.UPDATE_SIGNING_PUBLIC_KEYS_HEX) == fixture["keys"]
manifest.UPDATE_SIGNING_PUBLIC_KEYS_HEX = (test_key,)

class Stream(io.BytesIO):
    def geturl(self):
        return self.url

def opener(request, **kwargs):
    result = Stream(bytes.fromhex(fixture["files"][request.full_url.rsplit("/", 1)[-1]]))
    result.url = request.full_url
    return result

for platform in (release_assets.MACOS_PLATFORM, release_assets.WINDOWS_PLATFORM, release_assets.LINUX_PLATFORM):
    result = installer_checker.eligible_release(fixture["payload"], channel=channel, platform_name=platform, opener=opener)
    assert result is not None, platform
    assert result[0]["installer"]["name"] == release_assets.user_download_name(platform, version), result
# The companion is never offered as an update.
fixture["payload"]["tag_name"] += "-updates"
assert installer_checker.eligible_release(fixture["payload"], channel=channel, platform_name=None, opener=opener) is None
print("ok")
'''


@pytest.mark.parametrize("tag", ["v0.3.4", "v0.3.5"])
@pytest.mark.parametrize("version,channel", [("0.3.6", "stable"), ("0.3.6-rc.1", "beta")])
def test_released_checkers_accept_full_installer_only_release(tmp_path, monkeypatch, tag, version, channel):
    frozen = extract_release(tag, tmp_path / "released")
    sums = _workflow_manifest(tmp_path, monkeypatch, version)
    files = {name: name.encode() for name in release_assets.user_download_names(version)}
    files["SHA256SUMS"], files["SHA256SUMS.sig"] = sums, _sign(SEED, sums)
    _, entries = manifest.parse_manifest(sums)
    for name in release_assets.user_download_names(version):
        assert entries[name] == hashlib.sha256(files[name]).hexdigest()
    payload = {"tag_name": "v" + version, "draft": False, "prerelease": "-" in version, "body": "Release notes",
               "assets": [{"name": name, "state": "uploaded", "size": len(data),
                           "browser_download_url": f"{RELEASE_ROOT}/v{version}/{name}"}
                          for name, data in files.items()]}
    fixture = tmp_path / "release.json"
    fixture.write_text(json.dumps({"payload": payload, "files": {k: v.hex() for k, v in files.items()},
                                   "keys": list(manifest.UPDATE_SIGNING_PUBLIC_KEYS_HEX)}))
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    result = subprocess.run([sys.executable, "-I", "-c", _CHECK, str(frozen), str(fixture), PUBLIC_HEX, channel, version],
                            cwd=frozen, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stdout + result.stderr
