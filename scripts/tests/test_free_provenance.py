"""Focused checks for the free build provenance workflow wiring.

These checks inspect the real RC and release workflows and the inactive
manual-Beta proposal. They keep the attestation boundary visible: a producing
job signs only files it built locally, before its upload step, while a later
job may consume an attested file without creating a new claim for it.

The assertions are exact on purpose. The pinned action fails only when every
subject line together matches nothing, so a single mistyped line silently drops
one file from the attestation; a substring check would not see that.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

from shared import release_assets


ROOT = Path(__file__).resolve().parents[2]
RC_WORKFLOW = ROOT / ".github" / "workflows" / "rc-build.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
PROPOSAL_WORKFLOW = ROOT / "docs" / "reference" / "main-build.workflow-proposal.yml"
ALL_WORKFLOWS = (RC_WORKFLOW, RELEASE_WORKFLOW, PROPOSAL_WORKFLOW)
ATTEST_PIN = "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d"
BUILD_JOBS = ("spa", "macos-bundle", "windows-bundle", "linux-bundle")
GUARD = "Verify the candidate matches the workflow source"

ATTESTING = {"id-token": "write", "attestations": "write"}
RC_BUILD = {"contents": "read", **ATTESTING}
# A job-level block replaces the workflow's rather than adding to it, so each
# release build job restates actions: read, which the SPA job's CI check needs.
RELEASE_BUILD = {"actions": "read", "contents": "read", **ATTESTING}
PUBLISH = {"contents": "write"}

#: Every job each workflow has, and its exact permission block (None: inherits
#: the workflow's). Exact, so a job that gains anything is caught.
JOB_PERMISSIONS = {
    RC_WORKFLOW: dict.fromkeys(BUILD_JOBS, RC_BUILD),
    RELEASE_WORKFLOW: {**dict.fromkeys(BUILD_JOBS, RELEASE_BUILD), "publish": PUBLISH},
    PROPOSAL_WORKFLOW: {
        "identity": None,
        **dict.fromkeys(BUILD_JOBS, RC_BUILD),
        "publish": PUBLISH,
    },
}
WORKFLOW_PERMISSIONS = {
    RC_WORKFLOW: {"contents": "read"},
    RELEASE_WORKFLOW: {"actions": "read", "contents": "read"},
    PROPOSAL_WORKFLOW: {"contents": "read"},
}

VERSION = "9.8.7-rc.6"
RUNTIME_ID = "0123456789ab"
MACOS = release_assets.MACOS_PLATFORM
WINDOWS = release_assets.WINDOWS_PLATFORM
LINUX = release_assets.LINUX_PLATFORM

DMG = release_assets.installer_name(MACOS, VERSION)
APP_LAYER = release_assets.app_layer_name(VERSION)
APP_MANIFEST = release_assets.app_manifest_name(VERSION)
SETUP = release_assets.windows_setup_name(VERSION)
PORTABLE = release_assets.installer_name(WINDOWS, VERSION)
TARBALL = release_assets.installer_name(LINUX, VERSION)
RUNTIMES = {
    platform: release_assets.runtime_layer_name(platform, RUNTIME_ID)
    for platform in (MACOS, WINDOWS, LINUX)
}

#: What each bundle job must attest, by the names the builder gives the files.
RC_SUBJECTS = {
    "macos-bundle": {DMG, APP_LAYER, APP_MANIFEST},
    "windows-bundle": {SETUP},
    "linux-bundle": {TARBALL},
}
RELEASE_SUBJECTS = {
    "macos-bundle": {DMG, APP_LAYER, APP_MANIFEST, RUNTIMES[MACOS]},
    "windows-bundle": {SETUP, PORTABLE, RUNTIMES[WINDOWS]},
    "linux-bundle": {TARBALL, RUNTIMES[LINUX]},
}
SUBJECTS = {
    RC_WORKFLOW: RC_SUBJECTS,
    RELEASE_WORKFLOW: RELEASE_SUBJECTS,
    PROPOSAL_WORKFLOW: RELEASE_SUBJECTS,
}

#: Every file a bundle job can leave in build/bundle, checksum sidecars
#: included, so a glob that also reaches a sidecar or a neighbour is caught.
_PAYLOADS = {DMG, APP_LAYER, APP_MANIFEST, SETUP, PORTABLE, TARBALL, *RUNTIMES.values()}
BUNDLE_FILES = _PAYLOADS | {release_assets.checksum_name(name) for name in _PAYLOADS}


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


def _subject_lines(step: dict) -> list[str]:
    return [
        line.strip()
        for line in str(step["with"]["subject-path"]).splitlines()
        if line.strip()
    ]


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
        assert set(step["with"]) == {"subject-path"}, job


@pytest.mark.parametrize("path", ALL_WORKFLOWS)
def test_an_attestation_can_never_be_skipped_or_allowed_to_fail(path: Path) -> None:
    workflow = _load(path)
    for job in BUILD_JOBS:
        _index, step = _attestation(workflow, job)
        assert "if" not in step, job
        assert "continue-on-error" not in step, job
        assert "continue-on-error" not in workflow["jobs"][job], job


@pytest.mark.parametrize("path", ALL_WORKFLOWS)
def test_every_job_has_exactly_its_permissions(path: Path) -> None:
    workflow = _load(path)
    assert workflow["permissions"] == WORKFLOW_PERMISSIONS[path]
    expected = JOB_PERMISSIONS[path]
    assert set(workflow["jobs"]) == set(expected)
    for job, permissions in expected.items():
        assert workflow["jobs"][job].get("permissions") == permissions, job


@pytest.mark.parametrize("path", ALL_WORKFLOWS)
def test_the_source_guard_is_the_step_straight_after_checkout(path: Path) -> None:
    workflow = _load(path)
    jobs = ("identity",) if path == PROPOSAL_WORKFLOW else BUILD_JOBS
    for job in jobs:
        steps = _steps(workflow, job)
        assert str(steps[0].get("uses", "")).startswith("actions/checkout@"), job
        guard = steps[1]
        assert guard.get("name") == GUARD, job
        assert [step.get("name") for step in steps].count(GUARD) == 1, job
        assert guard["env"]["WORKFLOW_SHA"] == "${{ github.sha }}"
        run = str(guard["run"])
        assert 'resolved="$(git rev-parse HEAD)"' in run
        assert 'test "$resolved" = "$WORKFLOW_SHA"' in run


def test_the_rc_resolves_its_input_once_and_later_jobs_build_that_commit() -> None:
    """`main` can move during an RC; only the SPA job may resolve the input."""

    workflow = _load(RC_WORKFLOW)
    refs = {job: _steps(workflow, job)[0]["with"]["ref"] for job in BUILD_JOBS}
    assert refs == {
        "spa": "${{ inputs.sha }}",
        "macos-bundle": "${{ github.sha }}",
        "windows-bundle": "${{ github.sha }}",
        "linux-bundle": "${{ github.sha }}",
    }
    for job in BUILD_JOBS[1:]:
        assert "spa" in workflow["jobs"][job]["needs"], job


@pytest.mark.parametrize("path", ALL_WORKFLOWS)
def test_each_job_attests_exactly_its_own_payloads(path: Path) -> None:
    workflow = _load(path)
    _index, spa = _attestation(workflow, "spa")
    assert _subject_lines(spa) == ["${{ env.artifact }}"]

    for job, expected in SUBJECTS[path].items():
        _index, attest = _attestation(workflow, job)
        attested: set[str] = set()
        for line in _subject_lines(attest):
            # Only this job's fresh outputs: never a downloaded input such as
            # build/spa or build/app-reference.
            assert line.startswith("build/bundle/"), (job, line)
            matched = {
                name
                for name in BUNDLE_FILES
                if fnmatchcase(name, line.removeprefix("build/bundle/"))
            }
            # One file per line: a line matching nothing is dropped silently
            # at runtime, and one matching several reaches a sidecar or a file
            # another job owns.
            assert len(matched) == 1, (job, line, sorted(matched))
            attested |= matched
        assert attested == expected, job


@pytest.mark.parametrize("path", ALL_WORKFLOWS)
def test_each_attestation_precedes_every_upload(path: Path) -> None:
    workflow = _load(path)
    for job in BUILD_JOBS:
        steps = _steps(workflow, job)
        attest_index, _attest = _attestation(workflow, job)
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
    assert "build/bundle/update-app-*.manifest.json" in _subject_lines(macos_attestation)

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
        ".[].verificationResult.statement",
    ):
        assert required in text
    assert "updater enforce signatures" in text
