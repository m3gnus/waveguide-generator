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
# /bin/bash, ditto, xattr and codesign.

set -u

APP_NAME="Waveguide Generator.app"
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
SOURCE="$HERE/$APP_NAME"

# Finder passes no arguments, so a double-click always installs to
# /Applications. The optional first argument names a different folder; that is
# how the tests exercise the copy, the replacement and the quarantine removal
# without writing into the real /Applications, and it is also the escape hatch
# for anyone who keeps applications elsewhere.
DEFAULT_TARGET_DIR="${1:-/Applications}"

fail() {
    printf '\n'
    printf '===============================================================\n'
    printf '%s\n' "$@"
    printf '===============================================================\n'
    printf '\n'
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

# Displace any previous copy rather than deleting it, so a failed copy leaves
# the machine with the version it already had instead of nothing.
DISPLACED=""
if [ -e "$TARGET" ]; then
    DISPLACED="$TARGET_DIR/.Waveguide Generator.app.previous.$$"
    printf 'Replacing the copy already in %s ...\n' "$TARGET_DIR"
    mv "$TARGET" "$DISPLACED" || fail "Could not move the existing installation aside." \
                                      "Quit Waveguide Generator if it is running, then try again."
fi

printf 'Copying to %s ...\n' "$TARGET_DIR"
if ! ditto "$SOURCE" "$TARGET"; then
    rm -rf "$TARGET"
    if [ -n "$DISPLACED" ]; then
        mv "$DISPLACED" "$TARGET" && printf 'Restored the previous installation.\n'
    fi
    fail "Could not copy the app to $TARGET_DIR."
fi
if [ -n "$DISPLACED" ]; then
    rm -rf "$DISPLACED"
fi

# The point of the whole exercise. Everything read out of a quarantined disk
# image inherits com.apple.quarantine, so the fresh copy carries it on every
# one of its several thousand files until this runs.
printf 'Clearing the download quarantine flag ...\n'
if ! xattr -dr com.apple.quarantine "$TARGET"; then
    fail "Could not clear the quarantine flag from $TARGET."
fi

# ditto preserves the signature, so this normally passes untouched and costs a
# few seconds. Re-sign only when it does not: an ad-hoc signature that no longer
# seals the bundle would leave the app unlaunchable with no explanation.
printf 'Checking the app signature ...\n'
if ! codesign --verify --deep --strict "$TARGET" >/dev/null 2>&1; then
    printf 'Re-signing the copy (this takes a moment) ...\n'
    codesign --force --deep --sign - "$TARGET" >/dev/null 2>&1 || true
    if ! codesign --verify --deep --strict "$TARGET" >/dev/null 2>&1; then
        fail "The copy in $TARGET_DIR does not have a valid signature." \
             "macOS will refuse to start it. Delete it and try again."
    fi
fi

printf '\n'
printf 'Installed: %s\n' "$TARGET"
printf '\n'
printf 'You can eject the Waveguide Generator disk image now.\n'
# Only the double-click path starts the app. Someone who named a destination
# asked to install it, not to run it, and the tests rely on that.
if [ "$#" -eq 0 ]; then
    printf 'Starting Waveguide Generator ...\n'
    open "$TARGET" || printf 'Could not start it automatically; open it from %s.\n' "$TARGET_DIR"
fi
exit 0
