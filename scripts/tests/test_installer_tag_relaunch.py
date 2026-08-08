"""Installing a tag must continue under that tag's installer implementation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_shell_tag_checkout_execs_the_fresh_installer() -> None:
    source = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    tag_path = source[source.index('if [[ -n "$TAG" ]]'):source.index("if ! git symbolic-ref")]
    assert 'exec bash "$ROOT/scripts/install.sh" --after-pull' in tag_path


def test_windows_tag_checkout_requests_a_fresh_staged_copy() -> None:
    source = (ROOT / "scripts" / "install.bat").read_text(encoding="utf-8")
    tag_path = source[source.index(":checkout_tag"):source.index(":tag_needs_clean_tree")]
    assert 'set "CODE_UPDATED=1"' in tag_path
    assert "if defined CODE_UPDATED goto request_relaunch" in source
