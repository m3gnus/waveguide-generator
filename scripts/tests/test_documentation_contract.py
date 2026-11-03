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


def _words(relative: str, start: str, end: str | None = None) -> str:
    return " ".join(_between(_read(relative), start, end).split())


def test_doc_01_normalization_distinguishes_recorded_and_legacy_runs() -> None:
    section = _words("docs/USER-GUIDE.md", "### Where the microphone is", "### Reading group delay")
    assert "recorded normalization angle keeps that reference" in section
    assert "Legacy runs without a recorded angle follow the live setting for their model kind" in section
    assert "New solves record the current angle" in section
    assert "also re-references runs that were solved earlier" not in section


def test_doc_02_auto_accuracy_and_override_rules() -> None:
    section = _words("docs/USER-GUIDE.md", "### What each backend can solve", "BEMPP prefers")
    assert "Fast + AUTO" in section
    assert "Metal → BEAT · CUDA → BEAT · ROCm → BEAT · Metal → BEAT · CPU → BEMPP" in section
    assert "Accurate + AUTO" in section
    assert "BEAT · Metal → BEAT · CUDA → BEAT · ROCm → BEAT · CPU" in section
    assert "does not fall back to native Metal or BEMPP" in section
    assert "Changing **Accuracy** resets **Advanced backend** to **Automatic**" in section
    assert "non-BEAT override resets Accuracy to **Fast**" in section


def test_doc_03_runtime_setup_uses_selection_and_readiness_actions() -> None:
    section = _words("docs/USER-GUIDE.md", "### What each backend can solve", "BEMPP prefers")
    assert "select a runtime that can prepare to start setup" in section
    assert "**Prepare … and solve**" in section
    assert "selectable while unprepared or preparing" in section
    assert "CAD picker hides refused engines unless they are already selected" in section
    assert "beside **Advanced backend**" not in section
    assert "becomes selectable when its verification finishes" not in section
    assert "**Prepare** / **Retry** controls" not in section


def test_doc_05_geometry_exports_distinguish_solid_and_inner_surface() -> None:
    section = _words("docs/USER-GUIDE.md", "Result curves and run archives", "### Optional solve surface")
    assert "**STEP solid** and **Send to CAD** carry the horn and enclosure only" in section
    assert "**STL** and **STEP inner surface** carry only the horn's inner surface" in section
    assert "without the enclosure, woofers or baffle cutouts" in section


def test_doc_06_isolated_runs_and_settings_labels() -> None:
    section = _words("docs/USER-GUIDE.md", "### WG folder", "### The run archive")
    assert "fresh isolated setups also use `<data>/workspace` for runs" in section
    assert "rather than `<data>/workspace-root/runs`" in section
    install = _words("docs/INSTALL.md", "### Output workspace", "## Run the server directly")
    assert "**Settings → WG folder**" in install
    assert "**Settings → Workspace**" not in install
    assert "`<data dir>/workspace`" in install


def test_doc_07_cad_status_and_solve_live_in_results() -> None:
    section = _words("docs/CAD-LINK-GUIDE.md", "## 6. In WG:", "## 7.")
    assert "In **Results**, **one status line**" in section
    assert "**Solve** on this Results status line" in section
    assert "Under the model's name" not in section
    assert "has no Solve button" not in section


def test_doc_08_manual_describes_file_mesh_cutting_and_fallback() -> None:
    section = _words("docs/USER-MANUAL.md", "## 3. Import", "## 4.")
    assert "**Auto · cut for speed**" in section
    assert "**5,000 input triangles**" in section
    assert "explicit half or quarter" in section
    assert "valid full mesh remains ready to solve" in section
    assert "cannot be cut automatically" not in section


def test_doc_09_installed_and_checkout_launch_modes() -> None:
    section = _words("docs/USER-GUIDE.md", "## Launching", "## Design and preview")
    assert "Installed builds on macOS, Windows and Linux open a native desktop window by default" in section
    assert "Linux Qt check fails" in section
    assert "`--browser`" in section and "`--no-gui`" in section
    assert "Source-checkout scripts" in section and "`launchers.statusapp`" in section
    assert "native window is unavailable in this release" not in section


def test_doc_10_fusion_command_labels_and_automatic_update() -> None:
    manual = " ".join(_read("docs/USER-MANUAL.md").split())
    guide = " ".join(_read("docs/CAD-LINK-GUIDE.md").split())
    for text in (manual, guide):
        assert "**Show Panel**" in text
        assert "**Advanced → Send to WG**" in text
        assert "Requests…" not in text
        assert "Manage Link…" not in text
    assert "**Advanced → Insert**" in manual
    assert "automatically updates the linked waveguide" in guide
    assert "**Advanced → Update**" in guide
    assert "send it again and use **Update**" not in guide


def test_doc_12_cycles_increase_with_frequency_for_fixed_delay() -> None:
    section = _words("docs/USER-GUIDE.md", "### Reading group delay", "### Seeing the measurement")
    assert "0.3 cycles at 1 kHz and 3 cycles at 10 kHz" in section
    assert "fixed delay spans more periods at higher frequencies" in section
    assert "negligible up high" not in section
    assert "usually the reading a group delay is judged by" not in section


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
    assert '"ubuntu-latest","macos-latest","windows-latest"' in workflow
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

def test_user_guide_says_fusion_models_prepare_automatically() -> None:
    guide = _read("docs/USER-GUIDE.md")
    workflow = _between(
        guide,
        "WG prepares a Fusion model automatically",
        "### Starting from a model drawn in Fusion",
    )
    normalized = " ".join(workflow.split())

    assert "when it arrives" in normalized
    assert "under **Model versions**" in normalized
    assert "after a restart" in normalized
    assert "**Try again** after a failure" in normalized
    assert "Prepare simulation" not in guide
    assert "Rebuild mesh" not in normalized


def test_the_three_gatekeeper_texts_say_the_same_thing() -> None:
    """README, release notes and the in-image readme are one instruction.

    They disagreed once already, and the way it happened is instructive. An
    `spctl` difference -- the ad-hoc .app assessing `rejected` with no `source`
    line, an unsigned script assessing `rejected  source=no usable signature`
    (2026-09-02, macOS 26.5.2) -- was read as proving the app would never be
    listed under Privacy & Security and the script always would. The shipped
    texts said so, and the backlog said the opposite; a user following either
    half alone could be stranded, and nothing in the build compared them.

    Both halves were wrong. Two real installs on 2026-09-06 listed and opened
    the app, and listed and opened the script. So the contract is no longer
    "agree on which item is listed" -- it is that all three describe every
    route and none of them forecloses one. See
    docs/validation/2026-09/MACOS-GATEKEEPER.md.
    """

    from scripts.build_bundle import BundleBuilder

    surfaces = {
        "README.md": _read("README.md"),
        "docs/INSTALL.md": _read("docs/INSTALL.md"),
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

    # Each must also route the reader to Applications, so all three really are
    # the same instruction and not three different ones that happen to share a
    # filename.
    for name, text in surfaces.items():
        assert "Applications" in text, name

    # And none of them may promise which items macOS will offer an override for.
    # That promise has now been made in both directions and refuted in both: the
    # shipped texts asserted the app is never listed, the backlog asserted it
    # always is, and real installs contradicted each. Whoever gets the other
    # outcome is stranded by a text that forecloses it, so predicting the
    # outcome -- either way -- is the defect this guards.
    forbidden = (
        "does not list the app",
        "will not list",
        "not list the app",
        "never listed",
        "cannot be approved",
        "where the app does not",
    )
    for name, text in surfaces.items():
        normalized = " ".join(text.split()).lower()
        for phrase in forbidden:
            assert phrase not in normalized, f"{name}: {phrase!r}"


def test_the_readme_never_sends_anyone_to_open_anyway_for_the_app() -> None:
    """The correction on 2026-08-27 fixed one of the two places that said it.

    The macOS install section was rewritten that day to say Privacy & Security
    lists nothing for an ad-hoc bundle. The Launch section, a hundred lines down,
    kept the original advice -- Control-click then Open, and failing that
    "System Settings -> Privacy & Security and choose Open Anyway for the app" --
    and shipped that way in 0.3.0. Sequoia removed the Control-click bypass, so
    that half was wrong outright. (The Open Anyway half turned out to be true
    for a downloaded app -- see the gatekeeper-texts test -- but it is still
    wrong HERE, for the reason below.)

    It is also advice for a wall that a checkout does not hit. Measured
    2026-09-02 on macOS 26.5.2: the launcher app is a script bundle, unsigned,
    and `git clone` sets no com.apple.quarantine, so LaunchServices opens it with
    no dialog at all. Gatekeeper assesses quarantined items; nothing else.
    """

    launch = _between(_read("docs/INSTALL.md"), "## Launch", "## Run the server directly (dev)")
    normalized = " ".join(launch.split())

    assert "Open Anyway" not in launch
    assert "Control-click" not in launch
    assert "does not set it" in normalized
    assert "no dialog" in normalized
