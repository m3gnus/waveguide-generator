"""The candidate signer receives a complete, unchanged release inventory."""

import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from scripts import candidate_manifest
from scripts.release_manifest import _load
from shared import release_assets as names


from scripts.tests.candidate_fixtures import (
    ASSETS, RUNTIME, SOURCE_SHA, VERSION, _runtime_zip, _write, make_inputs,
)


@pytest.fixture
def inputs(tmp_path):
    return make_inputs(tmp_path / 'inputs')


def test_bare_runner_cli_stages_exact_release_subjects_without_altering_bytes(inputs, tmp_path):
    out = tmp_path / "staged"
    result = subprocess.run(
        [sys.executable, "-S", str(Path(candidate_manifest.__file__)), "stage",
         "--version", VERSION, "--expected-sha", SOURCE_SHA, "--root", str(inputs), "--out", str(out)],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    expected = {name for directory, name in ASSETS
                if directory != "spa" and not name.startswith(("update-app-", "update-runtime-"))}
    assert len(expected) == 4
    assert {path.name for path in out.iterdir()} == expected | {"SHA256SUMS"}
    manifest = _load()
    tag, entries = manifest.parse_manifest((out / "SHA256SUMS").read_bytes())
    assert tag == "v0.3.6" and set(entries) == expected
    for directory, name in ASSETS:
        if name in expected:
            assert (out / name).read_bytes() == (inputs / directory / name).read_bytes()
            manifest.verify_file(entries, name, out / name)


@pytest.mark.parametrize("asset", ASSETS, ids=[name for _, name in ASSETS])
@pytest.mark.parametrize("sidecar", [False, True])
def test_every_asset_and_sidecar_is_required(inputs, tmp_path, asset, sidecar):
    directory, name = asset
    (inputs / directory / (names.checksum_name(name) if sidecar else name)).unlink()
    out = tmp_path / "staged"
    assert candidate_manifest.main(["stage", "--version", VERSION, "--expected-sha", SOURCE_SHA, "--root", str(inputs), "--out", str(out)]) == 1
    assert not out.exists()


@pytest.mark.parametrize("target", ["installer", "spa", "sidecar"])
def test_corrupt_bytes_and_sidecars_refuse_before_staging(inputs, tmp_path, target):
    directory, name = ASSETS[0 if target == "spa" else 3]
    path = inputs / directory / name
    if target == "sidecar":
        path = path.with_name(names.checksum_name(name))
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("damage", ["unexpected", "duplicate", "collision", "directory", "runtime"])
def test_inventory_cannot_silently_omit_or_merge_inputs(inputs, tmp_path, damage):
    source = inputs / ASSETS[3][0] / ASSETS[3][1]
    if damage == "unexpected":
        (inputs / "windows" / "extra.zip").write_bytes(b"extra")
    elif damage in {"duplicate", "collision"}:
        shutil.copyfile(source, inputs / "windows" / (source.name.upper() if damage == "collision" else source.name))
    elif damage == "directory":
        (inputs / "windows" / "nested").mkdir()
    else:
        name = names.runtime_layer_name(names.LINUX_PLATFORM, RUNTIME)
        path = inputs / "linux" / name
        wrong = path.with_name(name.replace(RUNTIME, "f" * 12))
        _write(wrong, path.read_bytes())
        path.unlink()
        path.with_name(names.checksum_name(name)).unlink()
    with pytest.raises(ValueError):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("field,value", [
    ("schemaVersion", True), ("version", "0.3.5"), ("runtimeId", "f" * 12),
    ("runtimeId", "../escape"), ("commit", "short"), ("treeSha256", None),
])
def test_checksummed_but_invalid_app_identity_is_refused(inputs, tmp_path, field, value):
    path = inputs / "macos" / names.app_manifest_name(VERSION)
    identity = json.loads(path.read_text())
    identity[field] = value
    _write(path, json.dumps(identity).encode())
    with pytest.raises(ValueError):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("kind", ["file", "directory", "root"])
def test_symlink_inputs_are_refused(inputs, tmp_path, kind):
    path = inputs / ASSETS[3][0] / ASSETS[3][1] if kind == "file" else (
        inputs / "macos" if kind == "directory" else inputs)
    target = tmp_path / "link-target"
    path.rename(target)
    try:
        path.symlink_to(target, target_is_directory=kind != "file")
    except OSError as exc:
        pytest.skip(f"This host cannot create test symlinks: {exc}")
    with pytest.raises(ValueError, match="real directory|regular file"):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)


def test_existing_output_is_never_overwritten(inputs, tmp_path):
    out = tmp_path / "staged"
    out.mkdir()
    sentinel = out / "SHA256SUMS"
    sentinel.write_bytes(b"prior candidate")
    with pytest.raises(FileExistsError):
        candidate_manifest.stage(VERSION, inputs, out, SOURCE_SHA)
    assert sentinel.read_bytes() == b"prior candidate"


@pytest.mark.parametrize("sha", ["b" * 40, "short", "g" * 40, "A" * 40, ""])
def test_expected_workflow_source_is_required_and_must_match(inputs, tmp_path, sha):
    with pytest.raises(ValueError, match="SHA"):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", sha)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("directory,platform", [
    ("macos", names.MACOS_PLATFORM), ("windows", names.WINDOWS_PLATFORM), ("linux", names.LINUX_PLATFORM),
])
@pytest.mark.parametrize("damage", ["runtime-id", "platform"])
def test_correctly_named_and_checksummed_runtime_must_match_internal_identity(inputs, tmp_path, directory, platform, damage):
    path = inputs / directory / names.runtime_layer_name(platform, RUNTIME)
    data = _runtime_zip(platform, "f" * 12) if damage == "runtime-id" else _runtime_zip("wrong-platform")
    _write(path, data)
    with pytest.raises(ValueError, match="Runtime archive identity"):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("damage", ["missing", "nested", "traversal", "duplicate", "corrupt", "symlink", "oversized", "schema"])
def test_runtime_manifest_must_be_one_valid_regular_root_entry(inputs, tmp_path, damage):
    path = inputs / "macos" / names.runtime_layer_name(names.MACOS_PLATFORM, RUNTIME)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        manifest = json.dumps({"schemaVersion": 1, "runtimeId": RUNTIME, "platform": names.MACOS_PLATFORM})
        if damage == "missing":
            archive.writestr("other.json", manifest)
        elif damage in {"nested", "traversal"}:
            prefix = "nested/" if damage == "nested" else "../"
            archive.writestr(prefix + "RUNTIME-MANIFEST.json", manifest)
        elif damage == "symlink":
            entry = zipfile.ZipInfo("RUNTIME-MANIFEST.json")
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, manifest)
        elif damage == "oversized":
            archive.writestr("RUNTIME-MANIFEST.json", b" " * (64 * 1024 + 1))
        elif damage == "schema":
            archive.writestr("RUNTIME-MANIFEST.json", manifest.replace('"schemaVersion": 1', '"schemaVersion": 2'))
        else:
            archive.writestr("RUNTIME-MANIFEST.json", manifest)
            if damage == "duplicate":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.writestr("RUNTIME-MANIFEST.json", manifest)
    _write(path, b"not a zip" if damage == "corrupt" else buffer.getvalue())
    with pytest.raises(ValueError):
        candidate_manifest.stage(VERSION, inputs, tmp_path / "staged", SOURCE_SHA)
    assert not (tmp_path / "staged").exists()


# Promotion exercises the same inventory contract, without rebuilding or signing.
from scripts import promote_candidate  # noqa: E402
from server.tests.test_updates_signing import SEED, _keypair, _sign  # noqa: E402


@pytest.fixture
def promotion(inputs, tmp_path, monkeypatch):
    signed = tmp_path / 'signed'
    candidate_manifest.stage(VERSION, inputs, signed, SOURCE_SHA)
    module = _load()
    monkeypatch.setattr(module, 'UPDATE_SIGNING_PUBLIC_KEYS_HEX', (_keypair(SEED)[1].hex(),))
    monkeypatch.setattr(promote_candidate, '_load', lambda: module)
    (signed / 'SHA256SUMS.sig').write_bytes(_sign(SEED, (signed / 'SHA256SUMS').read_bytes()))
    source = {'repository': 'example/project', 'run_id': '1234', 'sha': SOURCE_SHA, 'attempt': 1}
    monkeypatch.setattr(promote_candidate, 'check_run', lambda *args: source)
    verified = []
    monkeypatch.setattr(promote_candidate, 'verify_attestation', lambda path, source: verified.append(path.name))
    return inputs, signed, source, verified


def _promote(promotion, tmp_path, version=VERSION):
    root, signed, source, _ = promotion
    promote_candidate.stage(version, root, signed, tmp_path / 'downloads', tmp_path / 'updates', source)


def test_promotion_verifies_all_subjects_and_preserves_exact_release_bytes(promotion, tmp_path):
    root, signed, _, verified = promotion
    _promote(promotion, tmp_path)
    assert set(verified) == {name for _, name in ASSETS}
    downloads = set(names.user_download_names(VERSION)) | {'SHA256SUMS', 'SHA256SUMS.sig'}
    updates = {names.spa_archive_name(VERSION), names.checksum_name(names.spa_archive_name(VERSION)),
               names.installer_name(names.WINDOWS_PLATFORM, VERSION)}
    for directory, expected in (('downloads', downloads), ('updates', updates)):
        assert {p.name for p in (tmp_path / directory).iterdir()} == expected
        for name in expected:
            source = signed / name if name.startswith('SHA256SUMS') else next(root.rglob(name))
            assert (tmp_path / directory / name).read_bytes() == source.read_bytes()


@pytest.mark.parametrize('damage', ['signature', 'signed-payload', 'missing', 'extra', 'manifest-subjects',
                                   'sha', 'version', 'digest', 'extra-root', 'signed-link'])
def test_promotion_refuses_damage_before_staging_or_provenance(promotion, tmp_path, damage):
    root, signed, source, verified = promotion
    if damage == 'signature':
        (signed / 'SHA256SUMS.sig').write_bytes(b'0' * 64)
    elif damage == 'signed-payload':
        (signed / names.windows_setup_name(VERSION)).write_bytes(b'changed')
    elif damage == 'missing':
        (signed / names.windows_setup_name(VERSION)).unlink()
    elif damage == 'extra':
        (signed / 'extra.txt').write_bytes(b'changed')
    elif damage == 'manifest-subjects':
        # A valid signature with a narrowed manifest must still be refused.
        manifest = _load()
        data = manifest.build_manifest('v' + VERSION, [signed / names.windows_setup_name(VERSION)])
        (signed / 'SHA256SUMS').write_bytes(data)
        (signed / 'SHA256SUMS.sig').write_bytes(_sign(SEED, data))
    elif damage == 'sha':
        source['sha'] = 'f' * 40
    elif damage == 'version':
        with pytest.raises(ValueError):
            _promote(promotion, tmp_path, '0.3.7')
        return
    elif damage == 'digest':
        next(root.rglob(names.spa_archive_name(VERSION))).write_bytes(b'changed')
    elif damage == 'extra-root':
        (root / 'extra').mkdir()
    elif damage == 'signed-link':
        path = signed / names.windows_setup_name(VERSION)
        target = tmp_path / 'target'
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(ValueError):
        _promote(promotion, tmp_path)
    assert not (tmp_path / 'downloads').exists() and not (tmp_path / 'updates').exists()
    assert not verified


def test_failed_attestation_prevents_any_staging(promotion, tmp_path, monkeypatch):
    def refuse(*args):
        raise ValueError('attestation failure')
    monkeypatch.setattr(promote_candidate, 'verify_attestation', refuse)
    with pytest.raises(ValueError, match='attestation failure'):
        _promote(promotion, tmp_path)
    assert not (tmp_path / 'downloads').exists() and not (tmp_path / 'updates').exists()


def test_rerun_during_promotion_is_refused(promotion, tmp_path, monkeypatch):
    source = promotion[2]
    monkeypatch.setattr(promote_candidate, 'check_run', lambda *args: source | {'attempt': 2})
    with pytest.raises(ValueError, match='changed during'):
        _promote(promotion, tmp_path)
    assert not (tmp_path / 'downloads').exists()


def _run_response():
    return {'id': 1234, 'path': promote_candidate.WORKFLOW, 'event': 'workflow_dispatch',
            'head_branch': 'main', 'head_sha': SOURCE_SHA, 'status': 'completed', 'conclusion': 'success',
            'run_attempt': 1, 'repository': {'full_name': 'example/project'},
            'head_repository': {'full_name': 'example/project'}}


@pytest.mark.parametrize('field,value', [
    ('id', 4321), ('path', '.github/workflows/release.yml'), ('event', 'pull_request'),
    ('head_branch', 'dev'), ('head_sha', 'b' * 40), ('status', 'in_progress'),
    ('conclusion', 'failure'), ('run_attempt', 0), ('run_attempt', True),
    ('repository', {'full_name': 'other/project'}), ('head_repository', {'full_name': 'other/project'}),
])
def test_unqualified_source_runs_are_refused(monkeypatch, field, value):
    monkeypatch.setattr(promote_candidate, '_gh_json', lambda *args: _run_response() | {field: value})
    with pytest.raises(ValueError):
        promote_candidate.check_run('example/project', '1234', SOURCE_SHA)


@pytest.mark.parametrize('damage', [None, 'missing', 'skipped', 'failure', 'duplicate'])
def test_source_run_requires_successful_builds_and_protected_signing(monkeypatch, damage):
    jobs = [{'name': name, 'status': 'completed', 'conclusion': 'success'}
            for name in sorted(promote_candidate.REQUIRED_JOBS)]
    if damage == 'missing':
        jobs.pop()
    elif damage == 'duplicate':
        jobs.append(jobs[0])
    elif damage:
        jobs[-1]['conclusion'] = damage
    def gh(*args):
        if '--slurp' in args:
            assert '/attempts/1/jobs?per_page=100' in args[-1]
            return [{'jobs': jobs[:2]}, {'jobs': jobs[2:]}]
        return _run_response()
    monkeypatch.setattr(promote_candidate, '_gh_json', gh)
    if damage:
        with pytest.raises(ValueError, match='Required candidate job'):
            promote_candidate.check_run('example/project', '1234', SOURCE_SHA)
    else:
        assert promote_candidate.check_run('example/project', '1234', SOURCE_SHA)['attempt'] == 1


@pytest.mark.parametrize('run_id', ['', '0', '-1', '../other', '1?x', 'abc'])
def test_run_id_is_required_before_any_api_call(monkeypatch, run_id):
    monkeypatch.setattr(promote_candidate, '_gh_json', lambda *args: pytest.fail('API called'))
    with pytest.raises(ValueError, match='run id'):
        promote_candidate.check_run('example/project', run_id, SOURCE_SHA)


@pytest.mark.parametrize('invocation', ['1234/attempts/1', '1235/attempts/1', '1234/attempts/2', None])
def test_provenance_must_be_cryptographically_verified_for_the_exact_run(monkeypatch, tmp_path, invocation):
    source = {'repository': 'example/project', 'run_id': '1234', 'sha': SOURCE_SHA, 'attempt': 1}
    def gh(*args):
        assert args[:2] == ('attestation', 'verify')
        for flag, expected in [('--repo', 'example/project'),
                               ('--signer-workflow', 'example/project/' + promote_candidate.WORKFLOW),
                               ('--source-digest', SOURCE_SHA), ('--source-ref', 'refs/heads/main'),
                               ('--predicate-type', 'https://slsa.dev/provenance/v1'), ('--format', 'json')]:
            assert args[args.index(flag) + 1] == expected
        return [{'verificationResult': {'statement': {'predicateType': 'https://slsa.dev/provenance/v1',
            'predicate': {'runDetails': {'metadata': {'invocationId':
                'https://github.com/example/project/actions/runs/' + invocation if invocation else None}}}}}}]
    monkeypatch.setattr(promote_candidate, '_gh_json', gh)
    if invocation == '1234/attempts/1':
        promote_candidate.verify_attestation(tmp_path / 'asset', source)
    else:
        with pytest.raises(ValueError, match='selected run attempt'):
            promote_candidate.verify_attestation(tmp_path / 'asset', source)


def test_gh_verification_failure_is_never_treated_as_an_attestation(monkeypatch, tmp_path):
    def gh(*args):
        raise subprocess.CalledProcessError(1, 'gh')
    monkeypatch.setattr(promote_candidate, '_gh_json', gh)
    with pytest.raises(subprocess.CalledProcessError):
        promote_candidate.verify_attestation(tmp_path / 'asset',
            {'repository': 'example/project', 'run_id': '1234', 'sha': SOURCE_SHA, 'attempt': 1})


@pytest.mark.parametrize('version', ['0.3.6-rc.1', '0.3.6-beta.2', '0.3.6-alpha'])
def test_prerelease_artifacts_promote_with_the_same_contract(tmp_path, monkeypatch, version):
    root = make_inputs(tmp_path / 'inputs', version)
    signed = tmp_path / 'signed'
    candidate_manifest.stage(version, root, signed, SOURCE_SHA)
    module = _load()
    monkeypatch.setattr(module, 'UPDATE_SIGNING_PUBLIC_KEYS_HEX', (_keypair(SEED)[1].hex(),))
    monkeypatch.setattr(promote_candidate, '_load', lambda: module)
    (signed / 'SHA256SUMS.sig').write_bytes(_sign(SEED, (signed / 'SHA256SUMS').read_bytes()))
    source = {'repository': 'example/project', 'run_id': '1234', 'sha': SOURCE_SHA, 'attempt': 1}
    monkeypatch.setattr(promote_candidate, 'check_run', lambda *args: source)
    monkeypatch.setattr(promote_candidate, 'verify_attestation', lambda *args: None)
    promote_candidate.stage(version, root, signed, tmp_path / 'downloads', tmp_path / 'updates', source)
    assert {p.name for p in (tmp_path / 'downloads').iterdir()} == set(names.user_download_names(version)) | {
        'SHA256SUMS', 'SHA256SUMS.sig'}
    assert (tmp_path / 'updates' / names.checksum_name(names.spa_archive_name(version))).is_file()


@pytest.fixture
def publication(tmp_path, monkeypatch):
    downloads, updates = tmp_path / 'downloads', tmp_path / 'updates'
    downloads.mkdir()
    updates.mkdir()
    (downloads / 'installer').write_bytes(b'qualified installer')
    (updates / 'portable').write_bytes(b'qualified portable')
    def release(directory, draft=False):
        manifest = _load()
        return {'draft': draft, 'prerelease': True, 'assets': [
            {'name': path.name, 'size': path.stat().st_size, 'state': 'uploaded',
             'digest': 'sha256:' + manifest.sha256_file(path)} for path in directory.iterdir()]}
    companion = release(updates)
    main = release(downloads, draft=True)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
        subprocess.CompletedProcess(args, 0, stdout=SOURCE_SHA + '\n'))
    return downloads, updates, companion, main


@pytest.mark.parametrize('state', ['absent', 'identical', 'different-sha', 'different-bytes',
                                  'missing', 'extra', 'duplicate', 'draft', 'stable', 'no-digest',
                                  'public-main', 'extra-draft', 'different-draft', 'partial-draft', 'orphan'])
def test_existing_releases_cannot_mix_or_mutate_qualified_runs(publication, monkeypatch, state):
    downloads, updates, companion, main = publication
    if state == 'absent':
        monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
            subprocess.CompletedProcess(args, 1, stdout=''))
    elif state == 'different-sha':
        monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
            subprocess.CompletedProcess(args, 0, stdout='b' * 40 + '\n'))
    elif state == 'different-bytes':
        companion['assets'][0]['digest'] = 'sha256:' + '0' * 64
    elif state == 'missing':
        companion['assets'].clear()
    elif state in {'extra', 'duplicate'}:
        companion['assets'].append(companion['assets'][0] | {'name': 'extra' if state == 'extra' else 'portable'})
    elif state == 'draft':
        companion['draft'] = True
    elif state == 'stable':
        companion['prerelease'] = False
    elif state == 'no-digest':
        companion['assets'][0]['digest'] = None
    elif state == 'public-main':
        main['draft'] = False
    elif state == 'extra-draft':
        main['assets'].append(main['assets'][0] | {'name': 'extra'})
    elif state == 'different-draft':
        main['assets'][0]['digest'] = 'sha256:' + '0' * 64
    elif state == 'partial-draft':
        main['assets'].clear()
    monkeypatch.setattr(promote_candidate, '_existing_release', lambda repo, tag:
        (None if state in {'absent', 'orphan'} else companion) if tag.endswith('-updates') else main)
    if state in {'absent', 'identical', 'partial-draft'}:
        assert promote_candidate.check_publication('example/project', 'v' + VERSION, SOURCE_SHA,
                                                   downloads, updates) is (state != 'absent')
    else:
        with pytest.raises(ValueError):
            promote_candidate.check_publication('example/project', 'v' + VERSION, SOURCE_SHA, downloads, updates)


@pytest.mark.parametrize('status', [404, 403, 500])
def test_only_api_not_found_can_mean_no_existing_release(monkeypatch, status):
    def fail(*args):
        raise subprocess.CalledProcessError(1, 'gh api', stderr=f'gh: error (HTTP {status})')
    monkeypatch.setattr(promote_candidate, '_gh_json', fail)
    if status == 404:
        assert promote_candidate._existing_release('example/project', 'v' + VERSION) is None
    else:
        with pytest.raises(subprocess.CalledProcessError):
            promote_candidate._existing_release('example/project', 'v' + VERSION)
