"""WGLink releases retain exact source, license, version, and file provenance."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build_wglink_package.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_wglink_package_test", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(tmp_path: Path, commit: str) -> Path:
    root = tmp_path / "hornlab-fusion-addin"
    addin = root / "fusion-addins" / "WGLink"
    (addin / "resources" / "insert").mkdir(parents=True)
    (root / "scripts").mkdir()
    (addin / "WGLink.py").write_text("def run(_context): pass\n", encoding="utf-8")
    (addin / "WGLink.manifest").write_text(
        json.dumps({"version": "0.1.1"}), encoding="utf-8"
    )
    (addin / "wglink_core.py").write_text(
        'PACKAGED_RUNTIME_FILE = "wglink_runtime.json"\n', encoding="utf-8"
    )
    (addin / "resources" / "insert" / "16x16.png").write_bytes(b"png")
    (root / "scripts" / "wglink_resample.py").write_text(
        "# exact resampler\n", encoding="utf-8"
    )
    (root / "LICENSE").write_text("AGPL test fixture\n", encoding="utf-8")
    return root


def _spec(commit: str) -> dict[str, object]:
    return {
        "schema": 1,
        "repository": "https://example.invalid/hornlab-fusion-addin.git",
        "commit": commit,
        "license": "AGPL-3.0-or-later",
        "addinVersion": "0.1.1",
    }


def test_package_is_deterministic_and_records_every_source_hash(tmp_path: Path):
    builder = _load_builder()
    commit = "a" * 40
    source = _source(tmp_path, commit)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    builder.build_package(
        source, first, spec=_spec(commit), version="9.8.7", observed_commit=commit
    )
    builder.build_package(
        source, second, spec=_spec(commit), version="9.8.7", observed_commit=commit
    )

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = set(archive.namelist())
        provenance = json.loads(archive.read("wglink/provenance.json"))
        assert provenance["sourceCommit"] == commit
        assert provenance["sourceLicense"] == "AGPL-3.0-or-later"
        assert provenance["waveguideGeneratorVersion"] == "9.8.7"
        assert "wglink/LICENSE" in names
        assert "wglink/scripts/wglink_resample.py" in names
        assert "wglink/fusion-addins/WGLink/wglink_core.py" in names
        assert names == set(provenance["files"]) | {"wglink/provenance.json"}
        for name, expected in provenance["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected


def test_package_refuses_a_checkout_other_than_the_reviewed_commit(tmp_path: Path):
    builder = _load_builder()
    source = _source(tmp_path, "a" * 40)

    with pytest.raises(builder.PackageError, match="unpinned"):
        builder.build_package(
            source,
            tmp_path / "wrong.zip",
            spec=_spec("a" * 40),
            version="1.0.0",
            observed_commit="b" * 40,
        )


def test_package_refuses_wglink_without_the_managed_runtime_contract(tmp_path: Path):
    builder = _load_builder()
    commit = "a" * 40
    source = _source(tmp_path, commit)
    (source / "fusion-addins" / "WGLink" / "wglink_core.py").write_text(
        "# old checkout\n", encoding="utf-8"
    )

    with pytest.raises(builder.PackageError, match="packaged-runtime contract"):
        builder.build_package(
            source,
            tmp_path / "old.zip",
            spec=_spec(commit),
            version="1.0.0",
            observed_commit=commit,
        )
