#!/bin/bash
# Double-click this in Finder to install Waveguide Generator from the disk
# image. It is shipped INSIDE the .dmg, beside the app; it is not the source
# installer, which is installers/macos/install-wg.command in the checkout.
#
# Why it exists. The app is ad-hoc signed rather than notarized, and a
# quarantined ad-hoc bundle assesses as `rejected` with NO source line at all,
# so Gatekeeper has nothing to attach an exception to and Privacy & Security
# lists nothing to approve. An unsigned script assesses as
# `rejected  source=no usable signature`, which is the state that does get an
# override. Measured 2026-09-02 on macOS 26.5.2; see docs/validation/2026-09/MACOS-GATEKEEPER.md.
#
# So this script is approvable where the app is not, and once it runs it does by
# hand what the user would otherwise open Terminal for: copy the app to
# Applications and clear the quarantine flag from the copy.
#
# It must stay self-contained. It runs from a read-only mounted volume with
# nothing else from the checkout beside it, and its only dependencies are
# /bin/bash, ditto, xattr and codesign (plus PlistBuddy in --update mode).

set -u

APP_NAME="Waveguide Generator.app"
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
SOURCE="$HERE/$APP_NAME"

# Finder passes no arguments, so a double-click always installs to
# /Applications. The optional first argument names a different folder; that is
# how the tests exercise the copy, the replacement and the quarantine removal
# without writing into the real /Applications, and it is also the escape hatch
# for anyone who keeps applications elsewhere.
#
# `--update <exact .app path>` is the unattended mode the in-app updater's
# helper runs. It replaces exactly that bundle and nothing else, never falls
# back to another folder, never prompts, and never re-signs (see below).
UPDATE=0
UPDATE_TARGET=""
if [ "${1:-}" = "--update" ]; then
    UPDATE=1
    if [ "$#" -ne 2 ] || [ -z "$2" ]; then
        printf 'usage: dmg-install.command --update <path to the installed .app>\n' >&2
        exit 2
    fi
    UPDATE_TARGET="$2"
    DEFAULT_TARGET_DIR=""
else
    DEFAULT_TARGET_DIR="${1:-/Applications}"
fi

fail() {
    printf '\n'
    printf '===============================================================\n'
    printf '%s\n' "$@"
    printf '===============================================================\n'
    printf '\n'
    if [ "$UPDATE" -eq 1 ]; then
        # Unattended: no hand-install advice for a different folder, no prompt.
        exit 1
    fi
    printf 'You can still install by hand: drag the app to Applications, then\n'
    printf 'run this once in Terminal:\n'
    printf '\n'
    printf '  xattr -dr com.apple.quarantine "/Applications/%s"\n' "$APP_NAME"
    printf '\n'
    if [ -t 0 ]; then
        read -r -p "Press Return to close..." _unused
    fi
    exit 1
}

bundle_identifier() {
    /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist" 2>/dev/null
}

printf '\n'
printf 'Installing Waveguide Generator\n'
printf '==============================\n'
printf '\n'

if [ ! -d "$SOURCE" ]; then
    fail "\"$APP_NAME\" is not beside this installer." \
         "Looked in: $HERE" \
         "" \
         "Run the installer from inside the disk image, without copying it" \
         "somewhere else first."
fi

if [ "$UPDATE" -eq 1 ]; then
    # Update mode replaces one exact path. Every check below fails closed: an
    # update that cannot be done in place is reported, not redirected.
    TARGET="$UPDATE_TARGET"
    case "$TARGET" in
        /*) ;;
        *) fail "--update needs an absolute path: $TARGET" "Nothing has been changed." ;;
    esac
    case "$TARGET" in
        */AppTranslocation/*)
            fail "The app is running from a translocated copy: $TARGET" \
                 "macOS is running it from a read-only quarantine location, so it cannot be" \
                 "updated in place. Move it to Applications, open it once, then update." \
                 "Nothing has been changed."
            ;;
    esac
    TARGET_DIR="$(dirname -- "$TARGET")"
    if [ ! -d "$TARGET" ] || [ -L "$TARGET" ]; then
        fail "There is no app bundle at: $TARGET" "Nothing has been changed."
    fi
    EXPECTED_ID="$(bundle_identifier "$SOURCE")"
    if [ -z "$EXPECTED_ID" ] || [ "$(bundle_identifier "$TARGET")" != "$EXPECTED_ID" ]; then
        fail "$TARGET is not a Waveguide Generator app." "Nothing has been changed."
    fi
    MOUNT_POINT="$(df -P "$TARGET_DIR" 2>/dev/null | awk 'NR==2 { $1=$2=$3=$4=$5=""; sub(/^ +/, ""); print }')"
    if [ -n "$MOUNT_POINT" ] && mount | grep -F " on $MOUNT_POINT (" | grep -q 'read-only'; then
        fail "$TARGET_DIR is on a read-only volume, so the app cannot be updated there." \
             "Nothing has been changed."
    fi
    if [ ! -w "$TARGET_DIR" ]; then
        fail "$TARGET_DIR is not writable by this account." \
             "The app is not updated, and no other folder is tried." \
             "Nothing has been changed."
    fi
else
    # /Applications is group-writable by admin users, which is the common case. A
    # standard account gets ~/Applications instead rather than an authentication
    # prompt this script has no safe way to satisfy.
    TARGET_DIR="$DEFAULT_TARGET_DIR"
    if [ ! -w "$TARGET_DIR" ]; then
        TARGET_DIR="$HOME/Applications"
        mkdir -p "$TARGET_DIR" || fail "Could not create $TARGET_DIR."
        printf '%s is not writable by this account.\n' "$DEFAULT_TARGET_DIR"
        printf 'Installing to %s instead.\n\n' "$TARGET_DIR"
    fi
    TARGET="$TARGET_DIR/$APP_NAME"
fi

# The copy is made and verified BESIDE the final name first, on the same volume,
# and only then swapped in by rename. Nothing about the installation that
# already exists is touched until the new copy has been proven good, so a failed
# copy, a failed quarantine step or a bad signature leaves the machine with the
# version it already had.
STAGED="$TARGET_DIR/.$(basename -- "$TARGET").new.$$"
DISPLACED="$TARGET_DIR/.$(basename -- "$TARGET").previous.$$"
rm -rf "$STAGED"

printf 'Copying to %s ...\n' "$TARGET_DIR"
if ! ditto "$SOURCE" "$STAGED"; then
    rm -rf "$STAGED"
    fail "Could not copy the app to $TARGET_DIR."
fi

# The point of the whole exercise. Everything read out of a quarantined disk
# image inherits com.apple.quarantine, so the fresh copy carries it on every
# one of its several thousand files until this runs.
printf 'Clearing the download quarantine flag ...\n'
if ! xattr -dr com.apple.quarantine "$STAGED"; then
    rm -rf "$STAGED"
    fail "Could not clear the quarantine flag from the copy."
fi

# ditto preserves the signature, so this normally passes untouched and costs a
# few seconds.
printf 'Checking the app signature ...\n'
if ! codesign --verify --deep --strict "$STAGED" >/dev/null 2>&1; then
    if [ "$UPDATE" -eq 1 ]; then
        # Never re-sign here. An ad-hoc signature carries no identity, so it
        # would add no authenticity, and a seal that fails after a verified
        # copy means a damaged copy, which re-signing would bless.
        rm -rf "$STAGED"
        fail "The copy of the new version does not have a valid signature." \
             "It was not installed, and the current version was left in place."
    fi
    # Interactive install: re-sign only when the seal is broken, because an
    # ad-hoc signature that no longer seals the bundle would leave the app
    # unlaunchable with no explanation.
    printf 'Re-signing the copy (this takes a moment) ...\n'
    codesign --force --deep --sign - "$STAGED" >/dev/null 2>&1 || true
    if ! codesign --verify --deep --strict "$STAGED" >/dev/null 2>&1; then
        rm -rf "$STAGED"
        fail "The copy in $TARGET_DIR does not have a valid signature." \
             "macOS will refuse to start it. The previous installation was left in place."
    fi
fi

# Displace any previous copy rather than deleting it, so a failed rename leaves
# the machine with the version it already had instead of nothing.
HAD_PREVIOUS=0
if [ -e "$TARGET" ]; then
    HAD_PREVIOUS=1
    printf 'Replacing the copy already in %s ...\n' "$TARGET_DIR"
    if ! mv "$TARGET" "$DISPLACED"; then
        rm -rf "$STAGED"
        fail "Could not move the existing installation aside." \
             "Quit Waveguide Generator if it is running, then try again."
    fi
fi
if ! mv "$STAGED" "$TARGET"; then
    rm -rf "$STAGED"
    if [ "$HAD_PREVIOUS" -eq 1 ]; then
        mv "$DISPLACED" "$TARGET" && printf 'Restored the previous installation.\n'
    fi
    fail "Could not put the new version in place at $TARGET."
fi
if [ "$HAD_PREVIOUS" -eq 1 ]; then
    rm -rf "$DISPLACED"
fi

printf '\n'
printf 'Installed: %s\n' "$TARGET"
printf '\n'
if [ "$UPDATE" -eq 1 ]; then
    exit 0
fi
printf 'You can eject the Waveguide Generator disk image now.\n'
# Only the double-click path starts the app. Someone who named a destination
# asked to install it, not to run it, and the tests rely on that.
if [ "$#" -eq 0 ]; then
    printf 'Starting Waveguide Generator ...\n'
    open "$TARGET" || printf 'Could not start it automatically; open it from %s.\n' "$TARGET_DIR"
fi
exit 0
