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

#: What every file a person downloads is named after. The release split is drawn
#: on this prefix rather than on a list of filenames, so adding a platform -- or
#: a second installer for one, as Windows now has -- cannot quietly put a
#: download on the machinery release or a layer on the page users read.
INSTALLER_PREFIX = "Waveguide.Generator-"


def installer_name(platform: str, version: str) -> str | None:
    """The one file a person downloads for their platform.

    Dots rather than spaces because those are the names GitHub serves; the
    installed application and the extracted Windows folder keep their spaces.
    """

    if platform == MACOS_PLATFORM:
        return f"{INSTALLER_PREFIX}{version}-{MACOS_PLATFORM}.dmg"
    if platform == WINDOWS_PLATFORM:
        return f"{INSTALLER_PREFIX}{version}-{WINDOWS_PLATFORM}.zip"
    return None


def windows_setup_name(version: str) -> str:
    """The Windows installer, and the file the release page points Windows at.

    It exists beside the .zip rather than replacing it because they fail
    differently: the installer writes its own payload, so nothing it installs
    carries the download mark that makes Explorer-extracted copies meet
    SmartScreen, and it can refuse an over-long install root before writing
    anything. The .zip stays for people who want a portable copy and are
    willing to do both by hand.
    """

    return f"{INSTALLER_PREFIX}{version}-{WINDOWS_PLATFORM}-setup.exe"


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


def updates_tag(version: str) -> str:
    """The tag of the companion release that carries the update layers.

    The user-facing release page is a list of files with no explanation, so it
    holds only what a person downloads: the installers, and nothing else. (That
    is not one file per platform -- Windows publishes both a setup .exe and a
    portable .zip -- which is why the split is drawn on ``INSTALLER_PREFIX``
    rather than on a count.) Everything the in-app updater consumes lives on a
    companion release, tagged
    ``v<version>-updates`` and flagged as a pre-release so it is never "Latest"
    and is visually demoted in the list.

    It is a separate RELEASE rather than a separate repository deliberately: the
    assets stay inside this repository, so ``trusted_asset_url`` keeps rejecting
    anything served from elsewhere, and the trust boundary does not widen to buy
    a tidier page.
    """

    return f"v{version}-updates"


UPDATES_TAG_SUFFIX = "-updates"

#: The one version whose update layers are published to BOTH releases.
#:
#: 0.3.0's updater reads the layers from the release it lands on, and its
#: ``TAG_RE`` (``^v\d+\.\d+\.\d+$``) rejects the ``-updates`` suffix outright,
#: so a clean split would leave every 0.3.0 install reporting "update preparing"
#: forever -- confidently telling people to wait for something that can never
#: arrive, which is worse than failing visibly. Publishing the layers twice for
#: one version lets 0.3.0 update once. From the next version every install reads
#: the companion, and this constant, the branch it guards in release.yml, and
#: the test named after it all go together.
LAYER_DUPLICATION_VERSION = "0.3.1"


def duplicate_layers_on_user_release(version: str) -> bool:
    """Whether ``version`` also publishes its layers to the user-facing release."""

    return version == LAYER_DUPLICATION_VERSION


def checksum_name(asset: str) -> str:
    """The sidecar carrying an asset's SHA-256."""

    return f"{asset}.sha256"
