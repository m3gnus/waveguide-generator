"""The CAD-store commit benchmark runs end to end and prints its one table."""

from __future__ import annotations

from scripts import measure_cad_store_commit


def test_prints_one_row_per_mode_and_cleans_up(tmp_path, capsys) -> None:
    assert measure_cad_store_commit.main(
        ["--commits", "3", "--prefill", "2", "--dir", str(tmp_path)]
    ) == 0
    out = capsys.readouterr().out
    for label in ("NORMAL commit (old", "NORMAL commit + checkpoint", "checkpoint alone", "FULL commit (new"):
        assert label in out
    assert list(tmp_path.iterdir()) == []
