#!/bin/bash
# Bash, compatible with Bash 3.2; this is not a POSIX /bin/sh script.
# Install Waveguide Generator from the release tarball. This file ships INSIDE
# Waveguide.Generator-<version>-linux-x86_64.tar.gz, beside the application
# folder; it is not the source installer, which is installers/linux/install.sh
# in the checkout and builds a .venv from a Git clone.
#
# WHY THE INSTALL IS PER-USER.
#
# Waveguide Generator updates itself by replacing `app` and `runtime` inside
# its own installation directory, and the updater runs as the user with no way
# to elevate (launchers/apply_update.py). A root-owned copy under /opt or
# /usr/local would therefore install once and then refuse every update it was
# offered, which is the failure the Windows installer avoids by writing to
# %LOCALAPPDATA%\Programs rather than Program Files. Linux gets the same
# answer for the same reason: ~/.local, no root, no package manager.
#
# It must stay self-contained. It runs from wherever the user extracted the
# tarball, with nothing from the checkout beside it, and its only dependencies
# are bash and coreutils.

# Exit status:
#   0  installed, or --help displayed
#   1  failed or interrupted; this run kept/restored the previous objects,
#      or displaced nothing. A missing target on entry remains missing.
#   3  ROLLBACK INCOMPLETE: recovery paths for preserved objects are printed;
#      a noncooperating replacement can also make the recorded old object missing.
#   4  existing installer lock; the refused run changes nothing on disk.
#
# Minimal lock: mkdir beside the resolved target, held through cleanup. Existing
# locks are NEVER reclaimed automatically, even after SIGKILL or power loss.
# After checking no installer is running, remove the exact lock path printed by
# the refused run using its command; inspect unexpected contents first.
# This is per-target exclusion; Linux integration paths are shared across prefixes.
# The updater helper owns staleness policy (UPDATER-PLAN.md section 9).
# Statuses verify recorded object identities, not external edits to their
# contents. Recovery assumes native same-device rename/link operations.
# Known limit: there is no journal. SIGKILL/power loss between the two renames
# can leave the target absent; a rerun does not discover/restore the backup.
# Look beside the resolved target for .waveguide-generator.previous.*; desktop
# integration backups remain beside their destinations as .waveguide-generator.*.backup.*.
# A hard kill can also leave a half-copied .waveguide-generator.install.*
# staging folder beside the target; after checking no installer is running,
# that staging folder is safe to delete.
# Cleanup finishes within 300 seconds unless the kernel itself blocks a kill
# during rollback or after commit. Work has a shared
# 20-second budget once CLEANING=1 or COMMITTED=1; metadata/housekeeping
# steps and complete messages have three-second elapsed-time deadlines, moves
# a five-second watchdog, then KILL and reap. Lock release and recovery reporting
# use independent bounded steps. Forward metadata/housekeeping/messages/moves
# allow 30 seconds per step, then report an ordinary failure; large copies and
# checks remain unbounded until cancellation. A timed-out restore is status 3.
# Journal recovery and fuller sweep/lock rules await the updater handoff stage.

# A signal before the traps ends a run that has done nothing; the flag never
# comes from the environment.
INTERRUPTED=0
# Record the first cancellation, then protect subsequent forks immediately.
# Bash 3.2 can deliver a repeated signal before a child installs its own trap.
trap 'INTERRUPTED=1; trap "" HUP INT TERM QUIT' HUP INT TERM QUIT
trap cleanup EXIT
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
OUTPUT_CAPTURE=0
PREFLIGHT_CAPTURE=""
PREFLIGHT_CAPTURE_ID=""
STAGE_ROOT=""
STAGE_ROOT_ID=""
set -u
# Bash 3.2 can longjmp out of wait while its diagnostic holds stderr's
# FILE lock. A later capture inherits that lock and blocks before its trap.
# Handle notifications outside wait, after the child is dead, before collecting
# its cached status. dash has no such Bash wait/stdio path.
wait() {
    if [ -n "${BASH_VERSION:-}" ]; then
        # jobs only reports cached states. A deferred SIGCHLD can leave a
        # zombie visible to kill -0 forever. A foreground no-op makes Bash
        # reap pending children outside the interruptible wait builtin.
        reap_polls=0
        while kill -0 "$1" 2>/dev/null; do
            jobs >/dev/null
            reap_polls=$((reap_polls + 1))
            if [ "$reap_polls" -ge 32 ]; then
                (trap - 0; :)
                reap_polls=0
            fi
        done
        jobs >/dev/null
    fi
    command wait "$@"
    return "$?"
}

# Render once to a private file. A killed renderer can safely be retried;
# only emission is retried after rendering. cat emits each short record at once.
# A KILL during emission can race its completed write: parsers must tolerate a
# repeated line. Catchable cleanup signals cannot interrupt the emitter.
printf() {
    print_seconds=30
    if [ "$CLEANING" -eq 1 ] || [ "$COMMITTED" -eq 1 ]; then print_seconds=3; fi
    (trap '' HUP INT TERM QUIT; exec sleep "$print_seconds") >/dev/null 2>&1 &
    PRINT_CLOCK=$!
    print_file=$(OUTPUT_BOUND=message protected_output mktemp "${TMPDIR:-/tmp}/wg-installer-message.XXXXXX") || {
        kill -KILL "$PRINT_CLOCK" 2>/dev/null || :
        wait "$PRINT_CLOCK" 2>/dev/null || :
        PRINT_CLOCK=""
        return 1
    }
    for print_attempt in 1 2 3 4 5 6 7 8 9 10; do
        (command printf "$@") > "$print_file" &
        render_pid=$!
        wait_for_child "$render_pid" 3 message
        print_status=$?
        [ "$print_status" -gt 128 ] || break
    done
    if [ "$print_status" -eq 0 ]; then
        for print_attempt in 1 2 3 4 5 6 7 8 9 10; do
            (trap '' HUP INT TERM QUIT; exec cat "$print_file") &
            print_pid=$!
            wait_for_child "$print_pid" 3 message
            print_status=$?
            [ "$print_status" -gt 128 ] || break
        done
    fi
    (trap '' HUP INT TERM QUIT; exec rm -f "$print_file") &
    print_remove_pid=$!
    wait_for_child "$print_remove_pid" 3 message "remove message $print_file" || :
    kill -KILL "$PRINT_CLOCK" 2>/dev/null || :
    wait "$PRINT_CLOCK" 2>/dev/null || :
    PRINT_CLOCK=""
    return "$print_status"
}

usage() {
    cat <<'USAGE'
Usage: ./install.sh [--prefix DIR] [--no-launch] [--skip-checks] [--update]

  --prefix DIR   install into DIR/waveguide-generator
                 (default: $XDG_DATA_HOME, or ~/.local/share)
  --no-launch    install without starting the application afterwards
  --skip-checks  install even if the system-library check below fails
  --update       replace an existing installation in place, without starting it;
                 fails if DIR/waveguide-generator is not already installed
USAGE
}

fail() {
    printf '\n'
    printf '===============================================================\n'
    printf '%s\n' "$@"
    printf '===============================================================\n'
    printf '\n'
    exit 1
}

# Identity is (device, inode), valid only while the recorded object survives.
# stat does not follow symlinks, including broken command links.
# Always called in a command substitution: ignore group signals at once, and
# callers retry a lookup a signal still managed to kill (status above 128).
object_id() {
    trap '' HUP INT TERM QUIT
    [ -e "$1" ] || [ -L "$1" ] || return 1
    if [ "$STAT_STYLE" = gnu ]; then
        protected_output stat -c '%d:%i' -- "$1" 2>/dev/null
    else
        protected_output stat -f '%d:%i' "$1" 2>/dev/null
    fi
}

same_object() {
    [ -n "$2" ] || return 1
    for same_attempt in 1 2 3; do
        current_id=$(object_id "$1")
        same_status=$?
        [ "$same_status" -gt 128 ] && continue
        # Explicit statuses: bash 5 makes a bare return in the EXIT trap
        # report the status from before the trap, not this test's.
        if [ "$same_status" -eq 0 ] && [ "$current_id" = "$2" ]; then return 0; fi
        return 1
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
# resources. Short deadlines apply only during cleanup or after commit.
protected_output() {
    OUTPUT_CAPTURE=1
    trap '' HUP INT TERM QUIT
    (exec "$@") &
    output_pid=$!
    wait_for_child "$output_pid" 3 "${OUTPUT_BOUND:-budgeted}" "$*"
}

# Elapsed-time clocks, never poll counts: deadline, KILL, reap, then report.
# Only kernel-blocked KILL can prevent reaping. A cancelled watchdog is bounded
# even before cleanup; ordinary forward steps get a generous thirty seconds.
wait_for_child() {
    bound_fast=0
    bound_seconds=${2:-3}
    if [ "$CLEANING" -eq 0 ] && [ "$COMMITTED" -eq 0 ] && [ "${3:-budgeted}" != watchdog ]; then bound_seconds=30; fi
    bound_clock="$PRINT_CLOCK"
    # A captured waiter cannot reap its parent's message clock.
    if [ "${3:-budgeted}" != message ] || [ "${OUTPUT_CAPTURE:-0}" -eq 1 ]; then
        (trap '' HUP INT TERM QUIT; exec sleep "$bound_seconds") >/dev/null 2>&1 &
        bound_clock=$!
    fi
    bound_timeout=0
    while kill -0 "$1" 2>/dev/null; do
        if ! kill -0 "$bound_clock" 2>/dev/null ||
           { [ "${3:-budgeted}" = budgeted ] && [ -n "$WORK_CLOCK" ] && ! kill -0 "$WORK_CLOCK" 2>/dev/null; }; then
            bound_timeout=1
            kill -KILL "$1" 2>/dev/null || :
            break
        fi
        if [ "$1" = "$MOVE_PID" ] && [ "$INTERRUPTED" -ne 0 ] && [ "$CLEANING" -eq 0 ]; then
            kill -TERM "$1" 2>/dev/null || :
            if [ "$bound_seconds" -eq 30 ]; then
                kill -KILL "$bound_clock" 2>/dev/null || :
                wait "$bound_clock" 2>/dev/null || :
                bound_seconds=5
                (trap '' HUP INT TERM QUIT; exec sleep "$bound_seconds") >/dev/null 2>&1 &
                bound_clock=$!
            fi
        fi
        # Most native tools finish in milliseconds. Avoid adding a full poll
        # interval to every stat/print while retaining the slow-step deadline.
        if [ "$bound_fast" -lt 5 ]; then
            sleep 0.001
            bound_fast=$((bound_fast + 1))
        else
            sleep 0.01
        fi
    done
    wait "$1"
    bound_status=$?
    if [ "${3:-budgeted}" != message ] || [ "${OUTPUT_CAPTURE:-0}" -eq 1 ]; then
        kill -KILL "$bound_clock" 2>/dev/null || :
        wait "$bound_clock" 2>/dev/null || :
    fi
    if [ "$bound_timeout" -eq 1 ]; then
        if [ "${5:-report}" != silent ]; then
            (trap '' HUP INT TERM QUIT; command printf 'WARNING: installer step timed out: %s (process %s).\n' "${4:-child}" "$1" >&2) &
            warning_pid=$!
            wait_for_child "$warning_pid" 3 independent warning silent 2>/dev/null || :
        fi
        return 124
    fi
    return "$bound_status"
}

# Share a twenty-second work budget across all cleanup/committed steps,
# including capturing subshells. Reserve bounded lock release/reporting after it.
start_work_clock() {
    [ -z "$WORK_CLOCK" ] || return 0
    (trap '' HUP INT TERM QUIT; exec sleep 20) >/dev/null 2>&1 &
    WORK_CLOCK=$!
}
stop_work_clock() {
    [ -n "$WORK_CLOCK" ] || return 0
    kill -KILL "$WORK_CLOCK" 2>/dev/null || :
    wait_for_child "$WORK_CLOCK" 3 independent 2>/dev/null || :
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
            # a five-second grace period, then KILL and a reap. Five seconds
            # of elapsed time from a clock child, not a count of polls: each
            # poll forks a sleep, and a loaded machine makes those slower
            # (fifty polls took well over five seconds on a hosted runner).
            (trap '' HUP INT TERM QUIT; exec sleep 5) >/dev/null 2>&1 &
            grace_clock=$!
            while kill -0 "$step_pid" 2>/dev/null && kill -0 "$grace_clock" 2>/dev/null; do
                sleep 0.1
            done
            kill -KILL "$step_pid" 2>/dev/null || :
            kill -KILL "$grace_clock" 2>/dev/null || :
            wait "$grace_clock" 2>/dev/null || :
            break
        fi
        sleep 0.1
    done
    wait "$step_pid"
    step_status=$?
    check_interrupted
    return "$step_status"
}

# A cancelled watchdog must never hold the lock indefinitely. Give it three
# elapsed seconds to stop its timer, then KILL it and reap.
reap_cancelled_watchdog() {
    wait_for_child "$1" 3 watchdog
}

# Record and reap housekeeping before inspecting its effects.
run_housekeeping() {
    (trap '' HUP INT TERM QUIT; exec "$@") &
    housekeeping_pid=$!
    wait_for_child "$housekeeping_pid" 3 budgeted "$*"
}

# Optional post-commit refreshes have default signal dispositions and three seconds.
# Their failure cannot change the committed status.
run_optional() {
    # Bash forces INT/QUIT ignored for asynchronous commands. The installed
    # runtime restores defaults before exec, keeping this PID in our group.
    (exec "$TARGET/runtime/bin/python3.13" -c '
import os, signal, sys
for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT, signal.SIGPIPE):
    signal.signal(sig, signal.SIG_DFL)
os.execvp(sys.argv[1], sys.argv[1:])
' "$@") &
    optional_pid=$!
    wait_for_child "$optional_pid" 3 budgeted "$*" || :
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
    made=$(protected_output stat -c '%y' "$LOCK_PATH" 2>/dev/null || protected_output stat -f '%Sm' "$LOCK_PATH" 2>/dev/null) || made="unknown"
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

canonical_path() {
    local input="$1" part resolved candidate
    local -a components
    if realpath -m -- "$input" 2>/dev/null; then
        return 0
    fi
    # BSD realpath has no -m; this branch keeps cross-platform fixture tests
    # useful. Resolve each existing component before applying the next one so
    # link/../target follows filesystem semantics rather than lexical cleanup.
    IFS='/' read -r -a components <<< "$input"
    resolved="/"
    for part in "${components[@]}"; do
        case "$part" in
            ''|.) ;;
            ..)
                [ "$resolved" = "/" ] || resolved="${resolved%/*}"
                [ -n "$resolved" ] || resolved="/"
                ;;
            *)
                if [ "$resolved" = "/" ]; then candidate="/$part"; else candidate="$resolved/$part"; fi
                if [ -e "$candidate" ] || [ -L "$candidate" ]; then
                    resolved="$(realpath -- "$candidate")" || return 1
                else
                    resolved="$candidate"
                fi
                ;;
        esac
    done
    printf '%s\n' "$resolved"
}

# Exec is not shell syntax. The desktop-entry specification first decodes the
# generic string escapes and then its own quoting, so a literal backslash needs
# four backslashes in a quoted argument. Quotes, dollar signs and backticks
# need two.
desktop_exec_escape() {
    local value="$1" output="" character
    while [ -n "$value" ]; do
        character="${value:0:1}"
        value="${value:1}"
        case "$character" in
            '\') output+='\\\\' ;;
            '"') output+='\\"' ;;
            '$') output+='\\$' ;;
            '`') output+='\\`' ;;
            *) output+="$character" ;;
        esac
    done
    printf '%s' "$output"
}

remove_owned() {
    [ -n "$1" ] || return 0
    for removal_attempt in 1 2 3; do
        [ -e "$1" ] || [ -L "$1" ] || return 0
        if ! same_object "$1" "$2"; then
            printf 'WARNING: leaving foreign staging/backup occupant: %s\n' "$1" >&2
            return 1
        fi
        run_housekeeping rm -rf -- "$1"
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
        if [ ! -d "$1" ] || [ -L "$1" ]; then
            # link(2) refuses an existing file atomically, preserving the
            # recorded identity. -P links a symlink itself, not its referent.
            exec ln -P -- "$1" "$2" </dev/null
        fi
        exec mv "${MV_OPTIONS[@]}" "$1" "$2" </dev/null
    ) &
    MOVE_PID=$!
    if [ "$INTERRUPTED" -ne 0 ] && [ "$CLEANING" -eq 0 ]; then kill -TERM "$MOVE_PID" 2>/dev/null || :; fi
    # Own the timer in the installer, so killing a stuck watchdog cannot
    # orphan its clock. Protected phases already ignore group cancellation.
    if [ "$CLEANING" -eq 1 ] || [ "$COMMITTED" -eq 1 ]; then
        sleep 5 >/dev/null 2>&1 &
    else
        sleep 30 >/dev/null 2>&1 &
    fi
    move_clock_pid=$!
    # Cancellation can arrive before the watchdog installs its USR1 handler.
    # Inherit ignored USR1 across that fork; a completed move also stops its timer.
    trap '' USR1
    (
        # Ignore group signals in the watchdog. USR1 is our
        # private cancellation, sent only after the move has been reaped.
        trap '' HUP INT TERM QUIT
        timer_cancelled=0
        trap 'timer_cancelled=1' USR1
        timer_pid=$move_clock_pid
        while [ "$timer_cancelled" -eq 0 ] && kill -0 "$timer_pid" 2>/dev/null && kill -0 "$MOVE_PID" 2>/dev/null; do sleep 0.01; done
        if [ "$timer_cancelled" -eq 0 ]; then kill -KILL "$MOVE_PID" 2>/dev/null || :; fi
        kill -KILL "$timer_pid" 2>/dev/null || :
    ) &
    watchdog_pid=$!
    trap - USR1
    move_status=1
    # A caught signal interrupts wait before the child exits. Reap that child
    # before inspecting paths, retrying, or stopping its watchdog.
    wait_for_child "$MOVE_PID" 5 budgeted "move $*"
    move_status=$?
    kill -USR1 "$watchdog_pid" 2>/dev/null || :
    reap_cancelled_watchdog "$watchdog_pid" 2>/dev/null || :
    kill -KILL "$move_clock_pid" 2>/dev/null || :
    wait "$move_clock_pid" 2>/dev/null || :
    # A file transfer has two temporary aliases; unlink only after verifying
    # the destination. The identity stays live at the destination after unlink.
    if same_object "$2" "$move_identity" && same_object "$1" "$move_identity"; then
        run_housekeeping rm -f -- "$1"
    fi
    move_identity=""
    MOVE_PID=""
    check_interrupted
    return "$move_status"
}

# Locate only the recorded object, never an unrelated occupant. BSD mv can nest
# at either end; these are the only locations a single rename can produce.
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
    local candidate
    OLD_PATH=""
    for candidate in "${LIVE[i]}" "${BACKUP[i]}" \
                     "${BACKUP[i]}/${LIVE[i]##*/}" "${LIVE[i]}/${BACKUP[i]##*/}"; do
        if same_object "$candidate" "${OLD_ID[i]}"; then OLD_PATH="$candidate"; return 0; fi
    done
    return 1
}

locate_new() {
    local candidate
    NEW_PATH=""
    for candidate in "${LIVE[i]}" "${STAGED[i]}" "${LIVE[i]}/${STAGED[i]##*/}"; do
        if same_object "$candidate" "${NEW_ID[i]}"; then NEW_PATH="$candidate"; return 0; fi
    done
    return 1
}

verify_move() {
    ! { [ -e "$1" ] || [ -L "$1" ]; } && same_object "$2" "$3"
}

restore_row() {
    local attempt
    [ "${STATE[i]}" != staged ] || return 0
    # Put our new object back beside the target, rather than recursively deleting
    # a possibly foreign destination. A failed evacuation preserves old's backup.
    if locate_new && [ "$NEW_PATH" != "${STAGED[i]}" ]; then
        STATE[i]=evacuate_intent
        # A signal/unlink error can leave both names of our new file.
        # Keep staging as the anchor and remove only the verified live alias.
        if same_object "${STAGED[i]}" "${NEW_ID[i]}"; then
            remove_owned "$NEW_PATH" "${NEW_ID[i]}" || return 1
            verify_move "$NEW_PATH" "${STAGED[i]}" "${NEW_ID[i]}" || return 1
        else
            if [ -e "${STAGED[i]}" ] || [ -L "${STAGED[i]}" ]; then return 1; fi
            for attempt in 1 2; do
                recovery_move "$NEW_PATH" "${STAGED[i]}" || :
                verify_move "$NEW_PATH" "${STAGED[i]}" "${NEW_ID[i]}" && break
                locate_new || return 1
            done
            same_object "${STAGED[i]}" "${NEW_ID[i]}" || return 1
        fi
        STATE[i]=evacuated
    fi
    if [ -z "${OLD_ID[i]}" ]; then
        if [ ! -e "${LIVE[i]}" ] && [ ! -L "${LIVE[i]}" ]; then return 0; fi
        return 1
    fi
    locate_old || return 1
    if [ "$OLD_PATH" = "${LIVE[i]}" ]; then
        # A failed unlink after displacement can leave an old backup alias.
        if same_object "${BACKUP[i]}" "${OLD_ID[i]}"; then remove_owned "${BACKUP[i]}" "${OLD_ID[i]}" || :; fi
        return 0
    fi
    [ ! -e "${LIVE[i]}" ] && [ ! -L "${LIVE[i]}" ] || return 1
    STATE[i]=restore_intent
    for attempt in 1 2; do
        recovery_move "$OLD_PATH" "${LIVE[i]}" || :
        if verify_move "$OLD_PATH" "${LIVE[i]}" "${OLD_ID[i]}"; then
            STATE[i]=restored
            return 0
        fi
        locate_old || return 1
        # A failed postcondition (including nesting) is not retried into a racer.
        [ ! -e "${LIVE[i]}" ] && [ ! -L "${LIVE[i]}" ] || return 1
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
    local status="$EXIT_STATUS" i
    if [ "$INTERRUPTED" -ne 0 ] && [ "$COMMITTED" -eq 0 ]; then
        printf 'Installation interrupted.\n' >&2
    fi
    if [ "$SWAP_READY" -eq 1 ]; then
        RESTORE_FAILED=0
        HAD_PREVIOUS=0
        if [ "$COMMITTED" -ne 1 ]; then
            status=1
            for ((i=0; i<${#LIVE[@]}; i++)); do
                [ -z "${OLD_ID[i]}" ] || HAD_PREVIOUS=1
                if ! restore_row; then
                    printf 'ERROR: could not restore the previous %s.\n' "${DESCRIPTION[i]}" >&2
                    if OUTPUT_BOUND=independent locate_old; then
                        printf 'Its backup remains at: %s\n' "$OLD_PATH" >&2
                    else
                        printf 'ERROR: recorded previous object could not be verified at its recovery paths.\n' >&2
                        if [ -e "${BACKUP[i]}" ] || [ -L "${BACKUP[i]}" ]; then
                            printf 'The unrecognized displaced object is at: %s\n' "${BACKUP[i]}" >&2
                        fi
                    fi
                    RESTORE_FAILED=1
                fi
            done
            if [ "$RESTORE_FAILED" -ne 0 ]; then
                printf 'ERROR: rollback was incomplete; the backup paths above were preserved.\n' >&2
                status=3
            elif [ "$HAD_PREVIOUS" -eq 1 ]; then
                printf 'Restored the previous installation and desktop integration.\n'
            fi
        else
            status=0
        fi
        for ((i=0; i<${#STAGED[@]}; i++)); do
            remove_owned "${STAGED[i]}" "${NEW_ID[i]}"
            # No later lookup may treat a deleted inode as historical identity.
            NEW_ID[i]=""
        done
        if [ -n "$STAGE_ROOT" ] && { [ -e "$STAGE_ROOT" ] || [ -L "$STAGE_ROOT" ]; }; then
            if same_object "$STAGE_ROOT" "$STAGE_ROOT_ID"; then
                run_housekeeping rmdir "$STAGE_ROOT" 2>/dev/null || printf 'WARNING: leaving occupied staging directory: %s\n' "$STAGE_ROOT" >&2
            else
                printf 'WARNING: leaving foreign staging occupant: %s\n' "$STAGE_ROOT" >&2
            fi
        fi
        STAGE_ROOT_ID=""
        for ((i=0; i<${#BACKUP[@]}; i++)); do
            if same_object "${BACKUP[i]}" "${RESERVATION_ID[i]}"; then
                run_housekeeping rmdir "${BACKUP[i]}" 2>/dev/null || :
            fi
        done
    fi
    remove_owned "$PREFLIGHT_CAPTURE" "$PREFLIGHT_CAPTURE_ID"
    stop_work_clock
    release_lock
    exit "$status"
}


BUNDLE_DIRECTORY="waveguide-generator"
LAUNCHER_NAME="waveguide-generator"
DESKTOP_ENTRY_NAME="waveguide-generator.desktop"
ICON_NAME="waveguide-generator.png"
ICON_SIZE="512x512"
UNINSTALLER_NAME="uninstall.sh"
DESKTOP_OWNER_NAME=".waveguide-generator.owner"
ICON_OWNER_NAME=".waveguide-generator.owner"

check_interrupted
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
SOURCE="$HERE/$BUNDLE_DIRECTORY"

HOME_DIRECTORY="${HOME:-}"
DATA_HOME="${XDG_DATA_HOME:-$HOME_DIRECTORY/.local/share}"
PREFIX="$DATA_HOME"
LAUNCH=1
PREFLIGHT=1
UPDATE=0

STAT_STYLE=gnu
(protected_output stat -c '%d:%i' -- /) >/dev/null 2>&1 || STAT_STYLE=bsd

while [ "$#" -gt 0 ]; do
    check_interrupted
    case "$1" in
        --prefix)
            [ "$#" -ge 2 ] || fail "--prefix needs a directory."
            PREFIX="$2"
            shift 2
            ;;
        --prefix=*)
            PREFIX="${1#--prefix=}"
            shift
            ;;
        --no-launch)
            LAUNCH=0
            shift
            ;;
        --skip-checks)
            PREFLIGHT=0
            shift
            ;;
        --update)
            UPDATE=1
            LAUNCH=0
            shift
            ;;
        -h|--help)
            check_interrupted
            usage
            EXIT_STATUS=0
            check_interrupted
            exit 0
            ;;
        *)
            usage >&2
            fail "Unknown option: $1"
            ;;
    esac
done

[ -n "$HOME_DIRECTORY" ] || fail "HOME is not set." "Nothing has been installed."
case "$HOME_DIRECTORY" in /*) ;; *) fail "HOME must be an absolute path: $HOME_DIRECTORY" "Nothing has been installed." ;; esac
case "$DATA_HOME" in /*) ;; *) fail "XDG_DATA_HOME must be an absolute path: $DATA_HOME" "Nothing has been installed." ;; esac
case "$PREFIX" in /*) ;; *) fail "--prefix must be an absolute path: $PREFIX" "Nothing has been installed." ;; esac
RESOLVED="$(canonical_path "$HOME_DIRECTORY")" || fail "Could not resolve HOME: $HOME_DIRECTORY"
HOME_DIRECTORY="$RESOLVED"
RESOLVED="$(canonical_path "$DATA_HOME")" || fail "Could not resolve XDG_DATA_HOME: $DATA_HOME"
DATA_HOME="$RESOLVED"
RESOLVED="$(canonical_path "$PREFIX")" || fail "Could not resolve --prefix: $PREFIX"
PREFIX="$RESOLVED"

case "$PREFIX$DATA_HOME$HOME_DIRECTORY" in
    *$'\n'*|*$'\r'*)
        fail "Installation paths cannot contain a newline." "Nothing has been installed."
        ;;
esac

if [ "$(id -u)" = "0" ]; then
    fail "Do not install Waveguide Generator as root." \
         "" \
         "It replaces files inside its own installation when it updates, and it" \
         "cannot ask for a password to do that. A root-owned copy would install" \
         "once and then refuse every update. Run this as your normal user; it" \
         "installs under your home directory and needs no privileges."
fi

if [ ! -d "$SOURCE" ]; then
    fail "\"$BUNDLE_DIRECTORY\" is not beside this installer." \
         "Looked in: $HERE" \
         "" \
         "Extract the whole tarball and run install.sh from inside the" \
         "extracted folder, without moving it somewhere else first."
fi

# A folder with the right name is not evidence of the right contents, and
# everything below this point copies and deletes directories.
for required in "app/APP-MANIFEST.json" "runtime/RUNTIME-MANIFEST.json" \
                "$LAUNCHER_NAME" "$DESKTOP_ENTRY_NAME" "$ICON_NAME"; do
    [ -e "$SOURCE/$required" ] || \
        fail "The application folder beside this installer is incomplete:" \
             "$SOURCE/$required is missing." \
             "" \
             "Download the release tarball again and re-extract it."
done
[ -f "$HERE/$UNINSTALLER_NAME" ] || \
    fail "The uninstaller is not beside this installer: $HERE/$UNINSTALLER_NAME" \
         "Download the release tarball again and re-extract it."
SOURCE="$(canonical_path "$SOURCE")" || fail "Could not resolve the extracted application path."

# The libraries this bundle does not bring with it.
#
# Everything else is inside: the interpreter, Tcl/Tk, and every Python package.
# gmsh is the exception -- its wheel dlopens the system OpenGL and X11 client
# libraries at import -- and gmsh is the single geometry authority in this
# application, so an install without them opens the interface and meshes
# nothing.
#
# A desktop system already has all of these; a server or container image does
# not. Measured 2026-09-04 against the pinned runtime on a bare ubuntu:24.04
# image, adding one package at a time until `import gmsh` succeeded, which is
# where the list below comes from.
#
# Checked by importing gmsh rather than by looking for package names, because
# the import is the thing that has to work, the error names the exact library,
# and the packages that provide them differ on every distribution.
if [ "$PREFLIGHT" -eq 1 ]; then
    printf 'Checking system libraries ...\n'
    PREFLIGHT_CAPTURE=$(protected_output mktemp -d "${TMPDIR:-/tmp}/waveguide-preflight.XXXXXX") || fail "Could not create library-check capture."
    PREFLIGHT_CAPTURE_ID=$(object_id "$PREFLIGHT_CAPTURE") || fail "Could not identify library-check capture."
    if ! run_interruptible --capture-output "$PREFLIGHT_CAPTURE/output" "$SOURCE/runtime/bin/python3.13" -c 'import gmsh'; then
        fail "Waveguide Generator's mesher cannot load a library this system does not have:" \
             "" \
             "$(tail -n 1 "$PREFLIGHT_CAPTURE/output")" \
             "" \
             "These are ordinary OpenGL and X11 desktop libraries. Pick the line" \
             "for your distribution; each installs every one the mesher needs." \
             "" \
             "  Ubuntu 24.04 / Debian:" \
             "  sudo apt install libglu1-mesa libgl1 libgomp1 libfontconfig1 \\" \
             "                   libxrender1 libxcursor1 libxft2 libxinerama1 \\" \
             "                   libxi6 libxext6" \
             "" \
             "  Fedora:" \
             "  sudo dnf install mesa-libGLU libglvnd-glx libgomp fontconfig \\" \
             "                   libXrender libXcursor libXft libXinerama \\" \
             "                   libXi libXext" \
             "" \
             "  Arch:" \
             "  sudo pacman -S --needed glu libglvnd gcc-libs fontconfig \\" \
             "                          libxrender libxcursor libxft libxinerama \\" \
             "                          libxi libxext" \
             "" \
             "The Fedora list is libglvnd-glx, not mesa-libGL: current Fedora" \
             "ships libGL.so.1 from libglvnd, and the obvious-looking guess does" \
             "not resolve." \
             "" \
             "Then run this installer again. Nothing has been installed yet." \
             "Use --skip-checks to install anyway."
    fi
    remove_owned "$PREFLIGHT_CAPTURE" "$PREFLIGHT_CAPTURE_ID"
    PREFLIGHT_CAPTURE=""
    PREFLIGHT_CAPTURE_ID=""
fi

TARGET="$PREFIX/$BUNDLE_DIRECTORY"
APPLICATIONS="$DATA_HOME/applications"
ICONS="$DATA_HOME/icons/hicolor/$ICON_SIZE/apps"
BIN="$HOME_DIRECTORY/.local/bin"
TARGET="$(canonical_path "$TARGET")" || fail "Could not resolve the installation path."

# The desktop-entry specification spells a literal percent as %%, but GLib on
# the supported Ubuntu 24.04 target rejects that sequence in the executable
# path and discards Exec entirely. A raw percent launches in GLib but fails
# desktop-file-validate. There is no representation accepted by both, so stop
# before mutation instead of installing a menu entry that cannot launch.
case "$TARGET" in
    *%*)
        fail "The installation path cannot contain a percent sign: $TARGET" \
             "Choose another location with --prefix. Nothing has been installed."
        ;;
esac

if [ "$TARGET" = "$SOURCE" ]; then
    fail "The source and installation directory are the same: $TARGET" \
         "Choose another location with --prefix. Nothing has been installed."
fi
case "$TARGET" in
    "$SOURCE"/*)
        fail "The installation directory is inside the extracted application: $TARGET" \
             "Choose another location with --prefix. Nothing has been installed."
        ;;
esac
case "$SOURCE" in
    "$TARGET"/*)
        fail "The extracted application is inside the installation directory: $SOURCE" \
             "Choose another location with --prefix. Nothing has been installed."
        ;;
esac

# Lock before inspecting an installed target, staging, or touching integration.
TARGET_PARENT="${TARGET%/*}"
# A missing update prefix must be refused before mkdir creates anything.
if [ "$UPDATE" -eq 1 ] && [ ! -d "$TARGET_PARENT" ]; then
    fail "--update: there is no Waveguide Generator installation at $TARGET." "Nothing has been changed."
fi
check_interrupted
mkdir -p "$TARGET_PARENT" || fail "Could not create the target parent."
LOCK_PATH="$TARGET_PARENT/.${TARGET##*/}.install.lock"
acquire_lock

# Validate an existing target before creating even the shared destination
# directories, and before any rename can make it disappear. --update (the
# in-app updater's helper) additionally requires that an installation is there,
# so it can never create a fresh one or touch anything else.
if [ "$UPDATE" -eq 1 ] && [ ! -e "$TARGET/app/APP-MANIFEST.json" ]; then
    fail "--update: there is no Waveguide Generator installation at $TARGET." \
         "Nothing has been changed."
fi
if [ -e "$TARGET" ]; then
    [ -e "$TARGET/app/APP-MANIFEST.json" ] || \
        fail "$TARGET already exists and is not a Waveguide Generator installation." \
             "" \
             "Refusing to replace it. Choose another location with --prefix, or" \
             "remove that directory yourself if you know what it is."
fi

printf '\n'
printf 'Installing Waveguide Generator\n'
printf '==============================\n'
printf '\n'
printf 'Application: %s\n' "$TARGET"
printf 'Menu entry:  %s/%s\n' "$APPLICATIONS" "$DESKTOP_ENTRY_NAME"
printf 'Command:     %s/%s\n' "$BIN" "$LAUNCHER_NAME"
printf '\n'

mkdir -p "$PREFIX" "$APPLICATIONS" "$ICONS" "$BIN" || \
    fail "Could not create the installation directories under $HOME_DIRECTORY."

# Build and validate every new artefact before moving the current installation.
# TARGET may resolve through a symlink; stage and back up beside its actual path.
TARGET_PARENT="${TARGET%/*}"
# Files are staged on the same filesystems as their final names, so the commit
# below uses renames for directories and link/unlink for files, preserving
# the identities of every displaced predecessor.
STAGE_ROOT=""
STAGE_ROOT_ID=""
STAGED_TARGET=""
STAGED_DESKTOP=""
STAGED_ICON=""
STAGED_DESKTOP_OWNER=""
STAGED_ICON_OWNER=""

# Swap table: LIVE[i], BACKUP[i], STAGED[i], with old/new device/inode identities.
# Each row goes staged -> prepared -> displace_intent -> displaced ->
# install_intent -> installed. Intent is recorded BEFORE acting; both the
# command result and the exact destination identity are verified afterward.
# Cleanup reconciles identities on disk even when a signal precedes bookkeeping:
# prepared/displace_intent keeps or restores old; displaced/install_intent/
# installed returns new to staging and restores old. Recovery goes through
# evacuate_intent -> evacuated (new back to stage), then
# restore_intent -> restored with the same verification for EVERY row.
# Only after all rows verify does COMMITTED retain new and retire backups.
# File transfers use no-clobber link/unlink; directories use verified mv -n.
# Raced files are never replaced: report the real old-object location
# (including BSD mv nesting) with exit 3. Uncommitted clean recovery exits 1.
COMMITTED=0
LIVE=("$TARGET" "$APPLICATIONS/$DESKTOP_ENTRY_NAME" "$ICONS/$ICON_NAME"
      "$APPLICATIONS/$DESKTOP_OWNER_NAME" "$ICONS/$ICON_OWNER_NAME" "$BIN/$LAUNCHER_NAME")
STAGED=("$STAGED_TARGET" "$STAGED_DESKTOP" "$STAGED_ICON"
        "$STAGED_DESKTOP_OWNER" "$STAGED_ICON_OWNER" "$BIN/.waveguide-generator.link.new.$$")
BACKUP=("" "" "" "" "" "")
RESERVATION_ID=("" "" "" "" "" "")
DESCRIPTION=("application" "desktop entry" "icon" "desktop ownership marker" "icon ownership marker" "command link")
INSTALL_ERROR=("Could not put the staged application in $TARGET." "Could not install the rendered desktop entry."
               "Could not install the application icon." "Could not install desktop ownership."
               "Could not install icon ownership." "Could not install the command link.")
STATE=(staged staged staged staged staged staged)
OLD_ID=("" "" "" "" "" "")
NEW_ID=("" "" "" "" "" "")
check_interrupted
MV_OPTIONS=(-n --)

SWAP_READY=1
check_interrupted

STAGE_ROOT=$(protected_output mktemp -d "$TARGET_PARENT/.waveguide-generator.install.XXXXXX") || fail "Could not create application staging."
STAGE_ROOT_ID=$(object_id "$STAGE_ROOT") || fail "Could not identify application staging."
STAGED[0]="$STAGE_ROOT/$BUNDLE_DIRECTORY"
run_housekeeping mkdir "${STAGED[0]}" || fail "Could not create the staged application."
NEW_ID[0]=$(object_id "${STAGED[0]}") || fail "Could not identify the staged application."
check_interrupted
STAGED[1]=$(protected_output mktemp "$APPLICATIONS/.waveguide-generator.XXXXXX.desktop") || fail "Could not stage the desktop entry."
NEW_ID[1]=$(object_id "${STAGED[1]}") || fail "Could not identify desktop staging."
check_interrupted
STAGED[2]=$(protected_output mktemp "$ICONS/.waveguide-generator.icon.XXXXXX") || fail "Could not stage the icon."
NEW_ID[2]=$(object_id "${STAGED[2]}") || fail "Could not identify icon staging."
check_interrupted
STAGED[3]=$(protected_output mktemp "$APPLICATIONS/.waveguide-generator.owner.XXXXXX") || fail "Could not stage desktop ownership."
NEW_ID[3]=$(object_id "${STAGED[3]}") || fail "Could not identify desktop-owner staging."
check_interrupted
STAGED[4]=$(protected_output mktemp "$ICONS/.waveguide-generator.owner.XXXXXX") || fail "Could not stage icon ownership."
NEW_ID[4]=$(object_id "${STAGED[4]}") || fail "Could not identify icon-owner staging."
check_interrupted
STAGED_TARGET=${STAGED[0]}
STAGED_DESKTOP=${STAGED[1]}
STAGED_ICON=${STAGED[2]}
STAGED_DESKTOP_OWNER=${STAGED[3]}
STAGED_ICON_OWNER=${STAGED[4]}

# No-clobber moves refuse raced files. GNU -T also refuses directory nesting;
# BSD fixtures still reconcile any nested object by its recorded identity.
run_housekeeping mkdir "$STAGE_ROOT/mv-probe-source" || fail "Could not probe safe rename support."
PROBE_ID=$(object_id "$STAGE_ROOT/mv-probe-source") || fail "Could not identify the rename probe."
if (cd -- "$STAGE_ROOT" && mv -T -n -- mv-probe-source mv-probe-target </dev/null 2>/dev/null); then
    MV_OPTIONS=(-T -n --)
fi
remove_owned "$STAGE_ROOT/mv-probe-source" "$PROBE_ID"
remove_owned "$STAGE_ROOT/mv-probe-target" "$PROBE_ID"
PROBE_ID=""

check_interrupted
printf 'Staging the application (this takes a moment) ...\n'
run_interruptible cp -a -- "$SOURCE/." "$STAGED_TARGET" || \
    fail "Could not stage the application under $PREFIX." \
         "Check that there is enough free space and that $PREFIX is writable."
check_interrupted
cp -- "$HERE/$UNINSTALLER_NAME" "$STAGED_TARGET/$UNINSTALLER_NAME" || \
    fail "Could not add the uninstaller to the staged application."
check_interrupted
chmod 755 "$STAGED_TARGET/$UNINSTALLER_NAME" || \
    fail "Could not make the staged uninstaller executable."

check_interrupted
EXECUTABLE="$(desktop_exec_escape "$TARGET/$LAUNCHER_NAME")"
DESKTOP_RENDERED=0
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        "Exec=@INSTALL_DIR@/$LAUNCHER_NAME")
            printf 'Exec="%s"\n' "$EXECUTABLE"
            DESKTOP_RENDERED=1
            ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$STAGED_TARGET/$DESKTOP_ENTRY_NAME" > "$STAGED_DESKTOP" || \
    fail "Could not render the desktop entry under $APPLICATIONS."
[ "$DESKTOP_RENDERED" -eq 1 ] || \
    fail "The staged desktop entry does not contain the expected Exec template."
check_interrupted
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$STAGED_DESKTOP" || \
        fail "The rendered desktop entry failed desktop-file-validate."
fi
check_interrupted
chmod 644 "$STAGED_DESKTOP" || fail "Could not set the desktop entry permissions."
check_interrupted
cp -- "$STAGED_TARGET/$ICON_NAME" "$STAGED_ICON" || fail "Could not stage the application icon."
check_interrupted
chmod 644 "$STAGED_ICON" || fail "Could not set the icon permissions."
printf '%s\n' "$TARGET" > "$STAGED_DESKTOP_OWNER" || fail "Could not record desktop ownership."
printf '%s\n' "$TARGET" > "$STAGED_ICON_OWNER" || fail "Could not record icon ownership."

check_interrupted

# Refuse to overwrite an unrelated command. Symlinks are the form this
# installer owns, including a broken link left by an older installation.
if { [ -e "$BIN/$LAUNCHER_NAME" ] || [ -L "$BIN/$LAUNCHER_NAME" ]; } && \
   [ ! -L "$BIN/$LAUNCHER_NAME" ]; then
    fail "$BIN/$LAUNCHER_NAME already exists and is not a symlink." \
         "Move it yourself before installing. Nothing has been replaced."
fi

# Stage the link too. Its final no-clobber creation keeps this recorded identity;
# the live hard link is verified before the staging alias is removed.
[ ! -e "${STAGED[5]}" ] && [ ! -L "${STAGED[5]}" ] || fail "The staged command path is occupied."
run_housekeeping ln -s -- "$TARGET/$LAUNCHER_NAME" "${STAGED[5]}" || fail "Could not stage the command link."
NEW_ID[5]=$(object_id "${STAGED[5]}") || fail "Could not identify the staged command link."
check_interrupted

# Reserve rollback names as empty directories. rmdir cannot destroy foreign
# files or contents, including replacement before mktemp returns the name.
for ((i=0; i<${#LIVE[@]}; i++)); do
    case "$i" in
        0) template="$TARGET_PARENT/.waveguide-generator.previous.XXXXXX" ;;
        1) template="$APPLICATIONS/.waveguide-generator.desktop.backup.XXXXXX" ;;
        2) template="$ICONS/.waveguide-generator.icon.backup.XXXXXX" ;;
        3) template="$APPLICATIONS/.waveguide-generator.owner.backup.XXXXXX" ;;
        4) template="$ICONS/.waveguide-generator.owner.backup.XXXXXX" ;;
        5) template="$BIN/.waveguide-generator.link.backup.XXXXXX" ;;
    esac
    BACKUP[i]=$(protected_output mktemp -d "$template") || fail "Could not reserve ${DESCRIPTION[i]} rollback."
    RESERVATION_ID[i]=$(object_id "${BACKUP[i]}") || fail "Could not identify rollback reservation."
    same_object "${BACKUP[i]}" "${RESERVATION_ID[i]}" && run_housekeeping rmdir "${BACKUP[i]}" || fail "The rollback reservation is occupied or replaced: ${BACKUP[i]}"
    RESERVATION_ID[i]=""
    check_interrupted
done

printf 'Committing the staged installation ...\n'
for ((i=0; i<${#LIVE[@]}; i++)); do
    same_object "${STAGED[i]}" "${NEW_ID[i]}" || fail "The staged ${DESCRIPTION[i]} is missing or replaced."
    same_device "${STAGED[i]}" "${LIVE[i]}" || fail "The staging and destination must be on the same device."
    OLD_ID[i]="$(object_id "${LIVE[i]}")" || OLD_ID[i]=""
    check_interrupted
    if { [ -e "${LIVE[i]}" ] || [ -L "${LIVE[i]}" ]; } && [ -z "${OLD_ID[i]}" ]; then
        fail "Could not identify the existing ${DESCRIPTION[i]}."
    fi
    STATE[i]=prepared
    check_interrupted
    if [ -n "${OLD_ID[i]}" ]; then
        [ ! -e "${BACKUP[i]}" ] && [ ! -L "${BACKUP[i]}" ] || fail "The backup path is still occupied."
        STATE[i]=displace_intent
        check_interrupted
        bounded_move "${LIVE[i]}" "${BACKUP[i]}"
        move_status=$?
        check_interrupted
        if ! verify_move "${LIVE[i]}" "${BACKUP[i]}" "${OLD_ID[i]}" || [ "$move_status" -ne 0 ]; then
            fail "Could not move the existing ${DESCRIPTION[i]} aside."
        fi
    fi
    STATE[i]=displaced
    check_interrupted
    [ ! -e "${LIVE[i]}" ] && [ ! -L "${LIVE[i]}" ] || fail "The ${DESCRIPTION[i]} destination is occupied."
    STATE[i]=install_intent
    check_interrupted
    bounded_move "${STAGED[i]}" "${LIVE[i]}"
    move_status=$?
    check_interrupted
    if ! verify_move "${STAGED[i]}" "${LIVE[i]}" "${NEW_ID[i]}" || [ "$move_status" -ne 0 ]; then
        fail "${INSTALL_ERROR[i]}"
    fi
    STATE[i]=installed
    check_interrupted
done
check_interrupted
COMMITTED=1
# Once committed, finish the success message and removal of this run's backups.
trap '' HUP INT TERM QUIT
start_work_clock
for ((i=0; i<${#BACKUP[@]}; i++)); do
    remove_owned "${BACKUP[i]}" "${OLD_ID[i]}"
    OLD_ID[i]=""
done

# Best effort, and genuinely optional: every current desktop notices a new
# .desktop file on its own, and these tools are absent on minimal systems.
if command -v update-desktop-database >/dev/null 2>&1; then
    run_optional update-desktop-database "$APPLICATIONS" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    run_optional gtk-update-icon-cache -q -t -f "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

printf '\n'
printf 'Installed: %s\n' "$TARGET"
printf '\n'

case ":${PATH}:" in
    *":$BIN:"*) ;;
    *)
        printf '%s is not on your PATH, so the "%s" command will not be found\n' \
            "$BIN" "$LAUNCHER_NAME"
        printf 'until you add it. The menu entry works either way.\n\n'
        ;;
esac

printf 'To remove it later:\n'
printf '  %s/%s          (add --data to remove your designs and job history too)\n\n' \
    "$TARGET" "$UNINSTALLER_NAME"

if [ "$LAUNCH" -eq 1 ]; then
    printf 'Starting Waveguide Generator ...\n'
    # The application outlives the installer; give it ordinary signal handling.
    (trap - HUP INT TERM QUIT PIPE; exec "$TARGET/$LAUNCHER_NAME") >/dev/null 2>&1 &
fi
check_interrupted
exit 0
