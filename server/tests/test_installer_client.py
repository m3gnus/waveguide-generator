"""Full-installer trust, pagination, caching and worker handoff boundaries."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import threading
from types import SimpleNamespace

import pytest

from shared import release_assets as assets
from server.tests.test_updates_signing import PUBLIC_HEX, SEED, _keypair, _sign
from server.updates import manifest
from server.updates import installer_client
from server.updates.installer_checker import (
    InstallerClientError, ReleaseResponse, check_releases, eligible_release, next_page,
    version_precedence,
)
from server.updates.installer_client import InstallerClient, SPACE_RESERVE

ROOT = "https://api.github.com/repos/m3gnus/waveguide-generator/releases"
RELEASE_ROOT = "https://github.com/m3gnus/waveguide-generator/releases/download"


class Stream(io.BytesIO):
    def __init__(self, data, url, headers=None):
        super().__init__(data)
        self.url, self.headers = url, headers or {}

    def geturl(self):
        return self.url


@pytest.fixture
def release(monkeypatch):
    monkeypatch.setattr(manifest, "UPDATE_SIGNING_PUBLIC_KEYS_HEX", (PUBLIC_HEX,))
    return make_release()


def make_release(version="0.3.4", *, bad_signature=False, seed=SEED):
    tag = "v" + version
    files = {name: ("payload:" + name).encode() for name in assets.user_download_names(version)}
    text = (f"# version {tag}\n" + "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items()))).encode()
    files["SHA256SUMS"], files["SHA256SUMS.sig"] = text, b"x" * 64 if bad_signature else _sign(seed, text)
    payload = {"tag_name": tag, "draft": False, "prerelease": "-" in version, "body": "Release notes", "assets": [
        {"name": name, "state": "uploaded", "size": len(data), "browser_download_url": f"{RELEASE_ROOT}/{tag}/{name}"}
        for name, data in files.items()]}

    def opener(request, **kwargs):
        return Stream(files[request.full_url.rsplit("/", 1)[-1]], request.full_url)

    return payload, files, opener


def client(tmp_path, release, **kwargs):
    payload, _, opener = release
    return InstallerClient(running_version=kwargs.pop("running_version", "0.3.3"), data_dir=tmp_path / "data", repo_root=tmp_path / "app",
        platform_name=assets.WINDOWS_PLATFORM,
        fetcher=kwargs.pop("fetcher", lambda url, etag: ReleaseResponse(payload, '"one"')),
        opener=kwargs.pop("opener", opener), clock=kwargs.pop("clock", lambda: 1000.0),
        install_probe=kwargs.pop("install_probe", lambda *args: {"kind": "windows", "installRoot": str(tmp_path / "installed"), "updateSupported": True, "reason": None}),
        disk_usage=kwargs.pop("disk_usage", lambda path: SimpleNamespace(free=SPACE_RESERVE + 100000)), **kwargs)


def checked(instance):
    instance.get_status(force=True)
    instance._worker.join(3)
    assert not instance._worker.is_alive()
    return instance.get_status()


def downloaded(instance, callback=None):
    accepted = instance.request_download(callback)
    assert accepted["accepted"] is True
    instance._download.join(3)
    assert not instance._download.is_alive()
    return instance.get_status()


def test_complete_signed_release_downloads_then_reverifies_before_callback(tmp_path, release):
    instance = client(tmp_path, release)
    status = checked(instance)
    assert status["schemaVersion"] == 2 and status["availability"] == "available"
    assert status["release"]["installer"]["name"].endswith("-setup.exe")
    assert status["release"]["installer"]["size"] == status["action"]["size"]
    callbacks = []
    status = downloaded(instance, lambda candidate: callbacks.append(instance.verified_installer()))
    assert status["installState"] == "ready" and len(callbacks) == 1
    ready = callbacks[0]
    assert ready.path.read_bytes() == release[1][ready.path.name]
    assert ready.sha256 == hashlib.sha256(ready.path.read_bytes()).hexdigest()
    ready.path.write_bytes(b"tampered".ljust(ready.size, b"x"))
    with pytest.raises(ValueError, match="checksum"):
        instance.verified_installer()
    instance.close()


def test_open_installed_bridge_transaction_does_not_change_check_or_download(tmp_path, release):
    from launchers import apply_update
    from server.updates.install_kind import probe_install

    app, runtime, data = tmp_path / "app", tmp_path / "runtime", tmp_path / "data"
    app.mkdir()
    runtime.mkdir()
    (app / "APP-MANIFEST.json").write_text(json.dumps({"schemaVersion": 1,
        "version": "0.3.3", "commit": "a" * 40, "runtimeId": "a" * 12}))
    (runtime / "RUNTIME-MANIFEST.json").write_text(json.dumps({"schemaVersion": 1,
        "runtimeId": "a" * 12, "platform": assets.WINDOWS_PLATFORM}))
    apply_update.begin_update_transaction(data_dir=data, bundle=tmp_path,
        resources=tmp_path, layers=[], platform_name="win32")
    apply_update.set_journal_state(data, tmp_path, "installed")
    (tmp_path / "app.previous").mkdir()
    (tmp_path / "runtime.previous").mkdir()
    before = apply_update.read_journal(data, tmp_path)
    instance = client(tmp_path, release, install_probe=lambda *args: probe_install(*args,
        environ={"WG2_BUNDLE": "1"}, registry_reader=lambda: str(tmp_path)))
    try:
        status = checked(instance)
        assert status["availability"] == "available" and status["canInstall"] is True
        assert status["action"]["kind"] == "full_installer"
        callbacks = []
        assert downloaded(instance, callbacks.append)["installState"] == "ready"
        assert len(callbacks) == 1 and instance.verified_installer() == callbacks[0]
        assert apply_update.read_journal(data, tmp_path) == before
        assert (tmp_path / ".update-transaction-open.json").is_file()
        assert (tmp_path / "app.previous").is_dir()
    finally:
        instance.close()


@pytest.mark.parametrize("mutation", ["missing-linux", "bad-signature", "wrong-version", "foreign-url", "wrong-size", "duplicate", "draft", "companion", "bad-numeric", "unuploaded"])
def test_ineligible_releases_never_become_available(release, mutation):
    payload, files, opener = copy.deepcopy(release[0]), dict(release[1]), release[2]
    if mutation == "missing-linux":
        payload["assets"] = [a for a in payload["assets"] if "linux" not in a["name"]]
    elif mutation == "bad-signature":
        files["SHA256SUMS.sig"] = b"x" * 64
    elif mutation == "wrong-version":
        files["SHA256SUMS"] = files["SHA256SUMS"].replace(b"v0.3.4", b"v0.3.5")
        files["SHA256SUMS.sig"] = _sign(SEED, files["SHA256SUMS"])
    elif mutation == "foreign-url":
        payload["assets"][0]["browser_download_url"] = "https://example.com/evil.exe"
    elif mutation == "wrong-size":
        payload["assets"][-1]["size"] += 1
    elif mutation == "duplicate":
        payload["assets"].append(payload["assets"][0])
    elif mutation == "draft":
        payload["draft"] = True
    elif mutation == "companion":
        payload["tag_name"] += "-updates"
    elif mutation == "bad-numeric":
        payload["tag_name"] = "v00.3.4"
    else:
        payload["assets"][0]["state"] = "new"
    opener = lambda request, **kwargs: Stream(files[request.full_url.rsplit("/", 1)[-1]], request.full_url)
    assert eligible_release(payload, channel="beta", platform_name=assets.WINDOWS_PLATFORM, opener=opener) is None
    # Stable fallback must preserve every eligibility refusal too.
    fetcher = lambda url, etag: ReleaseResponse(None if url.endswith("/latest") else [payload])
    found, proof, _ = check_releases(channel="stable", platform_name=assets.WINDOWS_PLATFORM,
        fetcher=fetcher, opener=opener, responses={}, running_version="0.3.3")
    assert found is proof is None


@pytest.mark.parametrize("version", ["v00.3.4", "v0.03.4", "v0.3.04", "v0.3.4-rc.01", "v0.3.4-updates", "../v0.3.4", "v0.3", "v0.3.4+local"])
def test_only_canonical_project_release_versions_are_accepted(version):
    with pytest.raises(InstallerClientError):
        version_precedence(version)


def test_beta_follows_trusted_next_then_highest_eligible_on_that_page(release):
    stable, _, stable_open = release
    rc, _, rc_open = make_release("0.4.0-rc.2")
    second = ROOT + "?per_page=100&page=2"
    calls = []

    def fetcher(url, etag):
        calls.append((url, etag))
        return ReleaseResponse([{"tag_name": "v9.0.0-updates"}], link=f'<{second}>; rel="next"') if len(calls) == 1 else ReleaseResponse([stable, rc])

    def opener(request, **kwargs):
        return rc_open(request) if "/v0.4.0-rc.2/" in request.full_url else stable_open(request)

    found, _, _ = check_releases(channel="beta", platform_name=assets.MACOS_PLATFORM, fetcher=fetcher, opener=opener, responses={})
    assert found["tag"] == "v0.4.0-rc.2"
    assert calls == [(ROOT + "?per_page=100&page=1", None), (second, None)]


@pytest.mark.parametrize("url", ["https://example.com/releases?page=2", ROOT + "?per_page=100&page=1", ROOT + "?per_page=100&page=2&extra=1", ROOT.replace("m3gnus", "foreign") + "?per_page=100&page=2", "http://api.github.com/repos/m3gnus/waveguide-generator/releases?per_page=100&page=2"])
def test_untrusted_or_nonadvancing_pagination_is_refused(url):
    with pytest.raises((InstallerClientError, ValueError)):
        next_page(f'<{url}>; rel="next"', current=ROOT + "?per_page=100&page=1", page=1)


def test_beta_stops_after_five_pages(release):
    calls = []
    def fetcher(url, etag):
        calls.append(url)
        return ReleaseResponse([], link=f'<{ROOT}?per_page=100&page={len(calls)+1}>; rel="next"')
    assert check_releases(channel="beta", platform_name=assets.MACOS_PLATFORM, fetcher=fetcher, opener=release[2], responses={})[0] is None
    assert len(calls) == 5


def releases_opener(*releases):
    by_tag = {release[0]["tag_name"]: release[2] for release in releases}
    def opener(request, **kwargs):
        tag = request.full_url.rsplit("/", 2)[-2]
        return by_tag[tag](request, **kwargs)
    return opener


def test_stable_latest_fast_path_does_not_fetch_release_list(tmp_path, release):
    calls = []
    def fetcher(url, etag):
        calls.append(url)
        assert url == ROOT + "/latest"
        return ReleaseResponse(release[0])
    instance = client(tmp_path, release, fetcher=fetcher)
    assert checked(instance)["release"]["version"] == "0.3.4"
    assert calls == [ROOT + "/latest"]
    instance.close()


def test_stable_skipped_transition_finds_newest_trusted_bridge_across_pages_and_downloads(tmp_path, release):
    # Latest is signed by a new key the old client has never received.
    latest = make_release("0.5.0", seed=bytes(reversed(range(32))))
    bridge = make_release("0.4.0")
    beta = make_release("0.6.0-rc.1")
    page1, page2 = ROOT + "?per_page=100&page=1", ROOT + "?per_page=100&page=2"
    calls = []
    def fetcher(url, etag):
        calls.append((url, etag))
        if url.endswith("/latest"):
            return ReleaseResponse(latest[0])
        if url == page1:
            return ReleaseResponse([latest[0], release[0], beta[0]], link=f'<{page2}>; rel="next"')
        assert url == page2
        return ReleaseResponse([bridge[0]])
    instance = client(tmp_path, release, fetcher=fetcher, opener=releases_opener(latest, bridge, beta, release))
    status = checked(instance)
    assert status["availability"] == "available" and status["release"]["version"] == "0.4.0"
    assert calls == [(ROOT + "/latest", None), (page1, None), (page2, None)]
    callbacks = []
    assert downloaded(instance, callbacks.append)["installState"] == "ready"
    assert len(callbacks) == 1 and instance.verified_installer().version == "0.4.0"
    instance.close()


@pytest.mark.parametrize("running,expected", [("0.3.3", "available"), ("0.3.4", "current"), ("0.3.5", "incomplete")])
def test_stable_fallback_never_offers_a_release_below_installed(tmp_path, release, running, expected):
    latest = make_release("0.5.0", bad_signature=True)
    def fetcher(url, etag):
        return ReleaseResponse(latest[0] if url.endswith("/latest") else [release[0]])
    instance = client(tmp_path, release, running_version=running, fetcher=fetcher, opener=releases_opener(latest, release))
    status = checked(instance)
    assert status["availability"] == expected
    assert status["canInstall"] is (expected == "available")
    assert (status["release"] is None) is (expected == "incomplete")
    instance.close()


def test_stable_fallback_reverifies_cached_latest_and_pages_on_304(release):
    latest = make_release("0.5.0", seed=bytes(reversed(range(32))))
    page = ROOT + "?per_page=100&page=1"
    calls = []
    def fetcher(url, etag):
        calls.append((url, etag))
        if etag:
            return ReleaseResponse(not_modified=True)
        return ReleaseResponse(latest[0] if url.endswith("/latest") else [release[0]], etag='"cached"')
    args = dict(channel="stable", platform_name=assets.MACOS_PLATFORM, fetcher=fetcher,
                opener=releases_opener(latest, release), running_version="0.3.3")
    found, _, responses = check_releases(**args, responses={})
    assert found["version"] == "0.3.4"
    found, _, _ = check_releases(**args, responses=responses)
    assert found["version"] == "0.3.4"
    assert calls == [(ROOT + "/latest", None), (page, None), (ROOT + "/latest", '"cached"'), (page, '"cached"')]
    release[1]["SHA256SUMS.sig"] = b"x" * 64
    assert check_releases(**args, responses=responses)[0] is None


def test_stable_fallback_stops_after_five_list_pages_and_never_accepts_unknown_key(release):
    unknown = make_release("0.5.0", seed=bytes(reversed(range(32))))
    calls = []
    def fetcher(url, etag):
        calls.append(url)
        if url.endswith("/latest"):
            return ReleaseResponse(unknown[0])
        page = len(calls) - 1
        return ReleaseResponse([unknown[0]], link=f'<{ROOT}?per_page=100&page={page+1}>; rel="next"')
    found, proof, _ = check_releases(channel="stable", platform_name=assets.MACOS_PLATFORM,
        fetcher=fetcher, opener=unknown[2], responses={}, running_version="0.3.3")
    assert found is proof is None
    assert calls == [ROOT + "/latest", *[ROOT + f"?per_page=100&page={page}" for page in range(1, 6)]]


@pytest.mark.parametrize("bad_page", [{}, [None] * 101])
def test_stable_fallback_refuses_malformed_or_unbounded_lists(release, bad_page):
    latest = make_release(bad_signature=True)
    def fetcher(url, etag):
        return ReleaseResponse(latest[0] if url.endswith("/latest") else bad_page)
    with pytest.raises(InstallerClientError, match="malformed or unbounded"):
        check_releases(channel="stable", platform_name=assets.MACOS_PLATFORM, fetcher=fetcher,
                       opener=latest[2], responses={}, running_version="0.3.3")


def test_stable_fallback_refuses_untrusted_pagination_even_after_a_valid_candidate(release):
    latest = make_release("0.5.0", bad_signature=True)
    def fetcher(url, etag):
        return ReleaseResponse(latest[0]) if url.endswith("/latest") else ReleaseResponse(
            [release[0]], link='<https://example.com/releases?per_page=100&page=2>; rel="next"')
    with pytest.raises(InstallerClientError, match="escaped"):
        check_releases(channel="stable", platform_name=assets.MACOS_PLATFORM, fetcher=fetcher,
                       opener=releases_opener(latest, release), responses={}, running_version="0.3.3")


def test_cache_etag_six_hours_and_current_version_recomparison(tmp_path, release):
    now, calls = [1000.0], []
    def fetcher(url, etag):
        calls.append(etag)
        return ReleaseResponse(release[0], '"release-etag"') if etag is None else ReleaseResponse(not_modified=True)
    instance = client(tmp_path, release, fetcher=fetcher, clock=lambda: now[0])
    assert checked(instance)["availability"] == "available"
    for _ in range(3):
        instance.get_status()
    assert calls == [None]
    now[0] += 6 * 3600
    instance.get_status()
    instance._worker.join(3)
    assert calls == [None, '"release-etag"']
    instance.close()
    new = client(tmp_path, release, fetcher=fetcher, clock=lambda: now[0])
    new.running_version = "0.3.4"
    assert new.get_status()["availability"] == "current"
    assert calls == [None, '"release-etag"']
    new.close()


def test_network_never_blocks_status_and_stale_channel_worker_cannot_publish(tmp_path, release):
    entered, unblock = threading.Event(), threading.Event()
    settings = SimpleNamespace(get=lambda name: {"channel": values[0]}, put=lambda name, value: values.__setitem__(0, value["channel"]))
    values = ["stable"]
    def fetcher(url, etag):
        if url.endswith("/latest"):
            entered.set()
            assert unblock.wait(3)
            return ReleaseResponse(release[0])
        return ReleaseResponse([])
    instance = client(tmp_path, release, settings=settings, fetcher=fetcher)
    assert instance.get_status()["checking"] is True
    assert entered.wait(1)
    old = instance._worker
    assert instance.set_channel("beta") == "beta"
    instance._worker.join(2)
    unblock.set()
    old.join(2)
    status = instance.get_status()
    assert status["channel"] == "beta" and status["release"] is None
    instance.close()


@pytest.mark.parametrize("failure", ["corrupt", "truncated", "extra", "content-length", "foreign-redirect", "disk-full"])
def test_failed_downloads_never_invoke_handoff(tmp_path, release, failure):
    instance = client(tmp_path, release)
    checked(instance)
    asset = instance.get_status()["release"]["installer"]
    payload = release[1][asset["name"]]
    if failure == "corrupt":
        payload = b"x" * len(payload)
    elif failure == "truncated":
        payload = payload[:-1]
    elif failure == "extra":
        payload += b"x"
    if failure == "disk-full":
        instance.disk_usage = lambda path: SimpleNamespace(free=asset["size"] - 1)
    instance.opener = lambda request, **kwargs: Stream(payload,
        "https://example.com/evil" if failure == "foreign-redirect" else request.full_url,
        {"Content-Length": "1"} if failure == "content-length" else None)
    callbacks = []
    status = downloaded(instance, callbacks.append)
    assert status["installState"] == "failed" and status["error"]
    assert callbacks == []
    assert not list((instance.data_dir / "update-install").rglob("*.part"))
    with pytest.raises(InstallerClientError):
        instance.verified_installer()
    instance.close()


@pytest.mark.parametrize("cancel", ["close", "cancel"])
def test_shutdown_or_cancellation_suppresses_handoff_and_second_download(tmp_path, release, cancel):
    instance = client(tmp_path, release)
    checked(instance)
    entered, unblock = threading.Event(), threading.Event()
    def opener(request, **kwargs):
        entered.set()
        assert unblock.wait(3)
        return release[2](request)
    instance.opener = opener
    callbacks = []
    instance.request_download(callbacks.append)
    assert entered.wait(1)
    with pytest.raises(InstallerClientError, match="already"):
        instance.request_download(callbacks.append)
    if cancel == "close":
        instance.close()
    else:
        instance.cancel_download("Cancelled by the user.")
    unblock.set()
    instance._download.join(2)
    assert callbacks == [] and instance.get_status()["installState"] == "failed"
    if cancel == "cancel":
        assert instance.reset_download()["installState"] == "idle"
    instance.close()


def test_callback_failure_is_status_and_destination_changes_refuse_handoff(tmp_path, release):
    instance = client(tmp_path, release)
    checked(instance)
    def failed(candidate):
        raise RuntimeError("Helper could not start; restart was released.")
    assert "Helper could not start" in downloaded(instance, failed)["error"]
    downloaded(instance)
    instance.install_probe = lambda *args: {"updateSupported": False, "installRoot": None}
    with pytest.raises(InstallerClientError, match="destination changed"):
        instance.verified_installer()
    instance.close()


def test_bad_cached_proof_is_discarded_not_offered(tmp_path, release):
    instance = client(tmp_path, release)
    checked(instance)
    instance.close()
    path = instance._cache_path
    cache = json.loads(path.read_text())
    cache["proof"]["signature"] = "00" * 64
    path.write_text(json.dumps(cache))
    entered, unblock = threading.Event(), threading.Event()
    def fetcher(url, etag):
        entered.set()
        unblock.wait(2)
        return ReleaseResponse([])
    new = client(tmp_path, release, fetcher=fetcher)
    assert new.get_status()["release"] is None
    assert entered.wait(1)
    new.close()
    unblock.set()


def test_rotation_accepts_only_compiled_old_and_new_keys(release, monkeypatch):
    old = PUBLIC_HEX
    new_seed = bytes(reversed(range(32)))
    new = _keypair(new_seed)[1].hex()
    data = release[1]["SHA256SUMS"]
    monkeypatch.setattr(manifest, "UPDATE_SIGNING_PUBLIC_KEYS_HEX", (new, old))
    assert manifest.verify_manifest(data, _sign(SEED, data), "v0.3.4")
    assert manifest.verify_manifest(data, _sign(new_seed, data), "v0.3.4")
    foreign = bytes([8]) * 32
    with pytest.raises(manifest.ManifestError, match="signature"):
        manifest.verify_manifest(data, _sign(foreign, data), "v0.3.4")
    monkeypatch.setattr(manifest, "UPDATE_SIGNING_PUBLIC_KEYS_HEX", (new,))
    with pytest.raises(manifest.ManifestError, match="signature"):
        manifest.verify_manifest(data, _sign(SEED, data), "v0.3.4")


@pytest.mark.parametrize("value", [[], None, "text", 1, True])
def test_nonobject_persisted_cache_cannot_crash_startup(tmp_path, release, value):
    path = tmp_path / "data" / "update-install" / "release-cache.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value))
    instance = client(tmp_path, release)
    assert checked(instance)["availability"] == "available"
    instance.close()


@pytest.mark.parametrize("field", ["proof", "proof-assets", "asset", "tag", "version", "manifest", "signature", "responses"])
def test_malformed_cache_shapes_are_ignored_safely(tmp_path, release, field):
    original = client(tmp_path, release)
    checked(original)
    original.close()
    path = original._cache_path
    cache = json.loads(path.read_text())
    if field == "proof":
        cache["proof"] = []
    elif field == "proof-assets":
        cache["proof"]["assets"] = []
    elif field == "asset":
        name = assets.user_download_names("0.3.4")[0]
        cache["proof"]["assets"][name] = None
    elif field in {"tag", "version"}:
        cache["release"][field] = {}
    elif field in {"manifest", "signature"}:
        cache["proof"][field] = 1
    else:
        cache["responses"] = {ROOT + "/latest": []}
    path.write_text(json.dumps(cache))
    instance = client(tmp_path, release)
    assert checked(instance)["availability"] == "available"
    instance.close()


def test_cached_display_metadata_is_normalized_from_the_verified_tag(tmp_path, release):
    original = client(tmp_path, release)
    checked(original)
    original.close()
    path = original._cache_path
    cache = json.loads(path.read_text())
    cache["release"].update(url="https://evil.example/", notes={}, publishedAt=[])
    path.write_text(json.dumps(cache))
    instance = client(tmp_path, release)
    cached = instance.get_status()["release"]
    assert cached["url"] == "https://github.com/m3gnus/waveguide-generator/releases/tag/v0.3.4"
    assert cached["notes"] == "" and cached["publishedAt"] is None
    instance.close()


def test_network_failure_without_a_prior_offer_is_unknown_not_preparing(tmp_path, release):
    def fetcher(url, etag):
        raise OSError("The release server is unavailable.")
    instance = client(tmp_path, release, fetcher=fetcher)
    status = checked(instance)
    assert status["availability"] == "unknown" and status["release"] is None
    assert status["freshness"] == "stale" and status["lastError"]
    assert status["canInstall"] is False and status["action"] is None
    instance.close()


def test_status_and_cancellation_remain_responsive_during_reverification(tmp_path, release, monkeypatch):
    instance = client(tmp_path, release)
    checked(instance)
    downloaded(instance)
    entered, unblock = threading.Event(), threading.Event()
    verify = installer_client.verify_file
    def paused(*args):
        entered.set()
        assert unblock.wait(3)
        verify(*args)
    monkeypatch.setattr(installer_client, "verify_file", paused)
    failures = []
    def reverify():
        try:
            instance.verified_installer()
        except InstallerClientError as exc:
            failures.append(str(exc))
    worker = threading.Thread(target=reverify)
    worker.start()
    assert entered.wait(1)
    assert instance.get_status()["installState"] == "ready"
    instance.cancel_download("Cancelled during verification.")
    unblock.set()
    worker.join(2)
    assert failures == ["Cancelled during verification."]
    instance.close()


def test_file_identity_change_during_reverification_is_refused(tmp_path, release, monkeypatch):
    instance = client(tmp_path, release)
    checked(instance)
    downloaded(instance)
    candidate = instance.verified_installer()
    verify = installer_client.verify_file
    def replace_after_hash(*args):
        verify(*args)
        replacement = candidate.path.with_suffix(".other")
        replacement.write_bytes(candidate.path.read_bytes())
        replacement.replace(candidate.path)
    monkeypatch.setattr(installer_client, "verify_file", replace_after_hash)
    with pytest.raises(InstallerClientError, match="changed during"):
        instance.verified_installer()
    instance.close()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes")
def test_fifo_release_cache_cannot_block_startup(tmp_path, release):
    path = tmp_path / "data" / "update-install" / "release-cache.json"
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    instance = client(tmp_path, release)
    assert checked(instance)["availability"] == "available"
    instance.close()
