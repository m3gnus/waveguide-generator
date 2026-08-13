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
    performance = _between(
        _read("docs/reference/FRAME-SPEC.md"), "## Performance notes"
    )
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
        _read("docs/validation/2026-08/WINDOWS-PERFORMANCE.md"),
        "### 4.3 Parallel sweeps are available, and never silent",
        "### 4.4",
    )
    normalized = " ".join(section.split()).lower()

    assert "one process by default" in normalized
    assert "`WG2_SOLVE_WORKERS=0`" in section
    assert "any positive integer" in normalized
    assert "It now passes the engine's own auto mode" not in section


def test_release_docs_distinguish_current_workflow_from_dated_evidence() -> None:
    readiness = _read("docs/plans/RELEASE-READINESS.md")
    workflow = _read(".github/workflows/ci.yml")
    validation = _read("docs/validation/2026-08/WINDOWS-VALIDATION.md")

    assert "Ubuntu, macOS, Windows, frontend, codec, and drift" in readiness
    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert "Confirm the current GitHub Actions matrix is green" in readiness
    assert "Windows CI leg is written but unexecuted" in validation


def test_spa_docs_distinguish_module_import_from_app_construction() -> None:
    validation = _between(
        _read("docs/validation/2026-08/WINDOWS-VALIDATION.md"),
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
