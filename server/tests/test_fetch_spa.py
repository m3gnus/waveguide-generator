"""The prebuilt-SPA download path, exercised for real against local fixtures.

No release tag exists yet, so this cannot be pointed at a published artifact.
It is pointed at a ``file://`` directory shaped exactly like one instead --
``<asset>.tar.gz`` beside ``<asset>.tar.gz.sha256`` in ``sha256sum`` format --
which exercises the same fetch, parse, verify, and extract code the network
path uses. What is *not* covered here is HTTPS itself and GitHub's redirect to
the asset CDN.

The install target deliberately sits under a parent directory containing a
space and a parenthesis. R1-P1-7 exists because v1 has live bugs from exactly
that, and a test whose paths are all tidy would never have caught them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
from types import ModuleType

import pytest

from scripts.frontend_freshness import frontend_freshness, vite_executable


ROOT = Path(__file__).resolve().parents[2]
PARENT_WITH_SPACES = "Hornlab - Workspace (beta)"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wg2_fetch_spa", ROOT / "scripts" / "fetch_spa.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_spa = _load()


def _archive_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as handle:
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            handle.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _default_entries() -> dict[str, bytes]:
    return {
        "dist/index.html": b"<!doctype html><title>wg2</title>",
        "dist/assets/app.js": b"export const version = 1;\n",
    }


@pytest.fixture
def release(tmp_path: Path):
    """A directory shaped like a published GitHub release asset pair."""

    directory = tmp_path / "release"
    directory.mkdir()

    def publish(version: str = "2.0.0", entries: dict[str, bytes] | None = None, *, checksum: str | None = None):
        name = fetch_spa.asset_name(version)
        payload = _archive_bytes(_default_entries() if entries is None else entries)
        archive = directory / name
        archive.write_bytes(payload)
        digest = checksum if checksum is not None else hashlib.sha256(payload).hexdigest()
        (directory / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
        return archive

    publish.directory = directory  # type: ignore[attr-defined]
    publish.url = directory.as_uri()  # type: ignore[attr-defined]
    return publish


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A checkout under a parent path with a space and a parenthesis in it."""

    root = tmp_path / PARENT_WITH_SPACES / "waveguide-generator-v2"
    (root / "shared").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "shared" / "version.json").write_text('{"version": "2.0.0"}\n', encoding="utf-8")
    return root


def _install(checkout: Path, release, *extra: str) -> int:
    return fetch_spa.main(["--root", str(checkout), "--base-url", release.url, *extra])


def test_installs_a_verified_release_into_a_path_with_spaces(checkout, release, capsys):
    release()
    assert _install(checkout, release) == 0

    dist = checkout / "frontend" / "dist"
    assert (dist / "index.html").read_bytes() == _default_entries()["dist/index.html"]
    assert (dist / "assets" / "app.js").is_file()

    stamp = json.loads((dist / fetch_spa.STAMP_NAME).read_text(encoding="utf-8"))
    assert stamp["version"] == "2.0.0"
    assert stamp["sha256"] == hashlib.sha256((release.directory / fetch_spa.asset_name("2.0.0")).read_bytes()).hexdigest()
    assert "Checksum verified" in capsys.readouterr().out


def test_refuses_to_extract_an_archive_whose_checksum_does_not_match(checkout, release, capsys):
    release(checksum="0" * 64)

    assert _install(checkout, release) == 2

    captured = capsys.readouterr()
    assert "REFUSING TO EXTRACT" in captured.err
    assert "does not match its published checksum" in captured.err
    # Refusing has to mean nothing was written, not that a warning was printed.
    assert not (checkout / "frontend" / "dist").exists()


def test_a_tampered_archive_is_refused_even_though_its_own_bytes_are_intact(checkout, release):
    """The digest has to be compared against the *published* one, not recomputed."""

    release()
    name = fetch_spa.asset_name("2.0.0")
    (release.directory / name).write_bytes(
        _archive_bytes({"dist/index.html": b"<script>everything is fine</script>"})
    )

    assert _install(checkout, release) == 2
    assert not (checkout / "frontend" / "dist").exists()


def test_an_existing_interface_survives_a_failed_reinstall(checkout, release):
    release()
    assert _install(checkout, release) == 0
    dist = checkout / "frontend" / "dist"
    (dist / "index.html").write_bytes(b"the build that was already working")

    release(version="2.0.1", checksum="f" * 64)
    (checkout / "shared" / "version.json").write_text('{"version": "2.0.1"}\n', encoding="utf-8")
    assert _install(checkout, release) == 2

    assert (dist / "index.html").read_bytes() == b"the build that was already working"
    assert not (checkout / "frontend" / fetch_spa.STAGING_NAME).exists()
    assert not (checkout / "frontend" / fetch_spa.PREVIOUS_NAME).exists()


@pytest.mark.parametrize(
    "member",
    ["../evil.sh", "/etc/evil.sh", "dist/../../evil.sh", "evil.sh", "._dist"],
)
def test_refuses_members_outside_dist(checkout, release, member, capsys):
    release(entries={"dist/index.html": b"ok", member: b"payload"})

    assert _install(checkout, release) == 2
    assert "REFUSING TO EXTRACT" in capsys.readouterr().err
    assert not (checkout / "frontend" / "dist").exists()
    assert not (checkout.parent / "evil.sh").exists()
    assert not (checkout / "frontend" / fetch_spa.STAGING_NAME).exists()


def test_refuses_an_archive_with_no_index_html(checkout, release, capsys):
    """release.yml has the same guard; an installer that trusted it would still ship nothing."""

    release(entries={"dist/assets/app.js": b"console.log(1)"})

    assert _install(checkout, release) == 2
    assert "no dist/index.html" in capsys.readouterr().err
    assert not (checkout / "frontend" / "dist").exists()


def test_a_missing_release_is_an_actionable_message_not_a_traceback(checkout, release, capsys):
    assert _install(checkout, release) == 2

    error = capsys.readouterr().err
    assert "No release asset was found" in error
    assert "has not been published yet" in error
    assert "npm run build" in error
    assert "Traceback" not in error


def test_a_checksum_file_for_a_different_asset_does_not_validate(checkout, release, capsys):
    release()
    name = fetch_spa.asset_name("2.0.0")
    digest = hashlib.sha256((release.directory / name).read_bytes()).hexdigest()
    (release.directory / f"{name}.sha256").write_text(
        f"{digest}  waveguide-generator-v2-spa-1.9.9.tar.gz\n", encoding="utf-8"
    )

    assert _install(checkout, release) == 2
    assert "different release asset" in capsys.readouterr().err


def test_reinstalling_the_same_version_downloads_nothing(checkout, release, capsys):
    release()
    assert _install(checkout, release) == 0
    capsys.readouterr()

    assert _install(checkout, release) == 0
    assert "already installed" in capsys.readouterr().out

    assert _install(checkout, release, "--force") == 0
    assert "Checksum verified" in capsys.readouterr().out


def test_check_reports_a_locally_built_dist_as_unrecorded(checkout, release, capsys):
    dist = checkout / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("built by npm run build", encoding="utf-8")

    assert fetch_spa.main(["--root", str(checkout), "--check"]) == 1
    assert "an unrecorded build" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("stamp", "message"),
    [
        ({"version": "1.9.9", "sha256": "a" * 64}, "not 2.0.0"),
        ({"version": "2.0.0"}, "no valid verified-archive checksum"),
        (
            {"version": "2.0.0", "sha256": "not-a-sha256"},
            "no valid verified-archive checksum",
        ),
    ],
)
def test_check_rejects_stale_or_unverified_stamps(checkout, capsys, stamp, message):
    dist = checkout / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("existing interface", encoding="utf-8")
    (dist / fetch_spa.STAMP_NAME).write_text(json.dumps(stamp), encoding="utf-8")

    assert fetch_spa.main(["--root", str(checkout), "--check"]) == 1
    assert message in capsys.readouterr().err


def test_check_accepts_a_matching_checksum_verified_stamp(checkout, release, capsys):
    release()
    assert _install(checkout, release) == 0
    capsys.readouterr()

    assert fetch_spa.main(["--root", str(checkout), "--check"]) == 0
    assert "SPA 2.0.0 is installed" in capsys.readouterr().out


def test_normal_install_does_not_trust_a_version_only_stamp(checkout, release, capsys):
    dist = checkout / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("old interface", encoding="utf-8")
    (dist / fetch_spa.STAMP_NAME).write_text(
        json.dumps({"version": "2.0.0"}), encoding="utf-8"
    )

    assert _install(checkout, release) == 2
    captured = capsys.readouterr()
    assert "No release asset was found" in captured.err
    assert "already installed" not in captured.out


def test_a_local_archive_still_has_to_prove_itself(tmp_path, checkout, release, capsys):
    archive = release()
    orphan = tmp_path / "downloads" / archive.name
    orphan.parent.mkdir()
    orphan.write_bytes(archive.read_bytes())

    assert fetch_spa.main(["--root", str(checkout), "--archive", str(orphan)]) == 2
    error = capsys.readouterr().err
    assert "does not extract unverified archives" in error

    digest = hashlib.sha256(orphan.read_bytes()).hexdigest()
    assert fetch_spa.main(["--root", str(checkout), "--archive", str(orphan), "--sha256", digest]) == 0
    assert (checkout / "frontend" / "dist" / "index.html").is_file()


def test_the_requested_version_is_the_one_the_checkout_declares():
    """release.yml refuses to build a tag that disagrees with shared/version.json.

    That is the whole reason the installer can ask for "this tree's version" and
    get the tag's assets. If the two ever stop being the same question, this
    test is where the assumption is written down.
    """

    declared = json.loads((ROOT / "shared" / "version.json").read_text(encoding="utf-8"))["version"]
    assert fetch_spa.declared_version(ROOT) == declared
    assert fetch_spa.asset_name(declared) == f"waveguide-generator-v2-spa-{declared}.tar.gz"
    assert fetch_spa.release_base_url("m3gnus/waveguide-generator", declared).endswith(f"/v{declared}")


def _make_sources_newer_than(checkout: Path) -> None:
    """The mtime ordering every real release install actually has.

    The archive keeps the times CI built it with; the checkout around it is
    written when the user installs. So the sources are always newer than the
    interface, and the mtime fallback in frontend_freshness would call a
    correct install stale.
    """

    source = checkout / "frontend" / "src" / "main.tsx"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export {};\n", encoding="utf-8")
    os.utime(checkout / "frontend" / "dist" / "index.html", ns=(1_000_000_000, 1_000_000_000))
    os.utime(source, ns=(2_000_000_000, 2_000_000_000))


def test_a_verified_release_interface_is_never_reported_stale(checkout, release):
    release()
    assert _install(checkout, release) == 0
    _make_sources_newer_than(checkout)

    fresh, reason = frontend_freshness(checkout)

    assert fresh, reason
    assert "2.0.0" in reason


def test_an_interface_from_another_release_names_both_versions(checkout, release):
    release()
    assert _install(checkout, release) == 0
    (checkout / "shared" / "version.json").write_text('{"version": "2.0.1"}\n', encoding="utf-8")

    fresh, reason = frontend_freshness(checkout)

    assert not fresh
    assert "v2.0.0" in reason and "v2.0.1" in reason


def test_an_edited_release_interface_stops_being_trusted(checkout, release):
    release()
    assert _install(checkout, release) == 0
    (checkout / "frontend" / "dist" / "assets" / "app.js").write_text("//\n", encoding="utf-8")

    fresh, reason = frontend_freshness(checkout)

    assert not fresh
    assert "checksum" in reason


def test_a_checkout_that_can_build_is_still_judged_on_its_sources(checkout, release):
    """A developer who fetched a release SPA and then edited the interface.

    The release stamp still verifies -- nothing touched dist -- so trusting it
    unconditionally would tell them their edits are being served when they are
    not. Owning a Vite binary is what separates the two cases.
    """

    release()
    assert _install(checkout, release) == 0
    _make_sources_newer_than(checkout)
    vite = vite_executable(checkout)
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")

    fresh, reason = frontend_freshness(checkout)

    assert not fresh
    assert "source is newer" in reason
