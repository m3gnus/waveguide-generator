"""Every release asset name, in one place.

These names were constructed independently in four production modules and
asserted as literals in a dozen tests. That is workable while the names never
change and a trap the moment they do: the builder writes them, the updater
reconstructs them by exact string, the SPA fetcher builds a fifth, and nothing
connected the four. A rename would have been four coordinated edits with no
failing test in between.

The names also have to say something to a person. A GitHub release page is a
list of files with no explanation, and the previous scheme put four files there
that read as platform downloads -- a macOS installer, a Windows installer, and
two runtime layers named after the same two platforms. Users reasonably asked
which one to download.

So there are exactly two kinds:

* **Installers**, named for the platform, which is what a person downloads.
* **``update-`` prefixed layers**, which are machinery the in-app updater and
  the source installer consume. The prefix is the whole point: it says "not for
  you" to a reader scanning the list, and it does so without hiding anything.

Renaming was safe to do exactly once, at 3.0.0. The updater reconstructs asset
names by exact string, so a rename breaks auto-update for any client already
installed -- but the standalone app only ever shipped in v0.2.7, which was
withdrawn, and v0.2.2 through v0.2.4 published only the SPA archive. No
installed client had a working auto-update path to reach 3.0.0 by either name.
"""

from __future__ import annotations


#: Prefix for everything that is machinery rather than a download.
UPDATE_PREFIX = "update"

MACOS_PLATFORM = "macos-arm64"
WINDOWS_PLATFORM = "windows-x86_64"


def installer_name(platform: str, version: str) -> str | None:
    """The one file a person downloads for their platform.

    Dots rather than spaces because those are the names GitHub serves; the
    installed application and the extracted Windows folder keep their spaces.
    """

    if platform == MACOS_PLATFORM:
        return f"Waveguide.Generator-{version}-{MACOS_PLATFORM}.dmg"
    if platform == WINDOWS_PLATFORM:
        return f"Waveguide.Generator-{version}-{WINDOWS_PLATFORM}.zip"
    return None


def app_layer_name(version: str) -> str:
    """The platform-neutral application layer, rebuilt every release."""

    return f"{UPDATE_PREFIX}-app-{version}.zip"


def app_manifest_name(version: str) -> str:
    """The app layer's manifest, carrying its treeSha256 and runtimeId."""

    return f"{UPDATE_PREFIX}-app-{version}.manifest.json"


def runtime_layer_name(platform: str, runtime_id: str) -> str:
    """A runtime layer, addressed by content.

    The runtime id rather than the version is deliberate: an interpreter that
    did not change is the same layer, so releases share it and the updater
    downloads only what actually moved.
    """

    return f"{UPDATE_PREFIX}-runtime-{platform}-{runtime_id}.zip"


def spa_archive_name(version: str) -> str:
    """The prebuilt frontend, so a source install needs no Node runtime.

    Named with the update prefix like the other layers even though installs
    consume it too, because the question it has to answer on a release page is
    "is this the thing I download?", and the answer is no. Its ``.tar.gz``
    suffix is why it was mistaken for a Linux build; there is no Linux binary.
    """

    return f"{UPDATE_PREFIX}-spa-{version}.tar.gz"


def checksum_name(asset: str) -> str:
    """The sidecar carrying an asset's SHA-256."""

    return f"{asset}.sha256"
