"""The inactive main-build workflow proposal, held to being a real one.

`docs/reference/main-build.workflow-proposal.yml` is a complete workflow that
deliberately lives outside `.github/workflows/`, so GitHub never registers it
and it has no trigger, token or permission until someone moves it. That makes it
reviewable and unrunnable at the same time -- and unrunnable means nothing
executes it, so nothing else would notice if it stopped being coherent.

These are what notice: that every job it needs exists and has steps, that the
artifacts it passes between jobs match by name, that the asset globs match the
names `shared/release_assets.py` actually produces, that it builds one resolved
commit rather than a moving ref, and that the flags it calls exist in the
scripts it calls them on.

What they do NOT prove, and no test on this machine can: that Inno Setup
compiles the script, that macOS packages and notarizes the app, or that a
published main build installs. Those need the runners and a publication
decision. See `docs/reference/UPDATE-CHANNELS.md`.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = REPO_ROOT / "docs" / "reference" / "main-build.workflow-proposal.yml"
RC_BUILD = REPO_ROOT / ".github" / "workflows" / "rc-build.yml"

sys.path.insert(0, str(REPO_ROOT))

from shared import release_assets  # noqa: E402

#: The version a run of this workflow would name itself, for checking that the
#: globs it publishes match the files the builder writes under that name.
SAMPLE_VERSION = "0.4.0-main.7"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def proposal() -> dict:
    return _load(PROPOSAL)


def _steps(job: dict) -> list[dict]:
    return job.get("steps") or []


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def test_the_proposal_is_not_a_workflow_yet() -> None:
    """The whole point: reviewable, and inert until someone decides otherwise."""

    assert PROPOSAL.parent == REPO_ROOT / "docs" / "reference"
    registered = {path.name for path in (REPO_ROOT / ".github" / "workflows").iterdir()}
    assert PROPOSAL.name not in registered


def test_every_job_it_needs_exists_and_does_something(proposal: dict) -> None:
    """A `needs` pointing at a comment is a template that cannot run.

    This is the check the first draft failed: `publish` waited on four build
    jobs that were prose, so the graph looked complete and had one job in it.
    """

    jobs = proposal["jobs"]
    assert set(jobs) == {
        "identity",
        "spa",
        "macos-bundle",
        "windows-bundle",
        "linux-bundle",
        "publish",
    }
    for name, job in jobs.items():
        assert _steps(job), f"{name} has no steps"
        for required in _needs(job):
            assert required in jobs, f"{name} needs {required}, which does not exist"
    # And the publish job waits for all four platforms plus the identity.
    assert set(_needs(jobs["publish"])) == set(jobs) - {"publish"}


def test_one_commit_is_resolved_once_and_every_job_builds_that_one(
    proposal: dict,
) -> None:
    """`main` can move while a build runs.

    Checking out the ref in each job would publish an installer set that never
    existed as one tree, and `github.sha` is the workflow file's commit, not the
    commit being built. So the identity job resolves it and everyone else takes
    that value.
    """

    jobs = proposal["jobs"]
    identity_steps = _steps(jobs["identity"])
    assert identity_steps[0]["with"]["ref"] == "${{ inputs.sha }}"
    assert "git rev-parse HEAD" in identity_steps[1]["run"]
    assert jobs["identity"]["outputs"]["source"] == "${{ steps.resolve.outputs.source }}"

    for name, job in jobs.items():
        if name == "identity":
            continue
        checkouts = [
            step for step in _steps(job) if str(step.get("uses", "")).startswith("actions/checkout")
        ]
        assert checkouts, f"{name} never checks out the source"
        for step in checkouts:
            assert step["with"]["ref"] == "${{ needs.identity.outputs.source }}", name

    # The commit is carried into the artifacts too, not just the checkout.
    text = PROPOSAL.read_text(encoding="utf-8")
    assert "--source-commit" in text
    assert "github.sha" not in text


def test_the_build_is_stamped_before_anything_reads_the_version(
    proposal: dict,
) -> None:
    """The SPA compiles its version from package.json, and the app layer comes
    from Git blobs. A stamp after either is a build that misreports itself."""

    jobs = proposal["jobs"]
    for name in ("spa", "macos-bundle", "windows-bundle", "linux-bundle"):
        steps = _steps(jobs[name])
        names = [step.get("name", "") for step in steps]
        stamp = names.index("Stamp this build's identity")
        body = steps[stamp]["run"]
        assert "bump_version.py --build-stamp --set" in body
        # The verification the stamp would otherwise skip.
        assert "bump_version.py --check --build-stamp" in body
        # Committed, or the builder either refuses the dirty worktree or
        # packages the previous version.
        assert "commit -aqm" in body
        consumers = [
            index
            for index, label in enumerate(names)
            if label in {"Build the SPA", "Build and verify the standalone app"}
        ]
        assert consumers, name
        assert stamp < min(consumers), f"{name} stamps after it builds"


def test_the_stamp_commit_is_the_same_on_every_platform(proposal: dict) -> None:
    """Otherwise the three app manifests disagree on `commit` for one build."""

    jobs = proposal["jobs"]
    for name in ("spa", "macos-bundle", "windows-bundle", "linux-bundle"):
        step = next(
            step
            for step in _steps(jobs[name])
            if step.get("name") == "Stamp this build's identity"
        )
        environment = step["env"]
        assert environment["GIT_AUTHOR_DATE"] == "${{ needs.identity.outputs.stamp_date }}"
        assert environment["GIT_COMMITTER_DATE"] == "${{ needs.identity.outputs.stamp_date }}"


def test_the_artifacts_passed_between_jobs_match_by_name(proposal: dict) -> None:
    """An upload named one thing and a download naming another fails at the
    fourth job, twenty minutes in."""

    uploaded: set[str] = set()
    downloaded: set[str] = set()
    for job in proposal["jobs"].values():
        for step in _steps(job):
            uses = str(step.get("uses", ""))
            with_ = step.get("with") or {}
            if uses.startswith("actions/upload-artifact"):
                uploaded.add(with_["name"])
            elif uses.startswith("actions/download-artifact") and "name" in with_:
                downloaded.add(with_["name"])
    assert downloaded <= uploaded, f"downloaded but never uploaded: {downloaded - uploaded}"
    assert uploaded == {
        "main-build-spa",
        "main-build-app-layer",
        "main-build-macos",
        "main-build-windows",
        "main-build-linux",
    }
    # The publish job takes everything, by path rather than by name.
    publish_download = next(
        step
        for step in _steps(proposal["jobs"]["publish"])
        if str(step.get("uses", "")).startswith("actions/download-artifact")
    )
    assert "name" not in (publish_download.get("with") or {})
    assert publish_download["with"]["path"] == "build/artifacts"


def test_every_published_asset_name_is_one_the_builder_writes(proposal: dict) -> None:
    """The globs are checked against `release_assets`, not against themselves.

    Every installer, every layer and the SPA archive has to be matched by one of
    the upload paths, or it is built and then silently not published.
    """

    patterns: list[str] = []
    for job in proposal["jobs"].values():
        for step in _steps(job):
            if str(step.get("uses", "")).startswith("actions/upload-artifact"):
                patterns.extend(
                    line.strip()
                    for line in str(step["with"]["path"]).splitlines()
                    if line.strip()
                )

    # The SPA archive is uploaded through `env.artifact`, which the step above
    # sets from `release_assets.spa_archive_name`. Resolve it here rather than
    # skipping it: an expression nobody checks is how the one asset that is
    # named indirectly stops being published.
    spa_step = next(
        step
        for step in _steps(proposal["jobs"]["spa"])
        if step.get("name") == "Package and verify the SPA"
    )
    assert "release_assets.spa_archive_name" in spa_step["run"]
    assert 'echo "artifact=$archive"' in spa_step["run"]
    resolved = [
        release_assets.spa_archive_name(SAMPLE_VERSION)
        if pattern == "${{ env.artifact }}"
        else release_assets.spa_archive_name(SAMPLE_VERSION) + ".sha256"
        if pattern == "${{ env.artifact }}.sha256"
        else pattern
        for pattern in patterns
    ]
    assert not any(pattern.startswith("${{") for pattern in resolved), resolved

    def matched(name: str) -> bool:
        for pattern in resolved:
            regex = "^" + re.escape(Path(pattern).name).replace(r"\*", ".*") + "$"
            if re.match(regex, name):
                return True
        return False

    runtime_id = "0123456789ab"
    expected = [
        release_assets.spa_archive_name(SAMPLE_VERSION),
        release_assets.app_layer_name(SAMPLE_VERSION),
        release_assets.app_manifest_name(SAMPLE_VERSION),
        release_assets.windows_setup_name(SAMPLE_VERSION),
        *(
            release_assets.installer_name(platform, SAMPLE_VERSION)
            for platform in (
                release_assets.MACOS_PLATFORM,
                release_assets.WINDOWS_PLATFORM,
                release_assets.LINUX_PLATFORM,
            )
        ),
        *(
            release_assets.runtime_layer_name(platform, runtime_id)
            for platform in (
                release_assets.MACOS_PLATFORM,
                release_assets.WINDOWS_PLATFORM,
                release_assets.LINUX_PLATFORM,
            )
        ),
    ]
    # `installer_name(WINDOWS_PLATFORM, ...)` above is the portable folder as a
    # .zip: a real download, deliberately kept off the user-facing page, so it
    # belongs on the companion exactly as release.yml puts it there.
    unmatched = [name for name in expected if not matched(name)]
    assert not unmatched, f"built but never uploaded: {unmatched}"


def test_the_publish_job_can_actually_run_gh(proposal: dict) -> None:
    """`gh` needs a repository and a token, and this needs the source tree to
    name the assets with `release_assets` rather than a second copy of them."""

    publish = proposal["jobs"]["publish"]
    assert publish["permissions"] == {"contents": "write"}
    assert publish["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert publish["env"]["GH_REPO"] == "${{ github.repository }}"
    assert any(
        str(step.get("uses", "")).startswith("actions/checkout") for step in _steps(publish)
    )
    body = "\n".join(
        line
        for step in _steps(publish)
        for line in str(step.get("run", "")).splitlines()
        if not line.lstrip().startswith("#")
    )
    # Both releases are created, the companion first, and both as pre-releases:
    # `releases/latest` never returns a pre-release, which is what keeps a main
    # build off the stable channel.
    assert body.count("gh release create") == 2
    assert body.index("$UPDATES_TAG") < body.index('gh release create "$TAG"')
    assert body.count("--prerelease") == 2
    assert '--target "$SOURCE"' in body
    # Never republished, and never deleted: no tag moves.
    assert 'gh release view "$TAG"' in body
    assert "release delete" not in body
    assert "--cleanup-tag" not in body


def test_the_shared_actions_are_pinned_exactly_as_the_real_workflows_pin_them(
    proposal: dict,
) -> None:
    """A proposal that drifts from the workflows it mirrors is reviewed against
    a build nobody runs."""

    def pins(document: dict) -> set[str]:
        return {
            str(step["uses"])
            for job in document["jobs"].values()
            for step in _steps(job)
            if "uses" in step
        }

    proposed, actual = pins(proposal), pins(_load(RC_BUILD))
    assert proposed <= actual, f"pins the real workflows do not use: {proposed - actual}"
    # And the one pinned by digest stays pinned by digest.
    assert any(re.fullmatch(r"astral-sh/setup-uv@[0-9a-f]{40}", pin) for pin in proposed)


@pytest.mark.parametrize(
    ("script", "flag"),
    [
        ("scripts/bump_version.py", "--build-stamp"),
        ("scripts/build_bundle.py", "--source-commit"),
    ],
)
def test_the_flags_it_calls_exist_in_the_scripts_it_calls(script: str, flag: str) -> None:
    """The template is only as real as the command line it writes."""

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / script), "--help"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert flag in result.stdout
