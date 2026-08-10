"""Recursive uninstall must never accept a broad or source-tree target."""

from pathlib import Path

import pytest

from scripts.validate_uninstall_target import UnsafeTarget, validate


def test_rejects_filesystem_root_and_home(tmp_path: Path) -> None:
    repo = tmp_path / "checkout" / "waveguide-generator-v2"
    home = tmp_path / "users" / "owner"
    with pytest.raises(UnsafeTarget, match="filesystem root"):
        validate(Path(Path.cwd().anchor), repo, home=home)
    with pytest.raises(UnsafeTarget, match="home directory"):
        validate(home, repo, home=home)


@pytest.mark.parametrize("relative", (".", "server", "frontend/dist"))
def test_rejects_checkout_and_every_descendant(tmp_path: Path, relative: str) -> None:
    repo = tmp_path / "checkout" / "waveguide-generator-v2"
    with pytest.raises(UnsafeTarget, match="checkout"):
        validate(repo / relative, repo, home=tmp_path / "home")


def test_rejects_every_checkout_ancestor(tmp_path: Path) -> None:
    repo = tmp_path / "workspace" / "waveguide-generator-v2"
    with pytest.raises(UnsafeTarget, match="checkout"):
        validate(tmp_path / "workspace", repo, home=tmp_path / "home")


def test_accepts_a_narrow_sibling_data_directory(tmp_path: Path) -> None:
    repo = tmp_path / "workspace" / "waveguide-generator-v2"
    target = tmp_path / "application-data" / "WaveguideGenerator"
    assert validate(target, repo, home=tmp_path / "home") == target.absolute()
