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
