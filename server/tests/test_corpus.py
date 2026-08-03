"""Full read-only v1 corpus migration and round-trip contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.design.textcfg import parse, serialize


ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT.parent / "Waveguide Generator"
SNAPSHOTS = sorted((V1 / "output").glob("*/script.snapshot.mwg"))
ATH_FIXTURES = sorted((V1 / "tests/fixtures/ath").glob("*"))
CORPUS = SNAPSHOTS + ATH_FIXTURES


def _id(path: Path) -> str:
    return str(path.relative_to(V1))


def test_corpus_inventory_is_complete() -> None:
    assert len(SNAPSHOTS) == 191
    assert len(ATH_FIXTURES) == 3
    assert len(set(CORPUS)) == len(CORPUS)


@pytest.mark.parametrize("path", CORPUS, ids=_id)
def test_corpus_parse_validate_round_trip(path: Path) -> None:
    source = path.read_text()
    first = parse(source)
    category = (
        "migrated"
        if first.migrations
        else "unknown-blocks-preserved"
        if first.extra_blocks or first.extra_keys
        else "clean"
    )
    assert category in {"clean", "migrated", "unknown-blocks-preserved"}
    emitted = serialize(first)
    second = parse(emitted)
    assert emitted == source
    assert first.semantic_data() == second.semantic_data()

