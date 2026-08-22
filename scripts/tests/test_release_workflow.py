"""Release publication requires main ancestry and CI provenance."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_workflow_requires_main_ancestry_and_successful_ci_for_tag_sha() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "actions: read" in workflow
    assert "contents: write" in workflow
    assert "fetch-depth: 0" in workflow
    assert "git merge-base --is-ancestor \"$tagged_commit\" origin/main" in workflow
    assert 'workflow_id: "ci.yml"' in workflow
    assert "head_sha: process.env.TAGGED_COMMIT" in workflow
    assert 'event: "push"' in workflow
    assert 'run.conclusion === "success"' in workflow


def test_release_instructions_push_main_and_wait_for_exact_sha_before_tagging() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release = readme.split("## Releasing", 1)[1].split("## License", 1)[0]

    push_main = release.index("git push origin main")
    wait_for_ci = release.index("Wait for CI to be green on: git rev-parse HEAD")
    create_tag = release.index("git tag v0.2.1")
    push_tag = release.index("git push origin v0.2.1")
    assert push_main < wait_for_ci < create_tag < push_tag
