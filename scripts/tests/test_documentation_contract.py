"""Keep reviewed operational documentation aligned with the shipped paths."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _between(text: str, start: str, end: str | None = None) -> str:
    section = text.split(start, 1)[1]
    return section if end is None else section.split(end, 1)[0]


def test_frame_performance_note_describes_current_preview_frames() -> None:
    performance = _between(_read("docs/FRAME-SPEC.md"), "## Performance notes")
    launcher = _read("launch/serve.py")

    assert "0.23–0.64 MB" in performance
    assert "1.06–3.05 MB" in performance
    assert "2.04 ms" in performance
    assert "7–19 MB/s" in performance
    assert "170–335 KB" not in performance
    assert "0.23-0.64 MB" in launcher
    assert "1.06-3.05 MB" in launcher
    assert "23 ms median" in launcher
    assert "170-335 kB" not in launcher


def test_windows_performance_describes_serial_sweep_default() -> None:
    section = _between(
        _read("docs/WINDOWS-PERFORMANCE.md"),
        "### 4.3 Parallel sweeps are available, and never silent",
        "### 4.4",
    )
    normalized = " ".join(section.split()).lower()

    assert "one process by default" in normalized
    assert "`WG2_SOLVE_WORKERS=0`" in section
    assert "any positive integer" in normalized
    assert "It now passes the engine's own auto mode" not in section


def test_cutover_docs_describe_the_written_windows_ci_leg() -> None:
    cutover = _read("docs/P6-CUTOVER-PLAN.md")
    ci_section = _between(cutover, "### P6.3", "### P6.4")
    windows_section = _between(cutover, "### P6.4", "### P6.5")
    validation = _read("docs/WINDOWS-VALIDATION.md")
    normalized_ci = " ".join(ci_section.split())

    assert "ubuntu + macos + windows" in normalized_ci
    assert "Windows job" not in ci_section
    assert "no Windows CI job" not in windows_section
    assert "Windows CI leg is written but unexecuted" in validation


def test_spa_docs_distinguish_module_import_from_app_construction() -> None:
    validation = _between(
        _read("docs/WINDOWS-VALIDATION.md"),
        "### 2.1 Node was not an obstacle, but it is still required",
        "### 2.2",
    )
    workflow = _read(".github/workflows/ci.yml")
    normalized = " ".join(validation.split())

    assert "module still imports" in normalized
    assert "`create_app()`" in validation
    assert "server cannot start" in validation
    assert "will not even import" not in validation
    assert "`create_app()`" in workflow
    assert "at import" not in workflow
    assert "cannot even collect" not in workflow
