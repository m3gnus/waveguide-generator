"""The release build runs before the version is spent, not after.

A tag push used to trigger `release.yml`, so the version was committed to before
anything was known to build. Two failures of a gate that had never run made
v0.2.5 and v0.2.6 permanently dead tags. These tests pin the inversion: no tag
trigger, and the tag is created in the publish job after every asset validates.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap

import pytest

from shared import release_assets


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

VERSION = "9.9.9"
RUNTIME_ID = "0123456789ab"


def test_release_workflow_requires_main_ancestry_and_successful_ci() -> None:
    assert "actions: read" in WORKFLOW
    assert "contents: write" in WORKFLOW
    assert "fetch-depth: 0" in WORKFLOW
    assert 'git merge-base --is-ancestor "$release_commit" origin/main' in WORKFLOW
    assert 'workflow_id: "ci.yml"' in WORKFLOW
    assert "head_sha: process.env.RELEASE_COMMIT" in WORKFLOW
    assert 'event: "push"' in WORKFLOW
    assert 'run.conclusion === "success"' in WORKFLOW


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
    assert 'git push origin "refs/tags/$RELEASE_TAG"' in WORKFLOW


def test_release_instructions_are_two_phases_that_tag_last() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release = readme.split("## Releasing", 1)[1].split("## License", 1)[0]

    bump = release.index("release.sh waveguide-generator patch")
    publish = release.index("release.sh waveguide-generator publish")
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


def test_the_workflow_spells_the_spa_archive_the_way_the_module_does() -> None:
    """The name is generated in one job and hardcoded in two others.

    The v0.3.0 release build failed here: `spa_archive_name` returns a COMPLETE
    filename, extension included, and the shell around it still appended
    `.tar.gz` as it had when the value was a stem. That wrote
    `update-spa-<version>.tar.gz.tar.gz`, and the macOS job's `test -f` found
    nothing -- three jobs into a release, on the one path no test covered.

    The macOS and Windows jobs still spell the path literally, because they run
    before the repository is importable. That is a duplication this cannot
    remove, so it pins the two spellings together instead.
    """

    from shared.release_assets import spa_archive_name

    rendered = spa_archive_name("$version")
    assert f"build/spa/{rendered}" in WORKFLOW, (
        f"the jobs look for build/spa/{rendered}; the workflow disagrees"
    )
    # The generated half must be used as given, never re-extended.
    assert 'tar -czf "$archive" -C frontend dist' in WORKFLOW
    assert '"$archive.tar.gz"' not in WORKFLOW
    assert "${{ env.artifact }}.tar.gz" not in WORKFLOW


def test_the_update_layers_are_published_before_the_release_that_needs_them() -> None:
    """Ordering is the whole safety argument for splitting the release in two.

    An update layer that a client can be told about but cannot download is worse
    than no update at all, so the companion pre-release is published first and
    the user-facing release -- the one `/releases/latest` returns -- last. Get
    this backwards and there is a window, however short, in which the latest
    release advertises a version whose layers do not exist.
    """

    companion = WORKFLOW.index("Publish the update layers as a companion pre-release")
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

    assert "UPDATES_TAG: ${{ needs.spa.outputs.tag }}-updates" in WORKFLOW
    assert release_assets.updates_tag(VERSION) == f"v{VERSION}-updates"
    assert release_assets.updates_tag(VERSION).endswith(
        release_assets.UPDATES_TAG_SUFFIX
    )


def _staging_script(tmp_path: Path) -> Path:
    """The publish job's inventory-and-split step, lifted out of the YAML.

    Running the real step rather than a re-implementation is the point: this
    logic exists only inside a workflow file, so nothing else can execute it and
    a copy in the test would drift silently. It is a heredoc in a `run:` block,
    so it is extracted by its delimiters and dedented.
    """

    step = WORKFLOW.index("Validate the exact inventory and every checksum")
    opener = "python - <<'PY'\n"
    start = WORKFLOW.index(opener, step) + len(opener)
    end = WORKFLOW.index("\n          PY\n", start)
    script = textwrap.dedent(WORKFLOW[start:end])
    assert script.startswith("from __future__"), script[:80]
    path = tmp_path / "extracted" / "stage_release_assets.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script + "\n", encoding="utf-8")
    return path


def _write(directory: Path, name: str, payload: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="ascii")


def _publish_inputs(
    tmp_path: Path, version: str = VERSION, runtime_id: str = RUNTIME_ID
) -> dict[str, list[str]]:
    """Exactly what the three build jobs upload, one file per inventory entry."""

    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "version.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    root = tmp_path / "build" / "publish-inputs"
    layout = {
        "spa": [release_assets.spa_archive_name(version)],
        "macos": [
            release_assets.installer_name(release_assets.MACOS_PLATFORM, version),
            release_assets.app_layer_name(version),
            release_assets.app_manifest_name(version),
            release_assets.runtime_layer_name(
                release_assets.MACOS_PLATFORM, runtime_id
            ),
        ],
        "windows": [
            release_assets.windows_setup_name(version),
            release_assets.installer_name(release_assets.WINDOWS_PLATFORM, version),
            release_assets.runtime_layer_name(
                release_assets.WINDOWS_PLATFORM, runtime_id
            ),
        ],
        "linux": [
            release_assets.installer_name(release_assets.LINUX_PLATFORM, version),
            release_assets.runtime_layer_name(
                release_assets.LINUX_PLATFORM, runtime_id
            ),
        ],
    }
    for job, names in layout.items():
        for name in names:
            _write(root / job, name, f"contents of {name}".encode())
    return layout


def _run_staging(tmp_path: Path) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(_staging_script(tmp_path))],
        cwd=tmp_path,
        env={"PYTHONPATH": str(ROOT), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_publish_step_stages_one_download_per_platform_and_the_rest_apart(
    tmp_path: Path,
) -> None:
    """The split, end to end, on the code that actually runs in CI.

    Every asset the four build jobs produce goes in; what comes out has to be
    two disjoint sets, and the user-facing one has to be exactly one file per
    supported platform: the macOS disk image, the Windows setup .exe and the
    Linux tarball. Everything else goes to the companion -- the update layers,
    the SPA archive, and the portable Windows .zip, which is a real download
    and still not the one to put in front of someone who came here to install
    the application.

    This asserted "exactly two" until 0.3.2, when Linux became the third
    platform. The rule it was describing was one file per platform; the count
    was how many platforms there were.
    """

    layout = _publish_inputs(tmp_path)
    result = _run_staging(tmp_path)

    assert result.returncode == 0, result.stderr
    downloads = sorted(p.name for p in (tmp_path / "build/release-assets").iterdir())
    layers = sorted(p.name for p in (tmp_path / "build/update-assets").iterdir())

    every_name = [name for names in layout.values() for name in names]
    # One sidecar survives, and it travels with the SPA archive to the companion.
    expected_layers = sorted(
        [
            original
            for original in every_name
            if not release_assets.is_user_download(original, VERSION)
        ]
        + [release_assets.checksum_name(release_assets.spa_archive_name(VERSION))]
    )
    assert layers == expected_layers
    # Named rather than only derived, so the expectation is legible and a change
    # to what a person is offered has to be made deliberately here too.
    assert downloads == sorted(
        [
            f"Waveguide.Generator-{VERSION}-macos-arm64.dmg",
            f"Waveguide.Generator-{VERSION}-windows-x86_64-setup.exe",
            f"Waveguide.Generator-{VERSION}-linux-x86_64.tar.gz",
        ]
    )
    # And derived as well, because that set is what the publish job asserts the
    # release equals -- so the two spellings are pinned to each other here.
    assert downloads == sorted(release_assets.user_download_names(VERSION))
    assert not set(downloads) & set(layers)
    # The Linux download and the SPA archive are both .tar.gz and land on
    # opposite sides. Nothing about the split may rest on the suffix.
    assert release_assets.spa_archive_name(VERSION) in layers
    # The portable .zip is built, and it is on the companion rather than nowhere.
    assert f"Waveguide.Generator-{VERSION}-windows-x86_64.zip" in layers


@pytest.mark.parametrize("version", ["0.3.1", "0.3.2", "0.4.0-beta.1"])
def test_no_version_puts_an_extra_file_on_the_user_facing_release(
    tmp_path: Path, version: str
) -> None:
    """The update bridge is gone: installers only, at every version, no exception.

    v0.3.1 published its app layer and manifest to both releases for one cycle,
    because 0.3.0's updater read the layers off the release it landed on and its
    tag pattern could not see a ``-updates`` tag at all. Every 0.3.1+ install
    reads the companion first, so that duplication retired at 0.3.2 along with
    the two constants that named it.

    0.3.1 itself is parametrised here rather than left out: it is the one
    version a resurrected branch would special-case, so it is the one worth
    asserting is no longer special. Rebuilding it would now produce the plain
    installers-only page, which is the point.
    """

    _publish_inputs(tmp_path, version=version)
    result = _run_staging(tmp_path)

    assert result.returncode == 0, result.stderr
    downloads = sorted(p.name for p in (tmp_path / "build/release-assets").iterdir())
    layers = sorted(p.name for p in (tmp_path / "build/update-assets").iterdir())

    assert downloads == sorted(release_assets.user_download_names(version))
    # Every download is an installer for a supported platform, and there is
    # exactly one per platform -- the property, rather than the count, which
    # moved from two to three when Linux was added at 0.3.2.
    assert downloads == sorted(
        release_assets.user_download_name(platform, version)
        for platform in (
            release_assets.MACOS_PLATFORM,
            release_assets.WINDOWS_PLATFORM,
            release_assets.LINUX_PLATFORM,
        )
    )
    # Nothing is on both sides any more -- the split is a partition again.
    assert not set(downloads) & set(layers)
    for name in (
        release_assets.app_layer_name(version),
        release_assets.app_manifest_name(version),
        release_assets.spa_archive_name(version),
        release_assets.runtime_layer_name(release_assets.MACOS_PLATFORM, RUNTIME_ID),
        release_assets.runtime_layer_name(release_assets.WINDOWS_PLATFORM, RUNTIME_ID),
        release_assets.runtime_layer_name(release_assets.LINUX_PLATFORM, RUNTIME_ID),
    ):
        assert name in layers
        assert name not in downloads


def test_the_update_bridge_constants_are_gone() -> None:
    """The recipe the original author wrote down, asserted as carried out.

    ``LAYER_DUPLICATION_VERSION``, ``PRE_SPLIT_RUNTIME_ID`` and the predicates
    over them were documented as retiring together with the branch in
    release.yml. A leftover constant is harmless on its own and is exactly how a
    one-cycle exception becomes permanent, so its absence is checked rather than
    assumed -- and the workflow is checked for the spelling too, since the branch
    lives only inside YAML.
    """

    for retired in (
        "LAYER_DUPLICATION_VERSION",
        "PRE_SPLIT_RUNTIME_ID",
        "duplicate_layers_on_user_release",
        "duplicate_on_user_release",
    ):
        assert not hasattr(release_assets, retired)
        assert retired not in WORKFLOW


def test_only_the_spa_archive_keeps_its_checksum_sidecar(tmp_path: Path) -> None:
    """Checksums are still computed and verified -- they are just not uploaded.

    Half of every release used to be sidecars of the other half, deleted by hand
    afterwards because the producer kept making them. GitHub serves a per-asset
    digest and the updater reads that. The SPA archive is the exception:
    `scripts/fetch_spa.py` fetches it by URL for source installs and never
    touches the releases API, so for that one file the sidecar is the only
    checksum a consumer can reach.

    Asserted by running the staging step rather than by grepping the YAML, so it
    describes what is published rather than how it is spelled.
    """

    _publish_inputs(tmp_path)
    result = _run_staging(tmp_path)

    assert result.returncode == 0, result.stderr
    staged = [
        p.name
        for directory in ("build/release-assets", "build/update-assets")
        for p in (tmp_path / directory).iterdir()
    ]
    assert [name for name in staged if name.endswith(".sha256")] == [
        release_assets.checksum_name(release_assets.spa_archive_name(VERSION))
    ]
    # Still built, and still the gate: corrupt one and the publish must fail.
    sidecar = (
        tmp_path
        / "build/publish-inputs/macos"
        / release_assets.checksum_name(
            release_assets.installer_name(release_assets.MACOS_PLATFORM, VERSION)
        )
    )
    sidecar.write_text(f"{'0' * 64}  wrong{chr(10)}", encoding="ascii")
    assert _run_staging(tmp_path).returncode != 0


def test_an_asset_the_build_did_not_produce_fails_the_publish(tmp_path: Path) -> None:
    """The inventory gate still holds across two destinations rather than one."""

    _publish_inputs(tmp_path)
    missing = (
        tmp_path
        / "build/publish-inputs/windows"
        / release_assets.windows_setup_name(VERSION)
    )
    missing.unlink()

    result = _run_staging(tmp_path)

    assert result.returncode != 0
    assert "Expected exactly one" in result.stderr


@pytest.mark.parametrize("job", ["spa", "macos", "windows"])
def test_a_corrupted_asset_fails_the_publish_before_either_release(
    tmp_path: Path, job: str
) -> None:
    """A checksum is verified against the file, in every job, before any upload.

    Splitting the staging in two must not have left one destination checked and
    the other trusted -- the layers are the half a client applies unattended.
    """

    layout = _publish_inputs(tmp_path)
    (tmp_path / "build/publish-inputs" / job / layout[job][-1]).write_bytes(b"tampered")

    result = _run_staging(tmp_path)

    assert result.returncode != 0
    assert "Invalid checksum sidecar" in result.stderr


def test_an_unexpected_extra_file_fails_the_publish(tmp_path: Path) -> None:
    """Nothing reaches either release that the inventory did not name.

    The staging loop copies by spec, so an extra file is never copied -- it would
    have been silently dropped rather than refused, and a stray artefact in the
    publish inputs means one of the build jobs did something nobody described.
    """

    _publish_inputs(tmp_path)
    (tmp_path / "build/publish-inputs/spa" / "surprise.zip").write_bytes(b"x")

    result = _run_staging(tmp_path)

    assert result.returncode != 0
    assert "Release inventory mismatch" in result.stderr
    assert re.search(r"extra=\[[^]]*surprise\.zip", result.stderr)
