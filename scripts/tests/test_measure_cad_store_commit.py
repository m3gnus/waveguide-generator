"""The CAD-store commit benchmark runs end to end and prints its one table."""

from __future__ import annotations

import pytest

from scripts import measure_cad_store_commit


@pytest.mark.parametrize("extra", [[], ["--fullfsync"]])
def test_prints_one_row_per_mode_and_cleans_up(tmp_path, capsys, extra) -> None:
    assert measure_cad_store_commit.main(
        ["--boundaries", "3", "--per-boundary", "2", "--prefill", "2", "--dir", str(tmp_path), *extra]
    ) == 0
    out = capsys.readouterr().out
    for label in ("old: 2 NORMAL commits + checkpoint", "new: 2 FULL commits", "FULL, one commit",
                  "checkpoint alone"):
        assert label in out
    assert list(tmp_path.iterdir()) == []


def test_interleaves_one_boundary_of_each_mode_per_round(tmp_path) -> None:
    times = measure_cad_store_commit.measure(
        tmp_path, boundaries=4, per_boundary=3, prefill=0, fullfsync=False
    )
    assert len(times["old"]) == len(times["new"]) == len(times["checkpoint"]) == 4
    assert len(times["commit"]) == 12
