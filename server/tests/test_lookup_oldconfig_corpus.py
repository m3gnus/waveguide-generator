"""Saved LOOKUP designs retain their pre-custom-profile geometry."""

import asyncio
import json
from pathlib import Path

import pytest

from server.design.textcfg import parse, serialize
from server.tests._frozen_bytes import off_frozen_platform
from server.tests.freeform_oldconfig_support import artifact, canonical, resolved_config
from server.tests.test_freeform_oldconfig_corpus import bounded_native_builds  # noqa: F401


DATA = Path(__file__).parent / "data" / "lookup_oldconfig_corpus"
CASES = ("implicit-dimensions", "morph-snapping", "scale-source-quarter", "large-table")


def frozen(name):
    return json.loads((DATA / f"{name}.json").read_text())


def load_design(case):
    return parse(json.dumps(case["design"])).design


@pytest.mark.parametrize("name", CASES)
def test_lookup_saved_design_reopens_and_keeps_its_resolved_config(name):
    case = frozen(name)
    design = load_design(case)
    assert canonical(resolved_config(design)) == canonical(case["config"])
    reopened = parse(serialize(design)).design
    assert reopened.root.lookup_profile == design.root.lookup_profile
    assert canonical(resolved_config(reopened)) == canonical(case["config"])


@pytest.mark.slow
@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("kind", ("mesh", "step"))
def test_lookup_saved_design_keeps_frozen_artifact(name, kind):
    case = frozen(name)
    actual = asyncio.run(artifact(load_design(case), kind))
    elsewhere = off_frozen_platform("Darwin-arm64")
    if elsewhere:
        pytest.skip(elsewhere)
    assert actual == case[kind], f"{name}: {kind} differs: {actual}"
