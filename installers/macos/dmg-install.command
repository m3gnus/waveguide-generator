#!/bin/sh
# POSIX /bin/sh (Bash 3.2 on macOS); do not use Bash-only syntax.
# Double-click this in Finder to install Waveguide Generator from the disk
# image. It is shipped INSIDE the .dmg, beside the app; it is not the source
# installer, which is installers/macos/install-wg.command in the checkout.
#
# The app is ad-hoc signed rather than notarized. Both the app and this script
# can be approved through Privacy & Security. This script offers another route:
# copy the app to Applications and clear the quarantine flag from the copy.
# See docs/validation/2026-09/MACOS-GATEKEEPER.md.
#
# It must stay self-contained. It runs from a read-only mounted volume with
# nothing else from the checkout beside it, and its only dependencies are
# standard macOS shell tools, ditto, xattr and codesign (plus PlistBuddy
# in --update mode).

# Exit status (what an unattended caller can rely on):
#   0  installed
#   1  failed or interrupted; this run kept/restored the previous objects,
#      or displaced nothing. A missing target on entry remains missing.
#   2  usage error, nothing changed
#   3  ROLLBACK INCOMPLETE: recovery paths for preserved objects are printed;
#      a noncooperating replacement can also make the recorded old object missing.
#   4  existing installer lock; the refused run changes nothing on disk.
#
# Minimal lock: mkdir beside the resolved target, held through cleanup. Existing
# locks are NEVER reclaimed automatically, even after SIGKILL or power loss.
# After checking no installer is running, remove the exact lock path printed by
# the refused run using its command; inspect unexpected contents first.
# This is per-target exclusion.
# The updater helper owns staleness policy (UPDATER-PLAN.md section 9).
# Statuses verify recorded object identities, not external edits to their
# contents. Recovery assumes native same-device rename/link operations.
# Known limit: there is no journal. SIGKILL/power loss between the two renames
# can leave the target absent; a rerun does not discover/restore the backup.
# Look beside the target for .<app basename>.previous.<installer PID>.
# A hard kill can also leave a half-copied .<app basename>.new.<installer PID>;
# after checking no installer is running, that staging copy is safe to delete.
# Cleanup finishes within 300 seconds unless the kernel itself blocks a kill
# (excluding the macOS close prompt's intentional user wait). Work has a shared
# 20-second budget; metadata/housekeeping/output steps have short polling
# deadlines, moves a five-second watchdog, then KILL and reap. Lock release and recovery
# reporting also use bounded steps. A timed-out restore is status 3.
# Journal recovery and fuller sweep/lock rules await the updater handoff stage.

# A signal before the traps ends a run that has done nothing; the flag never
# comes from the environment.
INTERRUPTED=0
# Install these before any other executable line. Signals only record intent
# until cleanup or commit makes the rest of the run uninterruptible.
trap 'INTERRUPTED=1' HUP INT TERM QUIT
trap cleanup 0
trap '' PIPE
CLEANING=0
EXIT_STATUS=1
SWAP_READY=0
COMMITTED=0
LOCK_PATH=""
LOCK_ID=""
LOCK_HELD=0
MOVE_PID=""
WORK_CLOCK=""
PRINT_CLOCK=""
PROMPT_ON_FAILURE=0
set -u
# Render once to a private file. A killed renderer can safely be retried;
# only emission is retried after rendering. cat emits each short record at once.
# A KILL during emission can race its completed write: parsers must tolerate a
# repeated line. Catchable cleanup signals cannot interrupt the emitter.
printf() {
    print_file=$(OUTPUT_BOUND=independent protected_output mktemp "${TMPDIR:-/tmp}/wg-installer-message.XXXXXX") || return 1
    (trap '' HUP INT TERM QUIT; exec sleep 1) &
    PRINT_CLOCK=$!
    for print_attempt in 1 2 3 4 5 6 7 8 9 10; do
        (command printf "$@") > "$print_file" &
        render_pid=$!
        wait_for_child "$render_pid" 100 message
        print_status=$?
        [ "$print_status" -gt 128 ] || break
    done
    if [ "$print_status" -eq 0 ]; then
        for print_attempt in 1 2 3 4 5 6 7 8 9 10; do
            (trap '' HUP INT TERM QUIT; exec cat "$print_file") &
            print_pid=$!
            wait_for_child "$print_pid" 100 message
            print_status=$?
            [ "$print_status" -gt 128 ] || break
        done
    fi
    kill -KILL "$PRINT_CLOCK" 2>/dev/null || :
    wait_for_child "$PRINT_CLOCK" 100 independent 2>/dev/null || :
    PRINT_CLOCK=""
    (trap '' HUP INT TERM QUIT; exec rm -f "$print_file") &
    print_remove_pid=$!
    wait_for_child "$print_remove_pid" 100 independent "remove message $print_file" || :
    return "$print_status"
}

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
    PROMPT_ON_FAILURE=1
    exit 1
}

close_prompt() {
    if [ "$PROMPT_ON_FAILURE" -eq 1 ] && [ -t 0 ]; then
        # Recovery and lock release are finished. Resume ordinary cancellation
        # before displaying the prompt; retain the already-decided status rather
        # than replacing it with the shell's 128+signal default exit code.
        trap 'exit "$status"' HUP INT TERM QUIT
        printf 'Press Return to close...' || :
        read -r _unused || :
    fi
}

# Identity is (device, inode), valid only while the recorded object survives.
# stat does not follow symlinks, including broken command links.
# Always called in a command substitution: ignore group signals at once, and
# callers retry a lookup a signal still managed to kill (status above 128).
object_id() {
    trap '' HUP INT TERM QUIT
    [ -e "$1" ] || [ -L "$1" ] || return 1
    protected_output stat -f '%d:%i' "$1" 2>/dev/null
}

same_object() {
    [ -n "$2" ] || return 1
    for same_attempt in 1 2 3; do
        current_id=$(object_id "$1")
        same_status=$?
        [ "$same_status" -gt 128 ] && continue
        [ "$same_status" -eq 0 ] && [ "$current_id" = "$2" ]
        return
    done
    return 1
}

same_device() {
    source_id=$(object_id "$1") || return 1
    parent_id=$(object_id "${2%/*}") || return 1
    if [ "${source_id%%:*}" != "${parent_id%%:*}" ]; then
        printf 'ERROR: staging and destination are on different devices: %s -> %s\n' "$1" "$2" >&2
        return 1
    fi
    if [ -e "$2" ] || [ -L "$2" ]; then
        destination_id=$(object_id "$2") || return 1
        if [ "${source_id%%:*}" != "${destination_id%%:*}" ]; then
            printf 'ERROR: destination is on a different device: %s\n' "$2" >&2
            return 1
        fi
    fi
}

# Capturing shells and children ignore group cancellation while registering
# resources. The same deadline covers metadata, housekeeping and emission.
protected_output() {
    trap '' HUP INT TERM QUIT
    (exec "$@") &
    output_pid=$!
    wait_for_child "$output_pid" 100 "${OUTPUT_BOUND:-budgeted}" "$*"
}

# One bounded wait: deadline, KILL, reap, then report. Only kernel-blocked KILL
# can prevent reaping. 100 slow polls allow one second plus dispatch overhead;
# moves retain their five-second watchdog.
wait_for_child() {
    bound_ticks=0
    bound_fast=0
    bound_limit=${2:-100}
    bound_timeout=0
    while kill -0 "$1" 2>/dev/null; do
        if [ "$bound_ticks" -ge "$bound_limit" ] ||
           { [ "${3:-budgeted}" = message ] && ! kill -0 "$PRINT_CLOCK" 2>/dev/null; } ||
           { [ "${3:-budgeted}" = budgeted ] && [ -n "$WORK_CLOCK" ] && ! kill -0 "$WORK_CLOCK" 2>/dev/null; }; then
            bound_timeout=1
            kill -KILL "$1" 2>/dev/null || :
            break
        fi
        if [ "$1" = "$MOVE_PID" ] && [ "$INTERRUPTED" -ne 0 ] && [ "$CLEANING" -eq 0 ]; then
            kill -TERM "$1" 2>/dev/null || :
        fi
        # Most native tools finish in milliseconds. Avoid adding a full poll
        # interval to every stat/print while retaining the slow-step deadline.
        if [ "$bound_fast" -lt 5 ]; then
            sleep 0.001
            bound_fast=$((bound_fast + 1))
        else
            sleep 0.01
            bound_ticks=$((bound_ticks + 1))
        fi
    done
    wait "$1"
    bound_status=$?
    if [ "$bound_timeout" -eq 1 ]; then
        if [ "${5:-report}" != silent ]; then
            (trap '' HUP INT TERM QUIT; command printf 'WARNING: installer step timed out: %s (process %s).\n' "${4:-child}" "$1" >&2) &
            warning_pid=$!
            wait_for_child "$warning_pid" 100 independent warning silent 2>/dev/null || :
        fi
        return 124
    fi
    return "$bound_status"
}

# Share a twenty-second work budget across all cleanup/committed steps,
# including capturing subshells. Reserve bounded lock release/reporting after it.
start_work_clock() {
    [ -z "$WORK_CLOCK" ] || return 0
    (trap '' HUP INT TERM QUIT; exec sleep 20) &
    WORK_CLOCK=$!
}
stop_work_clock() {
    [ -n "$WORK_CLOCK" ] || return 0
    kill -KILL "$WORK_CLOCK" 2>/dev/null || :
    wait_for_child "$WORK_CLOCK" 100 independent 2>/dev/null || :
    WORK_CLOCK=""
}

# Long steps before the swap (copy, signature, library checks): forward a
# caught signal to the child, and report the interruption rather than the
# step's own failure.
run_interruptible() {
    step_capture=""
    if [ "$1" = --capture-output ]; then step_capture="$2"; shift 2; fi
    (
        trap - HUP INT TERM QUIT
        if [ -n "$step_capture" ]; then exec "$@" > "$step_capture" 2>&1; fi
        exec "$@"
    ) &
    step_pid=$!
    while kill -0 "$step_pid" 2>/dev/null; do
        if [ "$INTERRUPTED" -ne 0 ] && [ "$CLEANING" -eq 0 ]; then
            kill -TERM "$step_pid" 2>/dev/null || :
            # No deadline on an ordinary large copy: only cancellation gets
            # a five-second grace period, then KILL and a reap.
            cancel_ticks=0
            while kill -0 "$step_pid" 2>/dev/null && [ "$cancel_ticks" -lt 50 ]; do
                sleep 0.1
                cancel_ticks=$((cancel_ticks + 1))
            done
            kill -KILL "$step_pid" 2>/dev/null || :
            break
        fi
        sleep 0.1
    done
    wait "$step_pid"
    step_status=$?
    check_interrupted
    return "$step_status"
}

# A cancelled watchdog must never hold the lock indefinitely. Give it one
# second to stop its timer, then KILL it and reap. Polls may end early on signals.
reap_cancelled_watchdog() {
    wait_for_child "$1" 100 independent
}

# Record and reap housekeeping before inspecting its effects.
run_housekeeping() {
    (trap '' HUP INT TERM QUIT; exec "$@") &
    housekeeping_pid=$!
    wait_for_child "$housekeeping_pid" 100 budgeted "$*"
}

release_lock() {
    [ "$LOCK_HELD" -eq 1 ] || return 0
    for release_attempt in 1 2 3; do
        [ -e "$LOCK_PATH" ] || [ -L "$LOCK_PATH" ] || break
        if ! same_object "$LOCK_PATH" "$LOCK_ID"; then
            printf 'WARNING: installer lock was replaced; leaving it at: %s\n' "$LOCK_PATH" >&2
            break
        fi
        run_housekeeping rm -f "$LOCK_PATH/pid"
        run_housekeeping rmdir "$LOCK_PATH" 2>/dev/null || :
    done
    if same_object "$LOCK_PATH" "$LOCK_ID"; then
        printf 'WARNING: could not release installer lock: %s\n' "$LOCK_PATH" >&2
    fi
    LOCK_HELD=0
    LOCK_ID=""
}
lock_busy() {
    owner="unreadable or missing"
    # Never open a FIFO/device/socket (or a symlink to one) for the diagnostic.
    if [ -f "$LOCK_PATH/pid" ] && [ ! -L "$LOCK_PATH/pid" ]; then
        owner=$(protected_output cat "$LOCK_PATH/pid" 2>/dev/null) || owner="unreadable or missing"
    fi
    made=$(protected_output stat -f '%Sm' "$LOCK_PATH" 2>/dev/null) || made="unknown"
    # Single-quoted for the shell the user pastes it into.
    quoted=$(printf '%s' "$LOCK_PATH" | sed "s/'/'\\\\''/g")
    printf 'Not installed: another installation seems to be running. Nothing was changed.\n' >&2
    printf 'Its lock: %s\n' "$LOCK_PATH" >&2
    printf 'Made: %s, by process %s.\n' "$made" "$owner" >&2
    printf 'If an install was cut off (a crash or power loss) and no installer window is open,\n' >&2
    printf 'look inside a lock with unexpected contents before removing it.\n' >&2
    printf 'Remove the lock with this command, then run the installer again:\n' >&2
    if [ -L "$LOCK_PATH" ] || [ ! -d "$LOCK_PATH" ]; then
        printf "  rm -f '%s'\n" "$quoted" >&2
    else
        printf "  rm -f '%s/pid' && rmdir '%s'\n" "$quoted" "$quoted" >&2
    fi
    EXIT_STATUS=4
    exit 4
}

# The parent must be writable before attempting the exclusion primitive.
acquire_lock() {
    [ -w "${LOCK_PATH%/*}" ] || fail "${LOCK_PATH%/*} cannot be written." "Nothing has been changed."
    check_interrupted
    if ! run_housekeeping mkdir "$LOCK_PATH" 2>/dev/null; then
        if [ -e "$LOCK_PATH" ] || [ -L "$LOCK_PATH" ]; then lock_busy; fi
        fail "Could not create the installer lock: $LOCK_PATH"
    fi
    LOCK_HELD=1
    for lock_attempt in 1 2 3; do
        LOCK_ID=$(object_id "$LOCK_PATH") && break
    done
    [ -n "$LOCK_ID" ] || fail "Could not identify the installer lock."
    printf '%s\n' "$$" > "$LOCK_PATH/pid" || fail "Could not record the installer lock owner."
    check_interrupted
}

check_interrupted() {
    if [ "$INTERRUPTED" -ne 0 ] && [ "$CLEANING" -eq 0 ] && [ "$COMMITTED" -eq 0 ]; then
        EXIT_STATUS=1
        exit 1
    fi
}

bundle_identifier() {
    /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist" 2>/dev/null
}

remove_owned() {
    [ -n "$1" ] || return 0
    for removal_attempt in 1 2 3; do
        [ -e "$1" ] || [ -L "$1" ] || return 0
        if ! same_object "$1" "$2"; then
            printf 'WARNING: leaving foreign staging/backup occupant: %s\n' "$1" >&2
            return 1
        fi
        run_housekeeping rm -rf "$1"
    done
    if [ -e "$1" ] || [ -L "$1" ]; then
        printf 'WARNING: could not remove staging/backup object: %s\n' "$1" >&2
        return 1
    fi
    return 0
}

# Forward moves obey the flag; cleanup moves ignore catchable signals.
# wait can return early on a signal; reap the move before reconciling objects.
bounded_move() {
    same_device "$1" "$2" || return 1
    move_identity=$(object_id "$1") || return 1
    (
        if [ "$CLEANING" -eq 1 ]; then
            trap '' HUP INT TERM QUIT
            trap - PIPE
        else
            trap - HUP INT TERM QUIT PIPE
        fi
        if [ ! -d "$1" ] || [ -L "$1" ]; then exec ln -P -- "$1" "$2" </dev/null; fi
        exec mv -n "$1" "$2" </dev/null
    ) &
    MOVE_PID=$!
    if [ "$INTERRUPTED" -ne 0 ] && [ "$CLEANING" -eq 0 ]; then kill -TERM "$MOVE_PID" 2>/dev/null || :; fi
    (
        # Ignore group signals in the watchdog AND its timer. USR1 is our
        # private cancellation, sent only after the move has been reaped.
        trap '' HUP INT TERM QUIT
        timer_cancelled=0
        trap 'timer_cancelled=1' USR1
        sleep 5 &
        timer_pid=$!
        while [ "$timer_cancelled" -eq 0 ] && kill -0 "$timer_pid" 2>/dev/null; do sleep 0.01; done
        if [ "$timer_cancelled" -eq 0 ]; then kill -KILL "$MOVE_PID" 2>/dev/null || :; fi
        kill -KILL "$timer_pid" 2>/dev/null || :
        wait_for_child "$timer_pid" 2>/dev/null || :
    ) &
    watchdog_pid=$!
    move_status=1
    # A caught signal interrupts wait before the child exits. Reap that child
    # before inspecting paths, retrying, or stopping its watchdog.
    wait_for_child "$MOVE_PID" 500 budgeted "move $*"
    move_status=$?
    kill -USR1 "$watchdog_pid" 2>/dev/null || :
    reap_cancelled_watchdog "$watchdog_pid" 2>/dev/null || :
    if same_object "$2" "$move_identity" && same_object "$1" "$move_identity"; then
        run_housekeeping rm -f "$1"
    fi
    move_identity=""
    MOVE_PID=""
    check_interrupted
    return "$move_status"
}

# Retry only a move killed by a catchable signal, including the Bash 3.2
# pending-signal fork race. Ordinary failures/timeouts retain the two attempts
# in restore_row, so a blocked restore still has a bounded deadline.
recovery_move() {
    for recovery_attempt in 1 2 3 4 5 6 7 8 9 10; do
        bounded_move "$@"
        recovery_status=$?
        case "$recovery_status" in 129|130|131|143) ;; *) return "$recovery_status" ;; esac
    done
    return "$recovery_status"
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
    for candidate in "$LIVE_PATH" "$STAGED_PATH" "$LIVE_PATH/${STAGED_PATH##*/}"; do
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
        STATE=evacuate_intent
        if same_object "$STAGED_PATH" "$NEW_ID"; then
            remove_owned "$NEW_PATH" "$NEW_ID" || return 1
            verify_move "$NEW_PATH" "$STAGED_PATH" "$NEW_ID" || return 1
        else
            [ ! -e "$STAGED_PATH" ] && [ ! -L "$STAGED_PATH" ] || return 1
            for attempt in 1 2; do
                recovery_move "$NEW_PATH" "$STAGED_PATH" || :
                verify_move "$NEW_PATH" "$STAGED_PATH" "$NEW_ID" && break
                locate_new || return 1
            done
            same_object "$STAGED_PATH" "$NEW_ID" || return 1
        fi
        STATE=evacuated
    fi
    if [ -z "$OLD_ID" ]; then
        [ ! -e "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ]
        return
    fi
    locate_old || return 1
    if [ "$OLD_PATH" = "$LIVE_PATH" ]; then
        if same_object "$BACKUP_PATH" "$OLD_ID"; then remove_owned "$BACKUP_PATH" "$OLD_ID" || :; fi
        return 0
    fi
    [ ! -e "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ] || return 1
    STATE=restore_intent
    for attempt in 1 2; do
        recovery_move "$OLD_PATH" "$LIVE_PATH" || :
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
    [ "$CLEANING" -eq 0 ] || return 0
    CLEANING=1
    # The interruption flag is already recorded. Ignore in the parent before
    # forking: Bash 3.2 may resend a pending trapped signal in a new child.
    trap '' HUP INT TERM QUIT
    start_work_clock
    status="$EXIT_STATUS"
    if [ "$INTERRUPTED" -ne 0 ] && [ "$COMMITTED" -eq 0 ]; then
        printf 'Installation interrupted.\n' >&2
    fi
    if [ "$SWAP_READY" -eq 1 ]; then
        if [ "$COMMITTED" -eq 1 ]; then
            status=0
        elif restore_row; then
            status=1
            [ -z "$OLD_ID" ] || [ "$STATE" != restored ] || printf 'Restored the previous installation.\n'
        else
            printf 'ERROR: could not restore the previous installation.\n' >&2
            if OUTPUT_BOUND=independent locate_old; then
                printf 'The previous app is at: %s\n' "$OLD_PATH" >&2
            else
                printf 'ERROR: recorded previous app could not be verified at its recovery paths.\n' >&2
                if [ -e "$BACKUP_PATH" ] || [ -L "$BACKUP_PATH" ]; then
                    printf 'The unrecognized displaced object is at: %s\n' "$BACKUP_PATH" >&2
                fi
            fi
            status=3
        fi
        remove_owned "$STAGED_PATH" "$NEW_ID"
        NEW_ID=""
    fi
    stop_work_clock
    release_lock
    close_prompt
    exit "$status"
}


APP_NAME="Waveguide Generator.app"
check_interrupted
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
        EXIT_STATUS=2
        check_interrupted
        exit 2
    fi
    UPDATE_TARGET="$2"
    DEFAULT_TARGET_DIR=""
else
    DEFAULT_TARGET_DIR="${1:-/Applications}"
fi

PROMPT_ON_FAILURE=0

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
    TARGET_DIR=$(cd -- "$TARGET_DIR" && pwd -P) || fail "Could not resolve the target parent."
    TARGET="$TARGET_DIR/$(basename -- "$TARGET")"
    LOCK_PATH="$TARGET_DIR/.$(basename -- "$TARGET").install.lock"
    acquire_lock
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
        fail "$TARGET_DIR cannot be written." \
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
        printf '%s cannot be written.\n' "$DEFAULT_TARGET_DIR"
        printf 'Installing to %s instead.\n\n' "$TARGET_DIR"
    fi
    TARGET_DIR=$(cd -- "$TARGET_DIR" && pwd -P) || fail "Could not resolve the target parent."
    TARGET="$TARGET_DIR/$APP_NAME"
    LOCK_PATH="$TARGET_DIR/.$APP_NAME.install.lock"
    acquire_lock
fi

if [ "$UPDATE" -eq 1 ] && [ "$(id -u)" = "0" ]; then
    fail "Do not run --update as root." \
         "A root-owned app would refuse every later update made by the user." \
         "Nothing has been changed."
fi

# Sweep only empty hidden debris of exactly the shapes this script creates.
# Without a journal, application-bearing directories and unexpected contents
# must stay available for manual recovery, regardless of the live target.
TARGET_BASE="$(basename -- "$TARGET")"
for stale in "$TARGET_DIR"/".$TARGET_BASE".new.* "$TARGET_DIR"/".$TARGET_BASE".previous.*; do
    [ -d "$stale" ] && [ ! -L "$stale" ] || continue
    # Without a journal, any application-bearing directory might be the only
    # copy that an earlier exit 3 told the user to keep. Sweep only empty debris.
    [ ! -d "$stale/Contents" ] || continue
    contains_app=0
    for app in "$stale"/*.app; do [ ! -d "$app" ] || contains_app=1; done
    [ "$contains_app" -eq 0 ] || continue
    run_housekeeping rmdir "$stale" 2>/dev/null || :
done

# The copy is made and verified BESIDE the final name first, on the same volume,
# and only then swapped in by rename. Nothing about the installation that
# already exists is touched until the new copy has been proven good, so a failed
# copy, a failed quarantine step or a bad signature leaves the machine with the
# version it already had.
STAGED="$TARGET_DIR/.$(basename -- "$TARGET").new.$$"
DISPLACED="$TARGET_DIR/.$(basename -- "$TARGET").previous.$$"
# A failed sweep (or a leftover symlink) must never turn mv into a nesting
# operation. Refuse occupied names before touching the current installation.
if [ -e "$STAGED" ] || [ -L "$STAGED" ] || [ -e "$DISPLACED" ] || [ -L "$DISPLACED" ]; then
    fail "A staging or backup path is still occupied." "Nothing has been changed."
fi
# Swap table (one row): LIVE_PATH=TARGET, BACKUP_PATH=DISPLACED,
# STAGED_PATH=STAGED, plus the old/new device/inode identities. States are staged ->
# prepared -> displace_intent -> displaced -> install_intent -> installed.
# Record intent BEFORE acting, then verify both source absence and destination
# identity. Cleanup reconciles actual objects even before post-move bookkeeping:
# prepared/displace_intent keeps or restores old; displaced/install_intent/
# installed returns new to staging via evacuate_intent -> evacuated, then
# restores old via restore_intent ->
# restored. COMMITTED alone retains new and retires old. Never replace a raced file;
# a failed restore exits 3 and prints the real old-object path, including nesting.
LIVE_PATH="$TARGET"
BACKUP_PATH="$DISPLACED"
STAGED_PATH="$STAGED"
OLD_ID=""
NEW_ID=""
check_interrupted
STATE=staged
COMMITTED=0

SWAP_READY=1
check_interrupted

run_housekeeping mkdir "$STAGED_PATH" || fail "Could not create the staged app."
NEW_ID=$(object_id "$STAGED_PATH") || fail "Could not identify the staged app."
check_interrupted
same_device "$STAGED_PATH" "$LIVE_PATH" || fail "The staging and destination must be on the same device."
check_interrupted
printf 'Copying to %s ...\n' "$TARGET_DIR"
if ! run_interruptible ditto "$SOURCE" "$STAGED"; then
    fail "Could not copy the app to $TARGET_DIR."
fi

# The point of the whole exercise. Everything read out of a quarantined disk
# image inherits com.apple.quarantine, so the fresh copy carries it on every
# one of its several thousand files until this runs.
check_interrupted
printf 'Clearing the download quarantine flag ...\n'
if ! run_interruptible xattr -dr com.apple.quarantine "$STAGED"; then
    fail "Could not clear the quarantine flag from the copy."
fi

# ditto preserves the signature, so this normally passes untouched and costs a
# few seconds.
check_interrupted
printf 'Checking the app signature ...\n'
if ! run_interruptible --capture-output /dev/null codesign --verify --deep --strict "$STAGED"; then
    if [ "$UPDATE" -eq 1 ]; then
        # Never re-sign here. An ad-hoc signature carries no identity, so it
        # would add no authenticity, and a seal that fails after a verified
        # copy means a damaged copy, which re-signing would bless.
        fail "The copy of the new version does not have a valid signature." \
             "It was not installed, and the current version was left in place."
    fi
    # Interactive install: re-sign only when the seal is broken, because an
    # ad-hoc signature that no longer seals the bundle would leave the app
    # unlaunchable with no explanation.
    printf 'Re-signing the copy (this takes a moment) ...\n'
    run_interruptible --capture-output /dev/null codesign --force --deep --sign - "$STAGED" || true
    if ! run_interruptible --capture-output /dev/null codesign --verify --deep --strict "$STAGED"; then
        fail "The copy in $TARGET_DIR does not have a valid signature." \
             "macOS will refuse to start it. The previous installation was left in place."
    fi
fi

same_object "$STAGED_PATH" "$NEW_ID" || fail "The staged app is missing or replaced."
same_device "$STAGED_PATH" "$LIVE_PATH" || fail "The staging and destination must be on the same device."
OLD_ID="$(object_id "$LIVE_PATH")" || OLD_ID=""
check_interrupted
if { [ -e "$LIVE_PATH" ] || [ -L "$LIVE_PATH" ]; } && [ -z "$OLD_ID" ]; then
    fail "Could not identify the existing installation."
fi
STATE=prepared
check_interrupted
if [ -n "$OLD_ID" ]; then
    printf 'Replacing the copy already in %s ...\n' "$TARGET_DIR"
    if [ -e "$BACKUP_PATH" ] || [ -L "$BACKUP_PATH" ]; then
        fail "The backup path is still occupied." "Nothing has been changed."
    fi
    STATE=displace_intent
    check_interrupted
    bounded_move "$LIVE_PATH" "$BACKUP_PATH"
    move_status=$?
    check_interrupted
    if ! verify_move "$LIVE_PATH" "$BACKUP_PATH" "$OLD_ID" || [ "$move_status" -ne 0 ]; then
        fail "Could not move the existing installation aside." \
             "Quit Waveguide Generator if it is running, then try again."
    fi
fi
STATE=displaced
check_interrupted
[ ! -e "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ] || fail "The app destination is occupied."
STATE=install_intent
check_interrupted
bounded_move "$STAGED_PATH" "$LIVE_PATH"
move_status=$?
check_interrupted
if ! verify_move "$STAGED_PATH" "$LIVE_PATH" "$NEW_ID" || [ "$move_status" -ne 0 ]; then
    fail "Could not put the new version in place at $TARGET."
fi
STATE=installed
check_interrupted
COMMITTED=1
# Once committed, finish the success message and removal of this run's backups.
trap '' HUP INT TERM QUIT
start_work_clock
if ! remove_owned "$DISPLACED" "$OLD_ID" || [ -e "$DISPLACED" ] || [ -L "$DISPLACED" ]; then
    printf 'WARNING: installed successfully, but could not fully remove the previous copy.\n' >&2
    printf 'The leftover backup is at: %s\n' "$DISPLACED" >&2
fi
OLD_ID=""

printf '\n'
printf 'Installed: %s\n' "$TARGET"
printf '\n'
if [ "$UPDATE" -eq 1 ]; then
    check_interrupted
    exit 0
fi
printf 'You can eject the Waveguide Generator disk image now.\n'
# Only the double-click path starts the app. Someone who named a destination
# asked to install it, not to run it, and the tests rely on that.
if [ "$#" -eq 0 ]; then
    printf 'Starting Waveguide Generator ...\n'
    run_housekeeping open "$TARGET" || printf 'Could not start it automatically; open it from %s.\n' "$TARGET_DIR"
fi
check_interrupted
exit 0
