#!/bin/sh
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
# /bin/sh, ditto, xattr and codesign (plus PlistBuddy in --update mode).

# Exit status (what an unattended caller can rely on):
#   0  installed
#   1  failed, and the previous installation is intact (or was never touched):
#      a refusal, a bad copy, a bad signature, a failed swap that was rolled back,
#      or a TERM/HUP/INT that was rolled back
#   2  usage error, nothing changed
#   3  ROLLBACK INCOMPLETE: the old app was moved aside and could not be put
#      back, so there may be no app at the target. The backup path is printed
#      and the backup is left in place.

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
        printf 'Press Return to close...'
        read -r _unused
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
    # Remove the numeric columns, without splitting the device or mount name:
    # both may contain spaces (for example an SMB share). Refuse unknown output
    # rather than letting a failed parse bypass the read-only check.
    DF_OUTPUT="$(df -P "$TARGET_DIR" 2>/dev/null)" || \
        fail "Could not determine the volume containing $TARGET_DIR." "Nothing has been changed."
    MOUNT_POINT="$(printf '%s\n' "$DF_OUTPUT" | awk 'NR==2 {
        if (sub(/^.*[[:space:]][0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+%[[:space:]]+/, "")) print
    }')"
    [ -n "$MOUNT_POINT" ] || fail "Could not determine the volume containing $TARGET_DIR." \
                                    "Nothing has been changed."
    MOUNTS="$(mount)" || fail "Could not inspect mounted volumes." "Nothing has been changed."
    VOLUME_MOUNT="$(printf '%s\n' "$MOUNTS" | grep -F " on $MOUNT_POINT (")" || \
        fail "Could not identify the mounted volume containing $TARGET_DIR." "Nothing has been changed."
    if printf '%s\n' "$VOLUME_MOUNT" | grep -q 'read-only'; then
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

if [ "$UPDATE" -eq 1 ] && [ "$(id -u)" = "0" ]; then
    fail "Do not run --update as root." \
         "A root-owned app would refuse every later update made by the user." \
         "Nothing has been changed."
fi

# Sweep what an earlier run killed mid-install left beside the target: only
# hidden directories of exactly the shape this script creates, never symlinks.
# A .previous copy is the only remaining app when the target is missing, so it
# is kept in that case.
TARGET_BASE="$(basename -- "$TARGET")"
for stale in "$TARGET_DIR"/".$TARGET_BASE".new.* "$TARGET_DIR"/".$TARGET_BASE".previous.*; do
    [ -d "$stale" ] && [ ! -L "$stale" ] || continue
    case "$stale" in
        *.previous.*) [ -e "$TARGET" ] || continue ;;
    esac
    rm -rf "$stale"
done

# The copy is made and verified BESIDE the final name first, on the same volume,
# and only then swapped in by rename. Nothing about the installation that
# already exists is touched until the new copy has been proven good, so a failed
# copy, a failed quarantine step or a bad signature leaves the machine with the
# version it already had.
STAGED="$TARGET_DIR/.$(basename -- "$TARGET").new.$$"
DISPLACED="$TARGET_DIR/.$(basename -- "$TARGET").previous.$$"
rm -rf "$STAGED"
# A failed sweep (or a leftover symlink) must never turn mv into a nesting
# operation. Refuse occupied names before touching the current installation.
if [ -e "$STAGED" ] || [ -L "$STAGED" ] || [ -e "$DISPLACED" ] || [ -L "$DISPLACED" ]; then
    fail "A staging or backup path is still occupied." "Nothing has been changed."
fi
# Swap table (one row): LIVE_PATH=TARGET, BACKUP_PATH=DISPLACED,
# STAGED_PATH=STAGED, plus the old/new inode identities. States are staged ->
# prepared -> displace_intent -> displaced -> install_intent -> installed.
# Record intent BEFORE acting, then verify both source absence and destination
# identity. Cleanup reconciles actual objects even before post-move bookkeeping:
# prepared/displace_intent keeps or restores old; displaced/install_intent/
# installed returns new to staging via evacuate_intent -> evacuated, then
# restores old via restore_intent ->
# restored. COMMITTED alone retains new and retires old. Never delete a racer;
# a failed restore exits 3 and prints the real old-object path, including nesting.
LIVE_PATH="$TARGET"
BACKUP_PATH="$DISPLACED"
STAGED_PATH="$STAGED"
OLD_ID=""
NEW_ID=""
STATE=staged
COMMITTED=0

# Renames preserve the inode on this filesystem, including a broken symlink.
# Comparing identity, rather than existence/type, rejects both nesting and a
# plausible-looking directory created by another process.
object_id() {
    [ -e "$1" ] || [ -L "$1" ] || return 1
    identity=$(LC_ALL=C ls -di "$1" 2>/dev/null) || return 1
    identity=${identity#"${identity%%[! ]*}"}
    printf '%s\n' "${identity%% *}"
}
same_object() {
    [ -n "$2" ] && [ "$(object_id "$1")" = "$2" ]
}

# Catch signals in the parent; never ignore them in recovery children. Forward
# INT as TERM as well, because asynchronous POSIX jobs may start with INT ignored.
MOVE_PID=""
interrupt_recovery() {
    [ -z "$MOVE_PID" ] || kill -TERM "$MOVE_PID" 2>/dev/null || :
}
bounded_move() {
    (trap - HUP INT TERM; exec mv -f "$1" "$2" </dev/null) &
    MOVE_PID=$!
    (
        # A process-group signal must not cancel the deadline. It can shorten
        # it: an interrupted timer still kills the move. USR1 is our private
        # cancellation, sent only after the move has been reaped.
        trap ':' HUP INT TERM
        timer_pid=""
        trap '[ -z "$timer_pid" ] || kill "$timer_pid" 2>/dev/null || :; wait "$timer_pid" 2>/dev/null; exit' USR1
        sleep 5 &
        timer_pid=$!
        wait "$timer_pid" || :
        kill -KILL "$MOVE_PID" 2>/dev/null || :
        kill "$timer_pid" 2>/dev/null || :
        wait "$timer_pid" 2>/dev/null || :
    ) &
    watchdog_pid=$!
    move_status=1
    # A caught signal interrupts wait before the child exits. Reap that child
    # before inspecting paths, retrying, or stopping its watchdog.
    while :; do
        wait "$MOVE_PID"
        move_status=$?
        kill -0 "$MOVE_PID" 2>/dev/null || break
    done
    kill -USR1 "$watchdog_pid" 2>/dev/null || :
    wait "$watchdog_pid" 2>/dev/null || :
    MOVE_PID=""
    return "$move_status"
}

locate_old() {
    OLD_PATH=""
    for candidate in "$LIVE_PATH" "$BACKUP_PATH" "$BACKUP_PATH/$TARGET_BASE" "$LIVE_PATH/${BACKUP_PATH##*/}"; do
        if same_object "$candidate" "$OLD_ID"; then OLD_PATH="$candidate"; return 0; fi
    done
    return 1
}
locate_new() {
    NEW_PATH=""
    for candidate in "$STAGED_PATH" "$LIVE_PATH" "$LIVE_PATH/${STAGED_PATH##*/}"; do
        if same_object "$candidate" "$NEW_ID"; then NEW_PATH="$candidate"; return 0; fi
    done
    return 1
}
verify_move() {
    ! { [ -e "$1" ] || [ -L "$1" ]; } && same_object "$2" "$3"
}
restore_row() {
    [ "$STATE" != staged ] || return 0
    if locate_new && [ "$NEW_PATH" != "$STAGED_PATH" ]; then
        [ ! -e "$STAGED_PATH" ] && [ ! -L "$STAGED_PATH" ] || return 1
        STATE=evacuate_intent
        for attempt in 1 2; do
            bounded_move "$NEW_PATH" "$STAGED_PATH" || :
            verify_move "$NEW_PATH" "$STAGED_PATH" "$NEW_ID" && break
            locate_new || return 1
        done
        same_object "$STAGED_PATH" "$NEW_ID" || return 1
        STATE=evacuated
    fi
    if [ -z "$OLD_ID" ]; then
        [ ! -e "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ]
        return
    fi
    locate_old || return 1
    [ "$OLD_PATH" != "$LIVE_PATH" ] || return 0
    [ ! -e "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ] || return 1
    STATE=restore_intent
    for attempt in 1 2; do
        bounded_move "$OLD_PATH" "$LIVE_PATH" || :
        if verify_move "$OLD_PATH" "$LIVE_PATH" "$OLD_ID"; then
            STATE=restored
            return 0
        fi
        locate_old || return 1
        [ ! -e "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ] || return 1
    done
    return 1
}
cleanup() {
    status=$?
    trap - 0
    trap 'interrupt_recovery' HUP INT TERM
    if [ "$COMMITTED" -eq 1 ]; then
        status=0
    elif restore_row; then
        status=1
        [ -z "$OLD_ID" ] || [ "$STATE" != restored ] || printf 'Restored the previous installation.\n'
    else
        printf 'ERROR: could not restore the previous installation.\n' >&2
        if locate_old; then
            printf 'The previous app is at: %s\n' "$OLD_PATH" >&2
        else
            printf 'ERROR: recorded previous app is missing from its recovery paths.\n' >&2
        fi
        status=3
    fi
    rm -rf "$STAGED_PATH"
    exit "$status"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

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

NEW_ID="$(object_id "$STAGED_PATH")" || fail "The staged app is missing."
OLD_ID="$(object_id "$LIVE_PATH")" || OLD_ID=""
STATE=prepared
if [ -n "$OLD_ID" ]; then
    printf 'Replacing the copy already in %s ...\n' "$TARGET_DIR"
    if [ -e "$BACKUP_PATH" ] || [ -L "$BACKUP_PATH" ]; then
        fail "The backup path is still occupied." "Nothing has been changed."
    fi
    STATE=displace_intent
    mv -f "$LIVE_PATH" "$BACKUP_PATH" </dev/null
    move_status=$?
    if ! verify_move "$LIVE_PATH" "$BACKUP_PATH" "$OLD_ID" || [ "$move_status" -ne 0 ]; then
        fail "Could not move the existing installation aside." \
             "Quit Waveguide Generator if it is running, then try again."
    fi
fi
STATE=displaced
[ ! -e "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ] || fail "The app destination is occupied."
STATE=install_intent
mv -f "$STAGED_PATH" "$LIVE_PATH" </dev/null
move_status=$?
if ! verify_move "$STAGED_PATH" "$LIVE_PATH" "$NEW_ID" || [ "$move_status" -ne 0 ]; then
    fail "Could not put the new version in place at $TARGET."
fi
STATE=installed
COMMITTED=1
if ! rm -rf "$DISPLACED" || [ -e "$DISPLACED" ] || [ -L "$DISPLACED" ]; then
    printf 'WARNING: installed successfully, but could not fully remove the previous copy.\n' >&2
    printf 'The leftover backup is at: %s\n' "$DISPLACED" >&2
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
