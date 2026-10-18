#!/usr/bin/env python3
"""Verify a qualified rc-build run and stage its unchanged release assets.

Uses only the standard library and gh on a bare runner. Never builds, signs,
extracts payloads, tags or publishes. All source checks precede staging;
publication-state checks precede release writes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.candidate_manifest import validate  # noqa: E402
from scripts.release_manifest import _load  # noqa: E402
from shared import release_assets  # noqa: E402

WORKFLOW = '.github/workflows/rc-build.yml'
REQUIRED_JOBS = {
    'Build the SPA', 'Build the macOS standalone app',
    'Build the Windows standalone app', 'Build the Linux standalone app',
    'Sign candidate artifacts without publication',
}


def _gh_json(*args: str):
    return json.loads(subprocess.run(['gh', *args], check=True, capture_output=True, text=True).stdout)


def check_run(repository: str, run_id: str, expected_sha: str) -> dict:
    """The API, not a downloaded self-description, establishes qualification."""
    if not re.fullmatch(r'[1-9][0-9]*', run_id):
        raise ValueError('Source run id must be a positive decimal integer')
    if not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        raise ValueError('Expected source SHA must be a full lowercase commit')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid source repository')
    endpoint = f'repos/{repository}/actions/runs/{run_id}'
    run = _gh_json('api', endpoint)
    expected = {'id': int(run_id), 'path': WORKFLOW, 'event': 'workflow_dispatch',
                'head_branch': 'main', 'head_sha': expected_sha,
                'status': 'completed', 'conclusion': 'success'}
    if any(run.get(key) != value for key, value in expected.items()):
        raise ValueError('Source run must be a successful rc-build dispatch on the release SHA on main')
    for key in ('repository', 'head_repository'):
        if run.get(key, {}).get('full_name') != repository:
            raise ValueError('Source run repository mismatch')
    attempt = run.get('run_attempt')
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Invalid source run attempt')
    pages = _gh_json('api', '--paginate', '--slurp',
                     f'{endpoint}/attempts/{attempt}/jobs?per_page=100')
    jobs = [job for page in pages for job in page['jobs']]
    for name in REQUIRED_JOBS:
        matches = [job for job in jobs if job['name'] == name]
        if len(matches) != 1 or any(job.get('status') != 'completed' or
                                    job.get('conclusion') != 'success' for job in matches):
            raise ValueError(f'Required candidate job did not succeed: {name}')
    return {'repository': repository, 'run_id': run_id, 'sha': expected_sha, 'attempt': attempt}


def verify_attestation(path: Path, source: dict) -> None:
    """gh verifies signatures/digests/source; constrain the verified invocation too."""
    results = _gh_json('attestation', 'verify', str(path), '--repo', source['repository'],
                       '--signer-workflow', f"{source['repository']}/{WORKFLOW}",
                       '--source-digest', source['sha'], '--source-ref', 'refs/heads/main',
                       '--predicate-type', 'https://slsa.dev/provenance/v1', '--format', 'json')
    invocation = (f"https://github.com/{source['repository']}/actions/runs/"
                  f"{source['run_id']}/attempts/{source['attempt']}")
    for result in results:
        statement = result.get('verificationResult', {}).get('statement', {})
        actual = statement.get('predicate', {}).get('runDetails', {}).get('metadata', {}).get('invocationId')
        if statement.get('predicateType') == 'https://slsa.dev/provenance/v1' and actual == invocation:
            return
    raise ValueError(f'No verified attestation from the selected run attempt for {path.name}')


def _existing_release(repository: str, tag: str) -> dict | None:
    try:
        return _gh_json('api', f'repos/{repository}/releases/tags/{tag}')
    except subprocess.CalledProcessError as exc:
        if re.search(r'\bHTTP 404\b', exc.stderr or ''):
            return None
        raise


def _check_uploaded_assets(release: dict, directory: Path, *, complete: bool) -> None:
    manifest = _load()
    expected = {path.name: path for path in directory.iterdir()}
    assets = release['assets']
    actual = {asset['name'] for asset in assets}
    if len(actual) != len(assets) or not actual <= expected.keys() or (complete and actual != expected.keys()):
        raise ValueError('Existing release asset inventory differs from this qualified candidate')
    for asset in assets:
        path = expected[asset['name']]
        if (asset.get('state') != 'uploaded' or asset.get('size') != path.stat().st_size
                or asset.get('digest') != 'sha256:' + manifest.sha256_file(path)):
            raise ValueError(f'Existing release asset differs from qualified bytes: {path.name}')


def check_publication(repository: str, tag: str, sha: str, downloads: Path, updates: Path) -> bool:
    """Retry identical uploads; never mutate an existing public companion."""
    release = _existing_release(repository, tag)
    if release is not None:
        if release.get('draft') is not True:
            raise ValueError('Refusing to overwrite a published release')
        _check_uploaded_assets(release, downloads, complete=False)
    companion = _existing_release(repository, tag + '-updates')
    tagged = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', f'refs/tags/{tag}-updates^{{commit}}'],
                            check=False, capture_output=True, text=True)
    if tagged.returncode not in (0, 1):
        raise ValueError('Cannot resolve the companion tag')
    if companion is None:
        if tagged.returncode == 0:
            raise ValueError('Orphan companion tag already exists; refusing automatic publication')
        return False
    if companion.get('draft') is not False or companion.get('prerelease') is not True:
        raise ValueError('Existing companion must be a published prerelease')
    if tagged.returncode != 0 or tagged.stdout.strip() != sha:
        raise ValueError('Existing companion tag points to a different source SHA')
    _check_uploaded_assets(companion, updates, complete=True)
    return True


def stage(version: str, root: Path, signed: Path, downloads: Path, updates: Path, source: dict) -> None:
    """All source payloads, including internal layers, must pass before copying."""
    if not release_assets.VERSION_RE.fullmatch(version) or release_assets.is_build_stamp(version):
        raise ValueError('Expected a stable or release prerelease version')
    if downloads.exists() or updates.exists():
        raise ValueError('Release staging destinations must be fresh')
    assets = validate(version, root, source['sha'])
    subjects = set(release_assets.user_download_names(version)) | {
        release_assets.installer_name(release_assets.WINDOWS_PLATFORM, version)}
    if signed.is_symlink() or not signed.is_dir():
        raise ValueError('Signed candidate must be a real directory')
    if {path.name for path in signed.iterdir()} != subjects | {'SHA256SUMS', 'SHA256SUMS.sig'}:
        raise ValueError('Signed candidate inventory mismatch')
    if any(path.is_symlink() or not path.is_file() for path in signed.iterdir()):
        raise ValueError('Signed candidate inputs must be regular files')
    manifest = _load()
    data = (signed / 'SHA256SUMS').read_bytes()
    entries = manifest.verify_manifest(data, (signed / 'SHA256SUMS.sig').read_bytes(), f'v{version}')
    expected = manifest.build_manifest(f'v{version}', [assets[name] for name in sorted(subjects)])
    if data != expected or set(entries) != subjects:
        raise ValueError('Signed manifest differs from the complete candidate inventory')
    for name in subjects:
        manifest.verify_file(entries, name, signed / name)
        manifest.verify_file(entries, name, assets[name])
    for path in assets.values():
        verify_attestation(path, source)

    # Recheck the live API after downloads/verification: never publish a snapshot
    # whose run has since been re-run, cancelled or failed.
    if check_run(source['repository'], source['run_id'], source['sha']) != source:
        raise ValueError('Source run changed during promotion')
    downloads.mkdir(parents=True)
    updates.mkdir(parents=True)
    for name in sorted(subjects):
        target = downloads if release_assets.is_user_download(name, version) else updates
        shutil.copyfile(assets[name], target / name)
        manifest.verify_file(entries, name, target / name)
    spa = assets[release_assets.spa_archive_name(version)]
    for path in (spa, spa.with_name(release_assets.checksum_name(spa.name))):
        shutil.copyfile(path, updates / path.name)
    for name in ('SHA256SUMS', 'SHA256SUMS.sig'):
        shutil.copyfile(signed / name, downloads / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check-run')
    promote = commands.add_parser('stage')
    publication = commands.add_parser('check-publication')
    for command in (check, promote):
        command.add_argument('--repository', required=True)
        command.add_argument('--run-id', required=True)
        command.add_argument('--expected-sha', required=True)
    check.add_argument('--out', type=Path, required=True)
    for name in ('source-run', 'root', 'signed', 'downloads', 'updates'):
        promote.add_argument('--' + name, type=Path, required=True)
    promote.add_argument('--version', required=True)
    publication.add_argument('--repository', required=True)
    publication.add_argument('--tag', required=True)
    publication.add_argument('--expected-sha', required=True)
    for name in ('downloads', 'updates', 'out'):
        publication.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check-publication':
            exists = check_publication(args.repository, args.tag, args.expected_sha, args.downloads, args.updates)
            with args.out.open('a', encoding='utf-8') as output:
                output.write(f'companion_exists={str(exists).lower()}\n')
            return 0
        source = check_run(args.repository, args.run_id, args.expected_sha)
        if args.command == 'check-run':
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(source) + '\n', encoding='utf-8')
        else:
            if json.loads(args.source_run.read_text(encoding='utf-8')) != source:
                raise ValueError('Source run changed since the pre-download check')
            stage(args.version, args.root, args.signed, args.downloads, args.updates, source)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f'promote_candidate: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
