"""Byte compatibility of older user designs, frozen on WG 0.3.6's mesher."""

import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from server.tests import freeform_oldconfig_support as support
from server.tests._frozen_bytes import off_frozen_platform
from server.design.textcfg import parse, serialize
from server.tests.freeform_oldconfig_support import (
    CASES,
    DATA,
    MANIFEST,
    canonical,
    config_snapshot,
    load_case,
    normalize_step,
    observe_artifact,
    roundtrip_state,
)


@pytest.fixture(params=[False, True], ids=["original", "design-id"])
def stamped_corpus(request, monkeypatch):
    if not request.param:
        return
    original = support.load_case

    def load_stamped(case):
        path = DATA / case["file"]
        stamp = "; Waveguide Generator design-id: wgu_01K00000000000000000000000\n"
        if path.suffix == ".json":
            # JSON DesignConfig cannot carry comments. Prove stamp isolation on
            # its text representation, then retain the original typed input for
            # frozen builds (a text rewrite materializes compatibility defaults).
            design = original(case)
            text = serialize(design)
            assert parse(stamp + text).semantic_data() == parse(text).semantic_data()
            return design
        return parse(stamp + path.read_text()).design

    monkeypatch.setattr(support, "load_case", load_stamped)


def expected(case, kind):
    return json.loads((DATA / "expected" / case["name"] / f"{kind}.json").read_text())


@pytest.fixture(autouse=True)
def bounded_native_builds(monkeypatch):
    # The real killable worker supplies the timeout even under the suite's
    # ambient in-process mode. Never patch or substitute a geometry builder.
    monkeypatch.setenv("WG2_TEST_MESH_IN_PROCESS", "0")
    monkeypatch.setenv("WG2_MESH_BUILD_TIMEOUT_S", "45")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_resolved_config_and_no_silent_migration(case, stamped_corpus):
    frozen = expected(case, "config")
    assert canonical(config_snapshot(case)) == canonical(frozen), f"{case['name']}: config differs"
    if "error" not in frozen:
        assert roundtrip_state(load_case(case)) == frozen["freeform"], (
            f"{case['name']}: FREEFORM t, angles or stations changed on save/reload"
        )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(case, id=case["name"], marks=pytest.mark.slow if case["slow_mesh"] else ())
        for case in CASES
    ],
)
def test_solve_mesh_bytes(case, stamped_corpus):
    frozen = expected(case, "mesh")
    actual = asyncio.run(observe_artifact(case, "mesh"))
    # Refusals are platform-independent; produced bytes are not.
    elsewhere = None if "error" in frozen else off_frozen_platform(MANIFEST["reference"]["platform"])
    if elsewhere:
        assert "error" not in actual, f"{case['name']}: mesh is now refused: {actual}"
        pytest.skip(elsewhere)
    assert actual == frozen, f"{case['name']}: mesh differs: {actual}"


@pytest.mark.slow
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_step_bytes(case, stamped_corpus):
    frozen = expected(case, "step")
    actual = asyncio.run(observe_artifact(case, "step"))
    # Refusals are platform-independent; produced bytes are not.
    elsewhere = None if "error" in frozen else off_frozen_platform(MANIFEST["reference"]["platform"])
    if elsewhere:
        assert "error" not in actual, f"{case['name']}: STEP is now refused: {actual}"
        pytest.skip(elsewhere)
    assert actual == frozen, f"{case['name']}: STEP differs: {actual}"


def test_step_normalization_keeps_geometry_version_and_product_suffixes():
    first = (
        "FILE_NAME('Open CASCADE Shape Model','2026-10-21T12:00:00');\n"
        "#1=PRODUCT('Open CASCADE STEP translator 7.8 1.2');\n"
        "#2=NEXT_ASSEMBLY_USAGE_OCCURRENCE('9','','',#5,#255,$);\n"
        "#3=NEXT_ASSEMBLY_USAGE_OCCURRENCE('10','','',#5,#978,$);\n"
        "#4=CARTESIAN_POINT('',(1.234,5.678,9.012));\n"
    )
    second = (
        first.replace("12:00:00", "13:01:02")
        .replace("7.8 1.2", "7.8 9.2")
        .replace("('9'", "('29'")
        .replace("('10'", "('30'")
    )
    assert normalize_step(first) == normalize_step(second)
    for changed in (
        first.replace("1.234", "1.235"),
        first.replace("7.8", "7.9"),
        first.replace("1.2'", "1.3'"),
        first.replace("#978", "#979"),
        first.replace("('10'", "('11'"),
    ):
        assert normalize_step(first) != normalize_step(changed)


def test_step_normalization_requires_one_timestamp():
    with pytest.raises(ValueError, match="exactly one"):
        normalize_step("ISO-10303-21; DATA; ENDSEC;")


def test_frozen_fixture_regeneration_requires_explicit_overwrite():
    path = DATA / "expected" / "FF01-default" / "config.json"
    before = path.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/freeze_freeform_oldconfig_corpus.py",
            "--case",
            "FF01-default",
            "--artifact",
            "config",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "refusing to overwrite" in result.stderr
    assert path.read_bytes() == before
