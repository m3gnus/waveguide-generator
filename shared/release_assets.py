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

import re


#: Prefix for everything that is machinery rather than a download.
UPDATE_PREFIX = "update"

MACOS_PLATFORM = "macos-arm64"
WINDOWS_PLATFORM = "windows-x86_64"

#: What every file built for a person rather than for the updater is named
#: after. It marks a file as *not* machinery; it does not decide which release
#: the file lands on, because Windows builds two such files and only one of them
#: belongs on the page a user reads. See ``is_user_download``.
INSTALLER_PREFIX = "Waveguide.Generator-"


def installer_name(platform: str, version: str) -> str | None:
    """The self-contained bundle for a platform, as a single file.

    On macOS that is the disk image, which is also what a person downloads. On
    Windows it is the portable folder as a .zip, which is *not*: the setup .exe
    is. Use ``user_download_name`` for the question "what does a person click".

    Dots rather than spaces because those are the names GitHub serves; the
    installed application and the extracted Windows folder keep their spaces.
    """

    if platform == MACOS_PLATFORM:
        return f"{INSTALLER_PREFIX}{version}-{MACOS_PLATFORM}.dmg"
    if platform == WINDOWS_PLATFORM:
        return f"{INSTALLER_PREFIX}{version}-{WINDOWS_PLATFORM}.zip"
    return None


def windows_setup_name(version: str) -> str:
    """The Windows installer, and the only Windows file on the release page.

    It supersedes the portable .zip for anyone landing on a release. They fail
    differently: the installer writes its own payload, so nothing it installs
    carries the download mark that makes Explorer-extracted copies meet
    SmartScreen, and it can refuse an over-long install root before writing
    anything -- the two things the .zip made every user get right by hand.

    The .zip is still built and still published, on the companion release, for
    people who deliberately want a portable copy. It is off the page a user
    lands on because a third file there is a third choice to make, and it is
    the choice that fails.
    """

    return f"{INSTALLER_PREFIX}{version}-{WINDOWS_PLATFORM}-setup.exe"


def user_download_name(platform: str, version: str) -> str | None:
    """The one file a person downloads for their platform.

    This is the release page's whole contract: one row per platform, one file
    per row. macOS gets the disk image; Windows gets the setup .exe rather than
    the portable .zip beside it.
    """

    if platform == MACOS_PLATFORM:
        return installer_name(MACOS_PLATFORM, version)
    if platform == WINDOWS_PLATFORM:
        return windows_setup_name(version)
    return None


def user_download_names(version: str) -> tuple[str, ...]:
    """Exactly the assets the user-facing release carries.

    Named as a set rather than derived from a prefix because the prefix cannot
    answer it: the portable Windows .zip is built for a person and still does
    not belong here. A new platform is one entry added in one place, and the
    publish job asserts the release equals this set -- so a build that produced
    the right files and staged them into the wrong halves fails rather than
    shipping a page nobody meant.
    """

    return tuple(
        name
        for name in (
            user_download_name(MACOS_PLATFORM, version),
            user_download_name(WINDOWS_PLATFORM, version),
        )
        if name is not None
    )


def is_user_download(name: str, version: str) -> bool:
    """Whether ``name`` belongs on the release page a person lands on."""

    return name in user_download_names(version)


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


def runtime_id_of(name: str) -> str | None:
    """The runtime id a runtime layer's filename carries, or None.

    The inverse of ``runtime_layer_name``, and the only place that parsing is
    written: the transitional duplication has to ask "is this the interpreter
    the previous release already installed?", and answering it from the filename
    means the publish job needs no network call to find out.
    """

    prefix = f"{UPDATE_PREFIX}-runtime-"
    if not name.startswith(prefix) or not name.endswith(".zip"):
        return None
    remainder = name[len(prefix) : -len(".zip")]
    platform, separator, runtime_id = remainder.rpartition("-")
    if not separator or not platform or not runtime_id:
        return None
    return runtime_id


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
    holds exactly what a person downloads and nothing else: one file per
    platform, ``user_download_names``. Everything else -- every update layer,
    the SPA archive, and the portable Windows .zip, which is a real download but
    not the one to offer first -- lives on a companion release, tagged
    ``v<version>-updates`` and flagged as a pre-release so it is never "Latest"
    and is visually demoted in the list.

    It is a separate RELEASE rather than a separate repository deliberately: the
    assets stay inside this repository, so ``trusted_asset_url`` keeps rejecting
    anything served from elsewhere, and the trust boundary does not widen to buy
    a tidier page.
    """

    return f"v{version}-updates"


UPDATES_TAG_SUFFIX = "-updates"

#: ``<major>.<minor>.<patch>`` with an optional SemVer pre-release label. Dots
#: appear only between runs of alphanumerics and hyphens, so no label can be
#: ``.``, ``..``, or end in a dot.
_SEMVER = r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"

#: A version, as it appears inside an asset name: ``0.4.0``, ``0.4.0-beta.1``.
VERSION_RE = re.compile(rf"^{_SEMVER}$")

#: A release tag: a version with its ``v``. A companion tag is one of these plus
#: ``UPDATES_TAG_SUFFIX``, so a beta's companion is ``v0.4.0-beta.1-updates``.
#:
#: These live here, with the names, because three places have to agree on them
#: and cannot all import each other: the updater parses tags to order versions,
#: ``server/updates/bundle.py`` matches them to decide whether an asset URL is
#: one of this repository's, and the same module matches versions to decide
#: whether an install may start. They drifted once -- the URL and install checks
#: kept the narrow ``\d+\.\d+\.\d+`` shape after the beta channel widened the
#: parser, so every asset of every beta was refused as untrusted and no beta
#: could have been installed. One pattern is the fix that cannot drift again.
TAG_RE = re.compile(rf"^v{_SEMVER}$")


def is_release_tag(tag: str) -> bool:
    """Whether ``tag`` names a release of this project, or its companion.

    Shape only. Whether a tag may be *offered* to a given channel is a separate
    question, and a stricter one -- see ``server/updates/service.py``, where a
    companion is never a release and a pre-release reaches only the beta channel.
    """

    return TAG_RE.fullmatch(tag.removesuffix(UPDATES_TAG_SUFFIX)) is not None

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

#: The runtime id 0.3.0 shipped, on both platforms.
#:
#: An install already has its runtime, and the updater skips the runtime layer
#: when the release names the one it is running -- so what a 0.3.0 client needs
#: duplicated is the app layer and its manifest, and the runtime layers only if
#: the interpreter actually moved. That is the difference between 4.4 MB and
#: 382 MB: 0.3.0's two runtime layers are 178 MB and 200 MB, and putting those
#: on the page a user lands on would be a worse page than the one this whole
#: change exists to fix. Read off the published v0.3.0 assets, which are
#: immutable, so this constant cannot go stale; it retires with the duplication.
PRE_SPLIT_RUNTIME_ID = "75ecf8fdbb99"


def duplicate_layers_on_user_release(version: str) -> bool:
    """Whether ``version`` also publishes layers to the user-facing release."""

    return version == LAYER_DUPLICATION_VERSION


def duplicate_on_user_release(name: str, version: str) -> bool:
    """Whether this one companion asset is *also* put on the user-facing release.

    Not every layer, only what a 0.3.0 install actually resolves from the
    release it lands on:

    * the app layer and its manifest, always -- without them the release reports
      ``assetsReady: False`` and offers nothing;
    * a runtime layer only when the interpreter changed, since a client running
      ``PRE_SPLIT_RUNTIME_ID`` needs no runtime at all. If it did change, both
      platforms' layers are copied and the page is briefly ugly, which is the
      right trade against stranding those installs.

    The SPA archive and its sidecar are never copied. A bundle install does not
    read them, and a source install checks out the new tag before running
    ``scripts/fetch_spa.py``, so it runs the new fetcher, which already knows to
    look on the companion.
    """

    if not duplicate_layers_on_user_release(version):
        return False
    if name in {app_layer_name(version), app_manifest_name(version)}:
        return True
    runtime_id = runtime_id_of(name)
    return runtime_id is not None and runtime_id != PRE_SPLIT_RUNTIME_ID


def checksum_name(asset: str) -> str:
    """The sidecar carrying an asset's SHA-256."""

    return f"{asset}.sha256"
