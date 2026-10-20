"""Release promotion verifies qualified bytes and creates the tag last."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from typing import NamedTuple

import pytest
import yaml
from shared import release_assets

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / '.github/workflows/release.yml').read_text()
RC_WORKFLOW = (ROOT / '.github/workflows/rc-build.yml').read_text()
JOBS = yaml.safe_load(WORKFLOW)['jobs']
VERSION = '9.9.9'


def test_release_only_promotes_and_requires_an_explicit_source_run():
    workflow = yaml.safe_load(WORKFLOW)
    inputs = workflow[True]['workflow_dispatch']['inputs']
    assert set(inputs) == {'sha', 'source_run_id'}
    assert all(option['required'] is True for option in inputs.values())
    assert set(JOBS) == {'publish'}
    assert JOBS['publish']['permissions'] == {'actions': 'read', 'contents': 'write'}
    assert workflow['permissions'] == {'actions': 'read', 'contents': 'read'}
    for forbidden in ('build_bundle.py', 'npm ', 'actions/attest@', 'UPDATE_SIGNING_KEY',
                      'openssl ', 'setup-node@', 'setup-uv@', 'uses: ./.github/workflows/ci.yml'):
        assert forbidden not in WORKFLOW
    assert 'git merge-base --is-ancestor "$release_commit" origin/main' in WORKFLOW
    assert 'test "$RELEASE_SHA" = "$WORKFLOW_SHA"' in WORKFLOW
    assert 'test "$GITHUB_REF" = refs/heads/main' in WORKFLOW
    assert 'fetch-depth: 0' in WORKFLOW


def test_publication_requires_its_own_protected_environment_approval():
    assert JOBS['publish']['environment'] == 'update-signing'


def test_publish_credentials_are_scoped_to_steps_that_need_them():
    workflow = yaml.safe_load(WORKFLOW)
    assert 'GH_TOKEN' not in workflow.get('env', {})
    publish = JOBS['publish']
    assert 'GH_TOKEN' not in publish.get('env', {})
    credential_steps = {
        'Require a successful signed candidate run on this source',
        'Verify digests, signatures and same-run attestations before staging',
        'Verify existing releases before any publication',
        'Create the annotated tag now that every asset exists',
        'Publish only after every upload succeeded',
    }
    for step in publish['steps']:
        if step.get('name') in credential_steps:
            assert step['env']['GH_TOKEN'] == '${{ secrets.GITHUB_TOKEN }}'
        else:
            assert 'GH_TOKEN' not in step.get('env', {})
        if 'actions/checkout@' in step.get('uses', ''):
            assert step['with']['persist-credentials'] is False
    tag = next(s for s in publish['steps'] if s.get('name', '').startswith('Create the annotated tag'))
    assert "git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin" in tag['run']
    assert 'gh auth setup-git' not in WORKFLOW


def test_every_download_comes_from_the_selected_qualified_run():
    steps = JOBS['publish']['steps']
    downloads = [s for s in steps if 'download-artifact@' in s.get('uses', '')]
    assert {s['with']['name'] for s in downloads} == {
        'release-spa', 'rc-macos-sign-inputs', 'rc-windows-sign-inputs',
        'rc-linux-sign-inputs', 'rc-signed-candidate'}
    for step in downloads:
        assert step['with']['run-id'] == '${{ inputs.source_run_id }}'
        assert step['with']['github-token'] == '${{ secrets.GITHUB_TOKEN }}'
        assert 'if' not in step
    check = next(s for s in steps if 'check-run' in s.get('run', ''))
    verify = next(s for s in steps if 'promote_candidate.py stage' in s.get('run', ''))
    first_write = next(s for s in steps if 'action-gh-release@' in s.get('uses', ''))
    assert steps.index(check) < min(steps.index(s) for s in downloads)
    assert max(steps.index(s) for s in downloads) < steps.index(verify) < steps.index(first_write)
    assert '--source-run build/source-run.json' in verify['run']
    assert '--expected-sha "$RELEASE_SHA"' in verify['run']
    assert not any('if' in s for s in (check, verify))


def test_a_tag_push_cannot_trigger_a_release_build() -> None:
    """The trigger that spent two versions. It must not come back."""

    assert "tags:" not in WORKFLOW
    assert "on:\n  workflow_dispatch:\n    inputs:\n      sha:" in WORKFLOW
    assert "github.ref_name" not in WORKFLOW


def test_the_version_must_move_forward_past_every_published_tag() -> None:
    assert 'if git rev-parse --verify --quiet "refs/tags/$tag^{}"' in WORKFLOW
    assert "does not move forward past the highest tag" in WORKFLOW


def test_the_tag_is_created_after_the_assets_and_before_publication() -> None:
    upload = WORKFLOW.index("Upload the validated inventory to a draft release")
    create_tag = WORKFLOW.index("Create the annotated tag now that every asset exists")
    publish = WORKFLOW.index("Publish only after every upload succeeded")
    assert upload < create_tag < publish
    assert 'git tag -a "$RELEASE_TAG"' in WORKFLOW
    assert "git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin" in WORKFLOW


def test_release_instructions_prepare_then_promote_and_tag_last() -> None:
    release = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")

    bump = release.index("land.sh waveguide-generator version patch")
    publish = release.index("gh workflow run release.yml --ref main -f sha=")
    assert bump < publish
    assert "nothing is tagged\nand no version is spent" in release
    assert "then** creates the annotated\ntag" in release


def test_the_published_tag_is_asserted_annotated_and_on_the_release_commit() -> None:
    """The hole left by creating the tag ourselves.

    Publishing a draft whose tag does not exist makes GitHub create a LIGHTWEIGHT
    tag at ``target_commitish`` -- the default branch's HEAD, not necessarily the
    release commit. So a skipped or failed tag step would not fail the release; it
    would silently ship a tag of the wrong kind and possibly of the wrong commit.
    ``v0.2.3`` in this repository is lightweight, which is how that path is known
    to be reachable rather than merely conceivable.
    """

    create_tag = WORKFLOW.index("Create the annotated tag now that every asset exists")
    publish = WORKFLOW.index("Publish only after every upload succeeded")
    assert_tag = WORKFLOW.index("The published tag must be the annotated one")
    assert create_tag < publish < assert_tag

    assert 'kind="$(git cat-file -t "$(git rev-parse "$RELEASE_TAG")")"' in WORKFLOW
    assert 'if [ "$kind" != "tag" ]; then' in WORKFLOW
    assert 'if [ "$tagged" != "$RELEASE_SHA" ]; then' in WORKFLOW


def test_the_update_layers_are_published_before_the_release_that_needs_them() -> None:
    """Ordering is the whole safety argument for splitting the release in two.

    An update layer that a client can be told about but cannot download is worse
    than no update at all, so the companion pre-release is published first and
    the user-facing release -- the one `/releases/latest` returns -- last. Get
    this backwards and there is a window, however short, in which the latest
    release advertises a version whose layers do not exist.
    """

    companion = WORKFLOW.index("Publish supporting assets as a companion pre-release")
    upload = WORKFLOW.index("Upload the validated inventory to a draft release")
    publish = WORKFLOW.index("Publish only after every upload succeeded")
    assert companion < upload < publish

    assert "tag_name: ${{ env.UPDATES_TAG }}" in WORKFLOW
    assert "files: build/update-assets/*" in WORKFLOW
    assert "files: build/release-assets/*" in WORKFLOW
    # A pre-release is never what `/releases/latest` returns, so an updater that
    # knows nothing about the split cannot be pointed at the machinery release.
    assert "prerelease: true" in WORKFLOW


def test_the_workflow_spells_the_companion_tag_the_way_the_module_does() -> None:
    """The tag is generated in `shared`, and the workflow builds it in YAML.

    The workflow cannot import the module before checkout, so the two spellings
    have to be pinned together, exactly as the SPA archive name already is.
    """

    assert 'echo "UPDATES_TAG=$tag-updates" >> "$GITHUB_ENV"' in WORKFLOW
    assert release_assets.updates_tag(VERSION) == f"v{VERSION}-updates"
    assert release_assets.updates_tag(VERSION).endswith(
        release_assets.UPDATES_TAG_SUFFIX
    )


#: `owner/name@ref`, with the optional trailing `# version` comment separated.
USES_RE = re.compile(
    r"^\s*(?:-\s+)?uses:\s+(?P<action>[^@\s]+)@(?P<ref>\S+)(?:\s+#\s*(?P<comment>.+))?$",
    re.MULTILINE,
)
IMMUTABLE_REF_RE = re.compile(r"^[0-9a-f]{40}$")

#: Every action the two release-building workflows may use, and the commit it is
#: pinned to. Resolved from GitHub on 2026-09-06 with `gh api repos/<action>/
#: commits/<tag>`; the comment beside each `uses:` names the release that commit
#: belonged to. This table is the drift check: moving a pin means changing it
#: here, in the same commit, with the new SHA resolved the same way.
RELEASE_ACTION_PINS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/attest": "508db95dd578ae2727ebd6217d5ba78e4fbda05d",
    "astral-sh/setup-uv": "08807647e7069bb48b6ef5acd8ec9567f424441b",
    "softprops/action-gh-release": "3bb12739c298aeb8a4eeaf626c5b8d85266b0e65",
}

#: The exact Node the SPA is built with. A major-only spec resolves to whatever
#: the runner image happens to carry, which is the same reproducibility hole a
#: floating action tag is.
RELEASE_NODE_VERSION = "20.20.2"


@pytest.mark.parametrize(
    ("name", "text"),
    (("release.yml", WORKFLOW), ("rc-build.yml", RC_WORKFLOW)),
    ids=("release", "candidate"),
)
def test_every_release_action_is_pinned_to_an_immutable_commit(name: str, text: str) -> None:
    """A major tag moves, so a workflow naming one does not describe its build.

    Both files are checked because an RC that is hand-tested by a different set
    of actions than the release does not test the release.
    """

    seen = [match.groupdict() for match in USES_RE.finditer(text)]
    assert seen, f"{name} declares no actions at all; the pattern stopped matching"
    for entry in seen:
        action, ref, comment = entry["action"], entry["ref"], entry["comment"]
        assert IMMUTABLE_REF_RE.fullmatch(ref), (
            f"{name} uses {action}@{ref}, which is not a 40-character commit id"
        )
        assert action in RELEASE_ACTION_PINS, (
            f"{name} uses {action}, which is not in the reviewed pin table"
        )
        assert ref == RELEASE_ACTION_PINS[action], (
            f"{name} pins {action} to {ref}, the table says {RELEASE_ACTION_PINS[action]}"
        )
        assert comment and comment.strip().startswith("v"), (
            f"{name} pins {action} without naming the release the commit belongs to"
        )


def test_the_release_and_the_candidate_are_built_by_the_same_actions() -> None:
    """Same pins, same versions -- otherwise the hand test proves nothing."""

    def pins(text: str) -> dict[str, str]:
        return {
            match.group("action"): match.group("ref") for match in USES_RE.finditer(text)
        }

    release_pins = pins(WORKFLOW)
    candidate_pins = pins(RC_WORKFLOW)
    shared_actions = release_pins.keys() & candidate_pins.keys()
    assert shared_actions, "the two workflows share no actions at all"
    for action in sorted(shared_actions):
        assert release_pins[action] == candidate_pins[action], action


@pytest.mark.parametrize(
    ("name", "text"),
    (("rc-build.yml", RC_WORKFLOW),),
    ids=("candidate",),
)
def test_the_release_node_is_an_exact_patch_and_that_patch_is_checked(
    name: str, text: str
) -> None:
    """A major-only spec resolves to whatever the runner image carries.

    The pin is the point; the post-install check is drift detection for it, and
    is not asserted here to be anything more. `actions/setup-node` verifies no
    digest on its download -- read at the pinned commit -- and this repository
    does not authenticate that archive either, which the workflow comment says
    plainly rather than calling the version check a substitute.
    """

    assert f'node-version: "{RELEASE_NODE_VERSION}"' in text, name
    assert 'node-version: "20"' not in text, name
    assert f'expected="v{RELEASE_NODE_VERSION}"' in text, name
    assert 'actual="$(node --version)"' in text, name


def test_the_inno_setup_check_uses_the_compiler_engine_version() -> None:
    """The helper reads the exact engine version from a real compile probe."""

    verifier = (ROOT / "scripts" / "ci" / "verify_inno_setup.ps1").read_text(
        encoding="utf-8"
    )
    assert '[Environment]::GetEnvironmentVariable("ProgramFiles(x86)")' in verifier
    assert "FileVersionInfo]::GetVersionInfo($CompilerPath).FileVersion" in verifier
    assert 'ExpectedVersion = "6.7.1"' in verifier
    assert '(& $CompilerPath $probeScript 2>&1 | Out-String)' in verifier
    assert "Compiler engine version:" in verifier
    assert "$probeExitCode -ne 0" in verifier
    assert "Get-ChildItem" not in verifier
    for name, text in (("rc-build.yml", RC_WORKFLOW),):
        assert "choco install innosetup --version=6.7.1" in text, name
        assert 'if ($LASTEXITCODE -ne 0)' in text, name
        assert "./scripts/ci/verify_inno_setup.ps1" in text, name
        assert '$banner -notmatch "6\\.7\\.1"' not in text, name


def _version_guard(tmp_path: Path) -> Path:
    """The release guard's forward-ordering script, lifted out of the YAML."""

    opener = "          import subprocess\n"
    start = WORKFLOW.index(opener)
    end = WORKFLOW.index("\n          PY\n", start)
    script = textwrap.dedent(WORKFLOW[start:end])
    assert script.startswith("import subprocess"), script[:80]
    path = tmp_path / "extracted" / "release_version_guard.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script + "\n", encoding="utf-8")
    return path


def _tag_inventory(tmp_path: Path, tags: list[str]) -> Path:
    """A repository whose only interesting property is which tags it carries."""

    repo = tmp_path / "inventory"
    repo.mkdir()
    run = lambda *args: subprocess.run(  # noqa: E731 - one line, one purpose
        ["git", *args], cwd=repo, check=True, stdout=subprocess.DEVNULL
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    run("commit", "-q", "--allow-empty", "-m", "base")
    for tag in tags:
        run("tag", tag)
    return repo


class GuardRun(NamedTuple):
    """What the workflow step would have produced."""

    returncode: int
    stdout: str
    stderr: str
    #: Everything the guard appended to GITHUB_OUTPUT, as the step would.
    outputs: dict[str, str]


def _run_guard(tmp_path: Path, declared: str, tags: list[str]) -> GuardRun:
    tmp_path.mkdir(parents=True, exist_ok=True)
    github_output = tmp_path / "github-output"
    github_output.write_text("", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_version_guard(tmp_path)), declared],
        cwd=_tag_inventory(tmp_path, tags),
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return GuardRun(result.returncode, result.stdout, result.stderr, outputs)


def test_a_stable_release_passes_the_guard_and_is_not_a_prerelease(
    tmp_path: Path,
) -> None:
    result = _run_guard(tmp_path, "0.3.2", ["v0.3.1", "v0.3.1-updates"])

    assert result.returncode == 0, result.stderr
    assert result.outputs["prerelease"] == "false"


@pytest.mark.parametrize("declared", ["0.3.2-rc.1", "0.4.0-beta.1", "1.0.0-alpha"])
def test_a_prerelease_passes_the_guard_and_is_flagged(
    tmp_path: Path, declared: str
) -> None:
    """The finding. Each of these was refused before it could be built."""

    result = _run_guard(tmp_path, declared, ["v0.3.1", "v0.3.1-updates"])

    assert result.returncode == 0, result.stderr
    assert result.outputs["prerelease"] == "true"


def test_a_stable_release_may_follow_its_own_candidates(tmp_path: Path) -> None:
    """SemVer rule 11: `0.3.2` outranks `0.3.2-rc.2`, so the RC is not a wall."""

    result = _run_guard(
        tmp_path, "0.3.2", ["v0.3.1", "v0.3.2-rc.1", "v0.3.2-rc.2", "v0.3.2-rc.2-updates"]
    )

    assert result.returncode == 0, result.stderr
    assert result.outputs["prerelease"] == "false"


def test_a_candidate_may_follow_an_earlier_candidate(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, "0.3.2-rc.2", ["v0.3.1", "v0.3.2-rc.1"])

    assert result.returncode == 0, result.stderr
    assert result.outputs["prerelease"] == "true"


@pytest.mark.parametrize(
    ("declared", "tags"),
    [
        # A candidate for a version that has already shipped.
        ("0.3.2-rc.2", ["v0.3.1", "v0.3.2"]),
        # An earlier candidate than the highest published one.
        ("0.3.2-rc.1", ["v0.3.2-rc.2"]),
        # The same version again.
        ("0.3.2", ["v0.3.2"]),
        # Backwards.
        ("0.3.1", ["v0.3.2"]),
    ],
)
def test_the_guard_still_refuses_a_version_that_does_not_move_forward(
    tmp_path: Path, declared: str, tags: list[str]
) -> None:
    """Widening the parser must not widen what may be published."""

    result = _run_guard(tmp_path, declared, tags)

    assert result.returncode != 0
    assert "does not move forward past the highest tag" in result.stderr
    assert "prerelease" not in result.outputs


def test_a_companion_tag_is_never_the_highest_published_version(
    tmp_path: Path,
) -> None:
    """`git tag --list v*` returns the `-updates` companions too.

    A companion carries another version's machinery. Reading one as a version
    would let `v0.9.0-updates` block every release below `0.9.0`, and the old
    all-digit parser dropped them only as a side effect of not understanding a
    hyphen at all.
    """

    result = _run_guard(tmp_path, "0.3.2", ["v0.3.1", "v0.9.0-updates"])

    assert result.returncode == 0, result.stderr
    assert result.outputs["prerelease"] == "false"


@pytest.mark.parametrize("declared", ["0.3", "0.3.2.1", "0.3.2+build", "not-a-version"])
def test_the_guard_refuses_something_that_is_not_a_release_version(
    tmp_path: Path, declared: str
) -> None:
    result = _run_guard(tmp_path, declared, ["v0.3.1"])

    assert result.returncode != 0
    assert "does not name a release" in result.stderr
    assert "prerelease" not in result.outputs


@pytest.mark.parametrize("declared", ["0.4.0-main.7", "0.3.2-main.1", "0.4.0-nightly"])
def test_the_guard_refuses_a_build_stamp(tmp_path: Path, declared: str) -> None:
    """A build of `main` is not a release, and shares the pre-release slot.

    `docs/history/UPDATE-CHANNELS.md` states that a main build can never go
    out through this workflow. That held only because the guard's own parser
    refused every hyphen, and widening it for release candidates admitted a
    build stamp with them. The identifier is what tells the two apart, so the
    guarantee is now asserted rather than inherited.
    """

    result = _run_guard(tmp_path, declared, ["v0.3.1"])

    assert result.returncode != 0
    assert "build stamp" in result.stderr
    assert "prerelease" not in result.outputs


def test_the_guard_uses_the_shared_comparator_rather_than_its_own_parser(
    tmp_path: Path,
) -> None:
    """The drift this finding is: two parsers, one narrower than the other.

    `shared/release_assets.py` owns the tag pattern the updater matches with, so
    the publisher must order versions with the same code or the two can disagree
    again about what a version is.
    """

    guard = _version_guard(tmp_path).read_text(encoding="utf-8")
    assert "from shared import release_assets" in guard
    assert "release_assets.version_precedence" in guard
    assert "release_assets.is_prerelease" in guard
    assert "isdigit()" not in guard
    # The classification reaches the publish job through the step output.
    assert 'GITHUB_OUTPUT' in guard
    assert 'f"prerelease={flag}' in guard


def test_the_public_release_is_classified_from_the_declared_version() -> None:
    """Separately from the companion, which is unconditionally a pre-release.

    Asserting that `prerelease: true` appears somewhere in this file only ever
    proved the companion was hidden. The user-facing release had no
    classification at all, so an RC would have been published as a full release
    and offered to every stable install as `/releases/latest`.
    """

    companion = WORKFLOW.index("Publish supporting assets as a companion pre-release")
    draft = WORKFLOW.index("Upload the validated inventory to a draft release")
    tail = WORKFLOW.index("Create the annotated tag now that every asset exists")

    # Comments in both steps name the other one's flag, so read the keys only.
    def _keys(text: str) -> str:
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

    companion_step = _keys(WORKFLOW[companion:draft])
    draft_step = _keys(WORKFLOW[draft:tail])

    # Machinery is hidden whatever the version says.
    assert "prerelease: true" in companion_step
    # The release a person installs is classified by its own version.
    assert "prerelease: ${{ steps.release_guard.outputs.prerelease }}" in draft_step
    assert "prerelease: true" not in draft_step

    assert "prerelease: ${{ steps.release_guard.outputs.prerelease }}" in WORKFLOW
    assert 'handle.write(f"prerelease={flag}\\n")' in WORKFLOW


def test_the_flag_the_guard_prints_is_what_the_publisher_would_set(
    tmp_path: Path,
) -> None:
    """End to end across the two jobs, as far as a test can follow it.

    The guard's stdout becomes `steps.release_guard.outputs.prerelease`, which
    becomes `steps.release_guard.outputs.prerelease`, which is the publish job's flag. Only
    the first hop is executable here; the other two are pinned as text above.
    """

    for declared, expected in (("0.3.2", "false"), ("0.3.3-beta.1", "true")):
        result = _run_guard(tmp_path / declared, declared, ["v0.3.1"])
        assert result.returncode == 0, result.stderr
        assert result.outputs["prerelease"] == expected
        assert release_assets.is_prerelease(declared) is (expected == "true")


CI_WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
QUALIFICATION = "./.github/workflows/ci.yml"

#: What in a step builds, attests, uploads or publishes.
PRODUCING_ACTIONS = (
    "actions/attest@",
    "actions/upload-artifact@",
    "softprops/action-gh-release@",
)
PRODUCING_COMMANDS = ("npm run build", "build_bundle.py", "git push", "gh release")


def _keys_only(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _triggers(workflow: dict) -> dict:
    # PyYAML reads a bare `on:` key as the boolean True.
    return workflow.get("on", workflow.get(True)) or {}


def _needs(job: dict) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _ancestors(jobs: dict, name: str) -> set[str]:
    """Every job ``name`` transitively needs."""

    seen: set[str] = set()
    pending = list(_needs(jobs[name]))
    while pending:
        job = pending.pop()
        if job not in seen:
            seen.add(job)
            pending.extend(_needs(jobs[job]))
    return seen


def _produces(job: dict) -> bool:
    for step in job.get("steps") or []:
        if str(step.get("uses", "")).startswith(PRODUCING_ACTIONS):
            return True
        if any(command in str(step.get("run", "")) for command in PRODUCING_COMMANDS):
            return True
    return False


def test_hosted_ci_runs_on_dev_pushes_on_demand_or_when_a_release_workflow_calls_it() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW)

    triggers = _triggers(workflow)
    assert set(triggers) == {"push", "workflow_dispatch", "workflow_call"}
    # Only dev: a push to main or a topic branch, or a pull request, runs nothing.
    assert triggers["push"] == {"branches": ["dev"]}
    assert "pull_request" not in _keys_only(CI_WORKFLOW)


def test_only_a_dev_push_is_ever_cancelled_and_never_a_release_qualification() -> None:
    """Under workflow_call `github.workflow` names the caller.

    A group keyed on it, or a cancel-in-progress that applied to a dispatch or a
    called run, could cancel a release qualification. Only push runs share a
    group, and only they cancel.
    """

    concurrency = yaml.safe_load(CI_WORKFLOW)["concurrency"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'push' }}"
    group = concurrency["group"]
    assert "github.workflow" not in group
    assert "github.run_id" in group
    assert "github.ref" in group


def test_a_dev_push_runs_linux_and_windows_and_every_other_run_keeps_macos() -> None:
    matrix = yaml.safe_load(CI_WORKFLOW)["jobs"]["server"]["strategy"]["matrix"]
    expression = matrix["os"]
    assert "github.event_name == 'push'" in expression
    push_leg, full_leg = re.findall(r"'(\[[^']*\])'", expression)
    assert json.loads(push_leg) == ["ubuntu-latest", "windows-latest"]
    assert json.loads(full_leg) == ["ubuntu-latest", "macos-latest", "windows-latest"]


def test_hosted_ci_needs_nothing_a_release_caller_does_not_grant() -> None:
    """A called workflow's token holds only what the calling job grants.

    The callers grant `contents: read`. A job here asking for more would not
    widen anything; it would fail every release at startup instead.
    """

    workflow = yaml.safe_load(CI_WORKFLOW)
    blocks = [workflow.get("permissions")] + [
        job.get("permissions") for job in workflow["jobs"].values()
    ]
    for block in blocks:
        if block is None:
            continue
        assert isinstance(block, dict), block
        assert set(block.items()) <= {("contents", "read")}, block


@pytest.mark.parametrize(
    ("name", "text", "builders"),
    (
        (
            "rc-build.yml",
            RC_WORKFLOW,
            {"spa", "macos-bundle", "windows-bundle", "linux-bundle"},
        ),
    ),
    ids=("candidate",),
)
def test_nothing_is_built_attested_or_published_before_source_qualification(
    name: str, text: str, builders: set[str]
) -> None:
    jobs = yaml.safe_load(text)["jobs"]
    qualifying = [job for job, body in jobs.items() if body.get("uses") == QUALIFICATION]
    assert len(qualifying) == 1, f"{name} must call ci.yml exactly once"
    qualify = qualifying[0]
    call = jobs[qualify]
    # Read-only and no secrets: ci.yml needs nothing more.
    assert call.get("permissions") == {"contents": "read"}, name
    assert "secrets" not in call, name
    assert "if" not in call, name

    producing = {job for job, body in jobs.items() if _produces(body)}
    # The detector has to see the build jobs, or everything below is vacuous.
    assert builders <= producing, sorted(builders - producing)
    for job in sorted(producing):
        assert qualify in _ancestors(jobs, job), f"{name}: {job} does not need {qualify}"
    # A job downstream of qualification must not be able to run after it failed.
    for job, body in jobs.items():
        if qualify in _ancestors(jobs, job):
            condition = str(body.get("if", ""))
            for escape in ("always()", "failure()", "cancelled()"):
                assert escape not in condition, f"{name}: {job} runs on {escape}"


def test_publication_serializes_versions_and_keeps_public_companions_immutable():
    workflow = yaml.safe_load(WORKFLOW)
    assert workflow['concurrency'] == {'group': 'release-promotion', 'cancel-in-progress': False}
    steps = JOBS['publish']['steps']
    guard = next(s for s in steps if s.get('id') == 'publication')
    companion = next(s for s in steps if s.get('name') == 'Publish supporting assets as a companion pre-release')
    assert 'promote_candidate.py check-publication' in guard['run']
    assert steps.index(guard) < steps.index(companion)
    assert companion['if'] == "${{ steps.publication.outputs.companion_exists == 'false' }}"
    assert companion['with']['overwrite_files'] is False
