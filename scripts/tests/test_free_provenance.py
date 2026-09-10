"""Focused checks for the free build provenance workflow wiring.

These checks inspect the real RC and release workflows and the inactive
manual-Beta proposal. They keep the attestation boundary visible: a producing
job signs only files it built locally, before its upload step, while a later
job may consume an attested file without creating a new claim for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
RC_WORKFLOW = ROOT / ".github" / "workflows" / "rc-build.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
PROPOSAL_WORKFLOW = ROOT / "docs" / "reference" / "main-build.workflow-proposal.yml"
ALL_WORKFLOWS = (RC_WORKFLOW, RELEASE_WORKFLOW, PROPOSAL_WORKFLOW)
ATTEST_PIN = "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d"
BUILD_JOBS = ("spa", "macos-bundle", "windows-bundle", "linux-bundle")
ATTESTATION_PERMISSIONS = {"id-token": "write", "attestations": "write"}

#: The workflow-level permissions, and the exact block each build job carries.
#: A job-level block replaces the workflow's rather than adding to it, so a
#: build job restates what it already had and adds only the two attestation
#: permissions. release.yml's SPA job needs actions: read for its CI check.
PERMISSIONS = {
    RC_WORKFLOW: (
        {"contents": "read"},
        {"contents": "read", **ATTESTATION_PERMISSIONS},
    ),
    RELEASE_WORKFLOW: (
        {"actions": "read", "contents": "read"},
        {"actions": "read", "contents": "read", **ATTESTATION_PERMISSIONS},
    ),
    PROPOSAL_WORKFLOW: (
        {"contents": "read"},
        {"contents": "read", **ATTESTATION_PERMISSIONS},
    ),
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job].get("steps") or []


def _attestation(workflow: dict, job: str) -> tuple[int, dict]:
    steps = _steps(workflow, job)
    matches = [
        (index, step)
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/attest@")
    ]
    assert len(matches) == 1, f"{job} has {len(matches)} attestation steps"
    return matches[0]


@pytest.mark.parametrize("path", ALL_WORKFLOWS)
def test_attest_is_pinned_to_the_reviewed_immutable_commit(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert f"uses: {ATTEST_PIN} # v4.2.1" in text

    workflow = _load(path)
    for job in BUILD_JOBS:
        _index, step = _attestation(workflow, job)
        assert step["uses"] == ATTEST_PIN
        # File subjects use the default SLSA build-provenance predicate. A
        # custom predicate would need a separate verification contract.
        assert "predicate-type" not in step.get("with", {})
        assert "predicate-path" not in step.get("with", {})


@pytest.mark.parametrize("path", ALL_WORKFLOWS)
def test_only_producing_jobs_receive_attestation_permissions(path: Path) -> None:
    workflow = _load(path)
    workflow_level, build_job = PERMISSIONS[path]
    assert workflow["permissions"] == workflow_level
    for job in BUILD_JOBS:
        assert workflow["jobs"][job]["permissions"] == build_job, job

    for name, job in workflow["jobs"].items():
        if name in BUILD_JOBS:
            continue
        granted = job.get("permissions") or {}
        assert not set(granted) & set(ATTESTATION_PERMISSIONS), name

    # A publisher keeps only the permission its publication needs. The RC has
    # no publisher job.
    if "publish" in workflow["jobs"]:
        assert workflow["jobs"]["publish"]["permissions"] == {"contents": "write"}


@pytest.mark.parametrize("path", ALL_WORKFLOWS)
def test_source_guard_runs_before_any_build_or_attestation(path: Path) -> None:
    workflow = _load(path)
    jobs = ("identity",) if path == PROPOSAL_WORKFLOW else BUILD_JOBS
    for job in jobs:
        steps = _steps(workflow, job)
        guards = [
            step
            for step in steps
            if step.get("name") == "Verify the candidate matches the workflow source"
        ]
        assert len(guards) == 1, job
        guard = guards[0]
        assert guard["env"]["WORKFLOW_SHA"] == "${{ github.sha }}"
        run = str(guard["run"])
        assert 'resolved="$(git rev-parse HEAD)"' in run
        assert 'test "$resolved" = "$WORKFLOW_SHA"' in run
        guard_index = steps.index(guard)
        if job == "identity":
            first_build = next(
                index for index, step in enumerate(steps) if step.get("id") == "resolve"
            )
        else:
            first_build = min(
                index
                for index, step in enumerate(steps)
                if step.get("name") in {"Build the SPA", "Build and verify the standalone app"}
            )
        assert guard_index < first_build, job


@pytest.mark.parametrize(
    ("path", "subjects"),
    (
        (
            RC_WORKFLOW,
            {
                "spa": ("${{ env.artifact }}",),
                "macos-bundle": (
                    "Waveguide.Generator-*-macos-arm64.dmg",
                    "update-app-*.zip",
                    "update-app-*.manifest.json",
                ),
                "windows-bundle": (
                    "Waveguide.Generator-*-windows-x86_64-setup.exe",
                ),
                "linux-bundle": (
                    "Waveguide.Generator-*-linux-x86_64.tar.gz",
                ),
            },
        ),
        (
            RELEASE_WORKFLOW,
            {
                "spa": ("${{ env.artifact }}",),
                "macos-bundle": (
                    "Waveguide.Generator-*-macos-arm64.dmg",
                    "update-app-*.zip",
                    "update-app-*.manifest.json",
                    "update-runtime-macos-arm64-*.zip",
                ),
                "windows-bundle": (
                    "Waveguide.Generator-*-windows-x86_64-setup.exe",
                    "Waveguide.Generator-*-windows-x86_64.zip",
                    "update-runtime-windows-x86_64-*.zip",
                ),
                "linux-bundle": (
                    "Waveguide.Generator-*-linux-x86_64.tar.gz",
                    "update-runtime-linux-x86_64-*.zip",
                ),
            },
        ),
        (
            PROPOSAL_WORKFLOW,
            {
                "spa": ("${{ env.artifact }}",),
                "macos-bundle": (
                    "Waveguide.Generator-*-macos-arm64.dmg",
                    "update-app-*.zip",
                    "update-app-*.manifest.json",
                    "update-runtime-macos-arm64-*.zip",
                ),
                "windows-bundle": (
                    "Waveguide.Generator-*-windows-x86_64-setup.exe",
                    "Waveguide.Generator-*-windows-x86_64.zip",
                    "update-runtime-windows-x86_64-*.zip",
                ),
                "linux-bundle": (
                    "Waveguide.Generator-*-linux-x86_64.tar.gz",
                    "update-runtime-linux-x86_64-*.zip",
                ),
            },
        ),
    ),
)
def test_each_attestation_precedes_upload_and_names_only_local_outputs(
    path: Path, subjects: dict[str, tuple[str, ...]]
) -> None:
    workflow = _load(path)
    for job, expected_subjects in subjects.items():
        steps = _steps(workflow, job)
        attest_index, attest = _attestation(workflow, job)
        subject_path = str(attest["with"]["subject-path"])
        assert all(expected in subject_path for expected in expected_subjects), job
        # Downloaded inputs are intentionally absent. Attestation must happen
        # for this job's freshly built outputs, never as a post-hoc claim over
        # an old archive copied from another job.
        assert "release-spa" not in subject_path
        assert "build/spa" not in subject_path
        assert "build/app-reference" not in subject_path
        # Payloads, not checksum sidecars.
        assert ".sha256" not in subject_path, job

        uploads = [
            index
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        assert uploads, job
        assert attest_index < min(uploads), job
        if path == RC_WORKFLOW and job != "spa":
            qualification = [
                index
                for index, step in enumerate(steps)
                if str(step.get("name", "")).startswith("Qualify BEAT CPU")
            ]
            assert qualification, job
            assert attest_index < min(qualification), job

    if "publish" in workflow["jobs"]:
        assert not any(
            str(step.get("uses", "")).startswith("actions/attest@")
            for step in _steps(workflow, "publish")
        )


@pytest.mark.parametrize("path", (RC_WORKFLOW, RELEASE_WORKFLOW))
def test_a_windows_setup_is_attested_only_after_its_installer_gates(path: Path) -> None:
    """A setup that fails its gates must never carry a build claim."""

    workflow = _load(path)
    steps = _steps(workflow, "windows-bundle")
    attest_index, _attest = _attestation(workflow, "windows-bundle")
    gates = [
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Run setup-driven Windows installer gates"
    ]
    assert len(gates) == 1
    assert gates[0] < attest_index


def test_the_rc_dispatch_input_says_it_must_be_the_dispatch_ref_tip() -> None:
    """The guard makes `--ref main -f sha=<branch tip>` fail; say so where it is typed."""

    workflow = _load(RC_WORKFLOW)
    # PyYAML reads the bare `on:` key as boolean True.
    trigger = workflow.get("on", workflow.get(True))
    description = trigger["workflow_dispatch"]["inputs"]["sha"]["description"]
    assert "tip of the dispatch ref" in description
    assert "--ref <branch>" in RC_WORKFLOW.read_text(encoding="utf-8")


def test_manual_beta_attests_the_manifest_that_carries_stamped_identity() -> None:
    workflow = _load(PROPOSAL_WORKFLOW)
    _index, macos_attestation = _attestation(workflow, "macos-bundle")
    subjects = str(macos_attestation["with"]["subject-path"])
    assert "update-app-*.manifest.json" in subjects

    text = PROPOSAL_WORKFLOW.read_text(encoding="utf-8")
    assert "--source-commit \"$SOURCE\"" in text
    assert "sourceCommit" in text
    assert "treeSha256" in text
    assert "does not make the updater enforce" in text


def test_verification_document_uses_repository_signer_and_source_constraints() -> None:
    text = (ROOT / "docs" / "reference" / "BUILD-PROVENANCE.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "gh attestation verify",
        "--repo m3gnus/waveguide-generator",
        "--signer-workflow",
        "m3gnus/waveguide-generator/.github/workflows/rc-build.yml",
        "m3gnus/waveguide-generator/.github/workflows/release.yml",
        "--source-digest",
        "--format json",
    ):
        assert required in text
    assert "updater enforce signatures" in text
