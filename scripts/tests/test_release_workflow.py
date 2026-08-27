"""The release build runs before the version is spent, not after.

A tag push used to trigger `release.yml`, so the version was committed to before
anything was known to build. Two failures of a gate that had never run made
v0.2.5 and v0.2.6 permanently dead tags. These tests pin the inversion: no tag
trigger, and the tag is created in the publish job after every asset validates.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


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
