"""Artifact-only signing retains source, inventory and authorization boundaries."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / '.github/workflows/rc-build.yml').read_text()
WORKFLOW = yaml.safe_load(TEXT)


def test_signing_is_opt_in_with_no_publication_authority():
    trigger = WORKFLOW.get('on') or WORKFLOW[True]
    option = trigger['workflow_dispatch']['inputs']['sign_candidate']
    assert option['type'] == 'boolean' and option['default'] is False
    signer = WORKFLOW['jobs']['sign-candidate']
    assert signer['if'] == '${{ inputs.sign_candidate }}'
    assert signer['environment'] == 'update-signing'
    assert signer.get('permissions', WORKFLOW['permissions']) == {'contents': 'read'}
    assert set(signer['needs']) == {'spa', 'macos-bundle', 'windows-bundle', 'linux-bundle'}
    for name, job in WORKFLOW['jobs'].items():
        for step in job.get('steps', []):
            if 'actions/checkout@' in step.get('uses', ''):
                assert step['with']['persist-credentials'] is False
        if name != 'sign-candidate':
            assert 'UPDATE_SIGNING_KEY' not in json.dumps(job)
        for step in job.get('steps', []):
            command = step.get('run', '')
            assert 'git tag ' not in command and 'gh release ' not in command
            assert 'action-gh-release' not in step.get('uses', '')


def test_signer_keeps_production_verification_and_immutable_subjects():
    signer = WORKFLOW['jobs']['sign-candidate']
    rendered = json.dumps(signer)
    assert 'scripts/candidate_manifest.py stage' in rendered
    assert '--expected-sha' in rendered and '$GITHUB_SHA' in rendered
    assert 'scripts/release_manifest.py pubkey-matches' in rendered
    assert 'scripts/release_manifest.py verify' in rendered
    assert 'openssl pkeyutl -sign' in rendered
    assert 'refs/heads/main' in rendered
    assert 'test \\"$INPUT_SHA\\" = \\"$SOURCE_SHA\\"' in rendered
    assert signer['steps'][0]['with']['ref'] == '${{ github.sha }}'
    for job in ['macos-bundle', 'windows-bundle', 'linux-bundle']:
        steps = WORKFLOW['jobs'][job]['steps']
        retained = [s for s in steps if s.get('with', {}).get('name') == 'rc-' + job.split('-')[0] + '-sign-inputs']
        assert len(retained) == 1 and retained[0]['if'] == '${{ inputs.sign_candidate }}'
        assert retained[0]['with']['if-no-files-found'] == 'error'


@pytest.mark.parametrize(('sha', 'version', 'passed'), [
    ('a' * 40, '0.3.6', True),
    ('a' * 8, '0.3.6', False),
    ('A' * 40, '0.3.6', False),
    ('a' * 40, '0.3.6-rc.1', True),
    ('a' * 40, '0.3.6-beta.2', True),
    ('a' * 40, '0.3.6-alpha', True),
    ('a' * 40, '0.3.6-main.1', False),
    ('a' * 40, '0.3.6+build', False),
])
def test_actual_guard_refuses_abbreviations_and_nonrelease_versions(tmp_path, sha, version, passed):
    step = WORKFLOW['jobs']['sign-candidate']['steps'][1]['run']
    match = re.search(r"python3 - <<'PY'\n(.*?)\nPY", step, re.S)
    assert match
    (tmp_path / 'shared').mkdir()
    (tmp_path / 'shared/version.json').write_text(json.dumps({'version': version}))
    output = tmp_path / 'env'
    env = os.environ | {'INPUT_SHA': sha, 'GITHUB_ENV': str(output), 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, '-c', match[1]], cwd=tmp_path, env=env,
                            text=True, capture_output=True)
    assert (result.returncode == 0) is passed, result.stderr
    if passed:
        assert output.read_text() == f'CANDIDATE_VERSION={version}\nRELEASE_TAG=v{version}\n'
    else:
        assert not output.exists()


def test_compact_candidate_artifact_keeps_signature_and_exact_installer_digests():
    steps = WORKFLOW['jobs']['sign-candidate']['steps']
    metadata = next(s['run'] for s in steps if s.get('name', '').startswith('Prepare compact'))
    assert 'manifest.verify_manifest' in metadata and 'manifest.verify_file' in metadata
    assert 'user_download_names(version)' in metadata
    assert "os.environ['GITHUB_SHA']" in metadata
    assert "('SHA256SUMS', 'SHA256SUMS.sig')" in metadata
    uploaded = {s.get('with', {}).get('name') for s in steps}
    assert {'rc-signed-candidate', 'rc-signed-manifest'} <= uploaded


def test_windows_failed_setup_retains_diagnostics_without_becoming_candidate():
    steps = WORKFLOW['jobs']['windows-bundle']['steps']
    gate = next(s for s in steps if s.get('id') == 'installer_gates')
    assert '-EvidenceDir $evidence' in gate['run']
    assert 'WG-inst-evidence-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT' in gate['run']
    evidence = next(s for s in steps if s.get('with', {}).get('name') == 'rc-windows-installer-gates')
    assert evidence['if'] == 'always()'
    assert evidence['with']['path'].startswith('${{ runner.temp }}/WG-inst-evidence-')
    failed = next(s for s in steps if s.get('with', {}).get('name') == 'rc-windows-setup-failure')
    assert failed['if'] == "${{ failure() && steps.installer_gates.outcome == 'failure' }}"
    attestation = next(s for s in steps if s.get('name') == 'Attest the exact Windows installer before transfer')
    assert steps.index(gate) < steps.index(attestation) < steps.index(evidence)
    assert 'if' not in attestation  # failed gates still prevent attestation
    signer = WORKFLOW['jobs']['sign-candidate']
    assert 'windows-bundle' in signer['needs']
    downloads = {s.get('with', {}).get('name') for s in signer['steps'] if 'download-artifact@' in s.get('uses', '')}
    assert 'rc-windows-sign-inputs' in downloads
    assert not {'rc-windows-setup-failure', 'rc-windows-installer-gates'} & downloads


def test_all_internal_and_published_payloads_have_builder_provenance():
    expected = {
        'macos-bundle': ['*-macos-arm64.dmg', 'update-app-*.zip', 'update-app-*.manifest.json',
                         'update-runtime-macos-*.zip'],
        'windows-bundle': ['*-windows-x86_64-setup.exe', '*-windows-x86_64.zip',
                           'update-runtime-windows-*.zip'],
        'linux-bundle': ['*-linux-x86_64.tar.gz', 'update-runtime-linux-*.zip'],
    }
    for name, subjects in expected.items():
        attest = next(s for s in WORKFLOW['jobs'][name]['steps'] if 'actions/attest@' in s.get('uses', ''))
        assert all(subject in attest['with']['subject-path'] for subject in subjects)
        assert WORKFLOW['jobs'][name]['permissions'] == {
            'contents': 'read', 'id-token': 'write', 'attestations': 'write'}


def test_every_artifact_consumed_by_promotion_retains_for_the_release_window():
    release = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())
    consumed = {step['with']['name'] for step in release['jobs']['publish']['steps']
                if 'actions/download-artifact@' in step.get('uses', '')}
    retained = {step['with']['name']: step['with'].get('retention-days')
                for job in WORKFLOW['jobs'].values() for step in job.get('steps', [])
                if 'actions/upload-artifact@' in step.get('uses', '')}
    for job in WORKFLOW['jobs'].values():
        for step in job.get('steps', []):
            if 'actions/download-artifact@' in step.get('uses', ''):
                assert 'retention-days' not in step.get('with', {})
    assert len(consumed) == 5
    assert {name: retained[name] for name in consumed} == dict.fromkeys(consumed, 90)
