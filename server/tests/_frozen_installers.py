"""Extract a released build's updater code without importing it into this process.

The frozen-tag compatibility tests run the code a 0.3.4 or 0.3.5 install
actually has, read straight from the release tag, against what this build
publishes. They extract it to a temporary directory and run it in a separate
interpreter whose import path holds only that directory, so nothing from the
current checkout can stand in for the released code.
"""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

from server.tests._release_tags import skip_or_fail_missing_tag

ROOT = Path(__file__).resolve().parents[2]

#: Everything a released client's update checker and installer handoff import.
RELEASED_PATHS = ("server/__init__.py", "server/updates", "shared", "launchers", "scripts/fetch_spa.py")


def extract_release(tag: str, destination: Path) -> Path:
    archive = subprocess.run(
        ["git", "archive", tag, *RELEASED_PATHS],
        cwd=ROOT, capture_output=True, check=False,
    )
    if archive.returncode:
        skip_or_fail_missing_tag(f"Cannot extract {tag}: {archive.stderr.decode()}")
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=BytesIO(archive.stdout)) as released:
        released.extractall(destination, filter="data")
    return destination
