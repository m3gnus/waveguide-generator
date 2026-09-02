"""Keep reviewed operational documentation aligned with the shipped paths."""

from __future__ import annotations

import json
import platform
from pathlib import Path
import subprocess


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
    documentation_index = _read("docs/README.md")
    workflow = _read(".github/workflows/ci.yml")
    validation = _read("docs/validation/2026-08/WINDOWS-VALIDATION.md")

    assert "workspace-local" in documentation_index
    assert "Maintainer backlogs" in documentation_index
    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert "Current gates are maintained in the workspace-local" in validation
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


def _console_command(document: str, heading: str) -> str:
    section = document.split(heading, 1)[1]
    return section.split("```console", 1)[1].split("```", 1)[0].strip()


def _identity_command_for_platform(report: str, system: str) -> str:
    heading = (
        "**Windows Command Prompt**"
        if system == "Windows"
        else "**POSIX (macOS/Linux)**"
    )
    return _console_command(report, heading)


def test_wall_clearance_identity_command_selection_is_platform_specific() -> None:
    report = _read("docs/validation/2026-08/WALL-CLEARANCE-ACOUSTICS.md")
    posix = _identity_command_for_platform(report, "Darwin")
    windows = _identity_command_for_platform(report, "Windows")

    assert posix.startswith("python3.13 -m scripts.verify_model_identity ")
    assert windows.startswith("py -3.13 -m scripts.verify_model_identity ")
    assert _identity_command_for_platform(report, "Linux") == posix
    assert posix != windows


def test_wall_clearance_identity_command_runs_on_the_host_platform() -> None:
    report = _read("docs/validation/2026-08/WALL-CLEARANCE-ACOUSTICS.md")
    command = _identity_command_for_platform(report, platform.system())
    completed = subprocess.run(
        command,
        cwd=ROOT,
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(
        _read("docs/validation/2026-08/wall-clearance-model-identity.json")
    )
    assert f"sha256={manifest['sha256']}" in completed.stdout


def test_wall_clearance_report_limits_claims_to_committed_evidence() -> None:
    report = _read("docs/validation/2026-08/WALL-CLEARANCE-ACOUSTICS.md")
    index = _read("docs/validation/2026-08/README.md")
    combined = " ".join(f"{report}\n{index}".lower().split())

    assert "verdict: **pass**" not in combined
    assert "proving that" not in combined
    assert "non-reproducible local observation" in combined
    assert "no mesh-refinement or convergence run" in combined
    assert "unresolved and uninterpretable" in combined
    assert "do not independently validate" in combined

def test_side_by_side_v1_migration_requires_explicit_provenance() -> None:
    readme = _between(
        _read("README.md"), "### Original-app run migration", "## Run the server directly"
    )

    assert "including a v1 checkout in a sibling folder" in readme
    assert "set `WG1_ROOT`" in readme
    assert "Automatic sibling discovery is deliberately disabled" in " ".join(
        readme.split()
    )
    assert "looks for" in readme and "in sibling checkout folders" not in readme


def test_user_guide_distinguishes_startup_returns_from_new_arrivals() -> None:
    guide = _read("docs/USER-GUIDE.md")
    workflow = _between(
        guide,
        "A newly arriving return",
        "### Starting from a model drawn in Fusion",
    )
    normalized = " ".join(workflow.split())

    assert "manually select from the History list" in normalized
    assert "prepared automatically" in normalized
    assert "On startup" in normalized
    assert "Ready to prepare" in normalized
    assert "Prepare simulation" in normalized
    assert "appears only as the retry" not in normalized


def test_the_three_gatekeeper_texts_say_the_same_thing() -> None:
    """README, release notes and the in-image readme are one instruction.

    They disagreed once already, and the way it happened is instructive: the
    backlog recorded "Privacy & Security > Open Anyway" as the route that works
    for the app while the shipped texts recorded, correctly, that the app is
    never listed there. A user following the wrong half is stuck with no next
    step, and nothing in the build would have caught it.

    Measured 2026-09-02 on macOS 26.5.2 against a genuinely quarantined download:
    the ad-hoc signed .app assesses `rejected` with no `source` line, so it gets
    no override; the unsigned installer script assesses
    `rejected  source=no usable signature`, which is the state an override
    attaches to. See docs/validation/2026-09/MACOS-GATEKEEPER.md.
    """

    from scripts.build_bundle import BundleBuilder

    surfaces = {
        "README.md": _read("README.md"),
        "release notes": _read(".github/workflows/release.yml"),
        "READ ME FIRST.txt": BundleBuilder(
            ROOT, system=lambda: "Darwin", machine=lambda: "arm64"
        ).dmg_readme(),
    }

    for name, text in surfaces.items():
        # The route that needs no Terminal, named exactly as the file shipped.
        assert BundleBuilder.DMG_INSTALLER_NAME in text, name
        assert "Open Anyway" in text, name
        assert "Privacy & Security" in text, name
        # The fallback, still exact and still copy-pasteable.
        assert (
            'xattr -dr com.apple.quarantine "/Applications/Waveguide Generator.app"' in text
        ), name

    # And each must say, in as many words, that the app itself is NOT listed
    # there -- otherwise a reader sends themselves to Privacy & Security looking
    # for the app, finds nothing, and has no next step. That is the exact
    # sentence the backlog had backwards.
    for name, text in surfaces.items():
        normalized = " ".join(text.split()).lower()
        assert "not list" in normalized, name
