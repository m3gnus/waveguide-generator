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
    path.parent.mkdir(parents=True)
    path.write_text(script + "\n", encoding="utf-8")
    return path


def _write(directory: Path, name: str, payload: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="ascii")


def _publish_inputs(
    tmp_path: Path, version: str = VERSION
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
                release_assets.MACOS_PLATFORM, RUNTIME_ID
            ),
        ],
        "windows": [
            release_assets.windows_setup_name(version),
            release_assets.installer_name(release_assets.WINDOWS_PLATFORM, version),
            release_assets.runtime_layer_name(
                release_assets.WINDOWS_PLATFORM, RUNTIME_ID
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


def test_the_publish_step_stages_installers_and_layers_to_separate_releases(
    tmp_path: Path,
) -> None:
    """The split, end to end, on the code that actually runs in CI.

    Every asset the three build jobs produce goes in; what comes out has to be
    two disjoint sets. The user-facing release holds the three installers -- the
    macOS disk image and both Windows downloads, since the setup .exe is
    published beside the portable .zip -- and nothing else. Everything the
    updater and a source install consume, the SPA archive included, goes to the
    companion.
    """

    layout = _publish_inputs(tmp_path)
    result = _run_staging(tmp_path)

    assert result.returncode == 0, result.stderr
    downloads = sorted(p.name for p in (tmp_path / "build/release-assets").iterdir())
    layers = sorted(p.name for p in (tmp_path / "build/update-assets").iterdir())

    every_name = [name for names in layout.values() for name in names]
    expected_downloads = sorted(
        name
        for original in every_name
        if original.startswith(release_assets.INSTALLER_PREFIX)
        for name in (original, release_assets.checksum_name(original))
    )
    expected_layers = sorted(
        name
        for original in every_name
        if not original.startswith(release_assets.INSTALLER_PREFIX)
        for name in (original, release_assets.checksum_name(original))
    )
    assert downloads == expected_downloads
    assert layers == expected_layers
    # Named rather than only derived, so the expectation is legible and a change
    # to what a person is offered has to be made deliberately here too.
    assert downloads == sorted(
        [
            f"Waveguide.Generator-{VERSION}-macos-arm64.dmg",
            f"Waveguide.Generator-{VERSION}-macos-arm64.dmg.sha256",
            f"Waveguide.Generator-{VERSION}-windows-x86_64-setup.exe",
            f"Waveguide.Generator-{VERSION}-windows-x86_64-setup.exe.sha256",
            f"Waveguide.Generator-{VERSION}-windows-x86_64.zip",
            f"Waveguide.Generator-{VERSION}-windows-x86_64.zip.sha256",
        ]
    )
    assert not set(downloads) & set(layers)
    assert all(name.startswith(release_assets.UPDATE_PREFIX) for name in layers)


def test_the_transitional_version_publishes_its_layers_to_both_releases(
    tmp_path: Path,
) -> None:
    """One version ships its layers twice, so the release before it can update.

    0.3.0's updater reads layers from the release it lands on and cannot even
    see a ``-updates`` tag, so a clean split would leave it reporting "update
    preparing" forever. For `LAYER_DUPLICATION_VERSION` only, every layer is
    published to the user-facing release as well. The companion is still
    complete -- this adds copies, it never moves anything -- so a client that
    reads the companion is unaffected either way.

    The steady state is asserted by the disjointness test above, which runs at a
    version this rule does not cover. When the duplication is dropped, that test
    stays and this one goes.
    """

    version = release_assets.LAYER_DUPLICATION_VERSION
    layout = _publish_inputs(tmp_path, version=version)
    result = _run_staging(tmp_path)

    assert result.returncode == 0, result.stderr
    downloads = sorted(p.name for p in (tmp_path / "build/release-assets").iterdir())
    layers = sorted(p.name for p in (tmp_path / "build/update-assets").iterdir())

    every_name = [name for names in layout.values() for name in names]
    expected_layers = sorted(
        name
        for original in every_name
        if not original.startswith(release_assets.INSTALLER_PREFIX)
        for name in (original, release_assets.checksum_name(original))
    )
    # The companion is exactly what it always was.
    assert layers == expected_layers
    # And every one of those files is also on the page a 0.3.0 client reads.
    assert set(layers) <= set(downloads)
    # The installers are still there and were not displaced by the copies.
    assert {
        f"Waveguide.Generator-{version}-macos-arm64.dmg",
        f"Waveguide.Generator-{version}-windows-x86_64-setup.exe",
        f"Waveguide.Generator-{version}-windows-x86_64.zip",
    } <= set(downloads)
    assert len(downloads) == len(expected_layers) + 6


def test_only_the_transitional_version_duplicates_its_layers() -> None:
    """The rule is pinned to one version, not left to drift on as a default."""

    assert release_assets.duplicate_layers_on_user_release(
        release_assets.LAYER_DUPLICATION_VERSION
    )
    assert not release_assets.duplicate_layers_on_user_release("0.3.0")
    assert not release_assets.duplicate_layers_on_user_release("0.3.2")
    assert not release_assets.duplicate_layers_on_user_release(VERSION)


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
