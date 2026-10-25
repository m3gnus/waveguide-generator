"""Vendoring reads the named commit, never an uncommitted source file."""
import hashlib
import json
from pathlib import Path
import subprocess

from scripts.vendor_source import vendor_source


def test_vendor_pairs_committed_bytes_with_source_identity(tmp_path: Path):
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    (source / 'model.py').write_bytes(b'VALUE = 1\n')
    subprocess.run(['git', '-C', str(source), 'add', 'model.py'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=m-a',
                    '-c', 'user.email=m3gnus@users.noreply.github.com', 'commit', '-qm', 'Initial model'], check=True)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    (source / 'model.py').write_bytes(b'VALUE = 2\n')
    target, manifest = tmp_path / 'vendored.py', tmp_path / 'vendored.source.json'
    result = vendor_source(source, commit, 'model.py', target, manifest, repository='https://example.com/model.git')
    assert target.read_bytes() == b'VALUE = 1\n'
    assert result == json.loads(manifest.read_text())
    assert result['commit'] == commit and result['path'] == 'model.py'
    assert result['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
