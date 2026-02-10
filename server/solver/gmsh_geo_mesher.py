from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import subprocess
import threading
from typing import Dict

from .deps import GMSH_AVAILABLE, gmsh

SUPPORTED_MSH_VERSIONS = {'2.2', '4.1'}
gmsh_lock = threading.Lock()


class GmshMeshingError(RuntimeError):
    pass


def gmsh_mesher_available() -> bool:
    if GMSH_AVAILABLE:
        return True
    return shutil.which('gmsh') is not None


def _validate_inputs(geo_text: str, msh_version: str) -> None:
    if not isinstance(geo_text, str) or not geo_text.strip():
        raise ValueError('geoText must be a non-empty string.')
    if msh_version not in SUPPORTED_MSH_VERSIONS:
        raise ValueError(f'Unsupported mshVersion: {msh_version}. Allowed: {sorted(SUPPORTED_MSH_VERSIONS)}')


def _parse_count_from_section(lines, section_name: str) -> int:
    marker = f'${section_name}'
    for i, line in enumerate(lines):
        if line.strip() != marker:
            continue
        if i + 1 >= len(lines):
            break
        tokens = lines[i + 1].strip().split()
        if not tokens:
            break
        # MSH2: single token count; MSH4: second token is total count.
        try:
            if len(tokens) == 1:
                return int(tokens[0])
            return int(tokens[1])
        except (ValueError, TypeError):
            return 0
    return 0


def parse_msh_stats(msh_text: str) -> Dict[str, int]:
    lines = msh_text.splitlines()
    return {
        'nodeCount': _parse_count_from_section(lines, 'Nodes'),
        'elementCount': _parse_count_from_section(lines, 'Elements')
    }


def _run_with_gmsh_api(geo_path: Path, msh_path: Path, msh_version: str, binary: bool) -> None:
    with gmsh_lock:
        initialized_here = False
        try:
            if not gmsh.isInitialized():
                gmsh.initialize()
                initialized_here = True

            gmsh.option.setNumber('General.Terminal', 0)
            gmsh.clear()
            gmsh.open(str(geo_path))
            gmsh.option.setNumber('Mesh.MshFileVersion', float(msh_version))
            gmsh.option.setNumber('Mesh.Binary', 1 if binary else 0)
            gmsh.option.setNumber('Mesh.SaveAll', 0)
            gmsh.model.mesh.generate(2)
            gmsh.write(str(msh_path))
        except Exception as exc:
            raise GmshMeshingError(f'Gmsh API meshing failed: {exc}') from exc
        finally:
            if initialized_here and gmsh.isInitialized():
                gmsh.finalize()


def _run_with_gmsh_cli(geo_path: Path, msh_path: Path, msh_version: str, binary: bool) -> None:
    gmsh_cmd = shutil.which('gmsh')
    if not gmsh_cmd:
        raise GmshMeshingError('gmsh executable not found in PATH.')

    fmt = 'msh2' if msh_version == '2.2' else 'msh4'
    cmd = [
        gmsh_cmd,
        str(geo_path),
        '-2',
        '-format',
        fmt,
        '-save_all',
        '0',
        '-o',
        str(msh_path)
    ]
    if binary:
        cmd.insert(2, '-bin')

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise GmshMeshingError('Gmsh CLI meshing timed out after 120 s.') from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or '').strip()
        stdout = (exc.stdout or '').strip()
        detail = stderr or stdout or f'exit code {exc.returncode}'
        raise GmshMeshingError(f'Gmsh CLI meshing failed: {detail}') from exc


def generate_msh_from_geo(geo_text: str, msh_version: str = '2.2', binary: bool = False) -> Dict[str, object]:
    _validate_inputs(geo_text, msh_version)

    with TemporaryDirectory(prefix='mwg-gmsh-') as temp_dir:
        temp_path = Path(temp_dir)
        geo_path = temp_path / 'input.geo'
        msh_path = temp_path / 'output.msh'

        geo_path.write_text(geo_text, encoding='utf-8')

        if GMSH_AVAILABLE:
            _run_with_gmsh_api(geo_path, msh_path, msh_version, binary)
        else:
            _run_with_gmsh_cli(geo_path, msh_path, msh_version, binary)

        if not msh_path.exists():
            raise GmshMeshingError('Gmsh did not produce an output .msh file.')

        msh_text = msh_path.read_text(encoding='utf-8', errors='replace')
        return {
            'msh': msh_text,
            'stats': parse_msh_stats(msh_text)
        }
