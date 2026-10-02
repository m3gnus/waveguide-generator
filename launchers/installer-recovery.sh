#!/bin/sh
# Independent recovery entry staged beside the destination before installation.
# The record is data: no eval, source, bundled interpreter or wildcard removal.
set -u
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077
target=$1
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 3
config="$base/config"
base_id=$(stat -c '%d:%i' -- "$base" 2>/dev/null || stat -f '%d:%i' "$base" 2>/dev/null) || exit 3
lock="${target%/*}/.${target##*/}.install.lock"
journal="$lock.journal"
ledger="$journal"
committed=0
claim="$base/recovering"
object_id() { stat -c '%d:%i' -- "$1" 2>/dev/null || stat -f '%d:%i' "$1" 2>/dev/null; }
exists() { [ -e "$1" ] || [ -L "$1" ]; }
same() { [ -n "$2" ] && [ "$(object_id "$1")" = "$2" ]; }
bounded_regular() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(wc -c < "$1")" -le "$2" ]; }
fail() { printf 'WG installer recovery: %s\n' "$1" >&2; exit 3; }
case "$target" in /*) ;; *) fail 'target is not absolute' ;; esac
[ "$base" = "${target%/*}/.${target##*/}.installer-recovery" ] || fail 'recovery entry is outside its exact destination'
bounded_regular "$config" 16384 || fail 'configuration is not a bounded regular file'
exec 4< "$config"
IFS= read -r header <&4 && IFS= read -r saved_target <&4 && IFS= read -r old_root_id <&4 &&
IFS= read -r work <&4 && IFS= read -r metadata <&4 && IFS= read -r platform <&4 &&
IFS= read -r saved_home <&4 && IFS= read -r saved_data_home <&4 || fail 'configuration is incomplete'
[ "$header" = WG-INSTALL-RECOVERY-1 ] && [ "$saved_target" = "$target" ] || fail 'configuration names another destination'
case "$old_root_id" in *[!0-9:]*|''|:*) fail 'old root identity is invalid' ;; esac
case "$work" in /*/update-install) ;; *) fail 'helper directory is invalid' ;; esac
[ ! -L "$work" ] && [ -d "$work" ] || fail 'helper directory is linked or missing'
case "$platform" in linux-x86_64|macos-arm64) ;; *) fail 'unsupported recovery platform' ;; esac
if [ "$platform" = macos-arm64 ]; then PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH; fi
case "$saved_home" in /*) ;; *) fail 'saved home is not absolute' ;; esac
recovery_home=$saved_home
integration_data=${saved_data_home:-$recovery_home/.local/share}
case "$integration_data" in /*) ;; *) fail 'saved integration directory is not absolute' ;; esac

# Recovery uses the same bounded writer as the foreground native installer.
# Redirect this process, retaining the exact PID recorded in recovery/native
# claims. Its new logger is outside the historical helper lock contents.
recovery=$base
log="$work/install.log"
logger="" logger_failed=0 log_claim_retained=0
prepare_log() {
    # Recovery is already admitted. Slot capture belongs to the asynchronous
    # recorded sink so diagnostic I/O cannot delay reconciliation here.
    [ -f "$recovery/log" ] && [ ! -L "$recovery/log" ] && [ -x "$recovery/log" ]
}
log_failure() {
    # Separate, bounded diagnostic evidence; never a recovery verdict/ledger.
    (set -C; printf '{"schemaVersion":1,"kind":"installer_log_error"}\n' > "$work/install.log.error") 2>/dev/null || :
    logger_failed=1
}
start_logger() {
    # Publish an invalid placeholder before forking: death between spawning
    # the writer and recording it must remain an ambiguous, refused owner.
    : > "$log_records/writer" || return 1
    (
        trap '' HUP INT TERM
        exec 6< "$fifo" || exit 72
        "$recovery/log" --writer-record "$log_records/writer" <&6 &
        writer=$!
        if ! printf '%s\n' "$writer" > "$log_records/writer"; then
            # This is our actual unreaped child, independent of any failed
            # on-disk record. Preserve the reader while draining native output.
            kill -KILL "$writer" 2>/dev/null || :
            wait "$writer" 2>/dev/null || :
            log_failure
            while IFS= read -r discarded <&6; do :; done
            exit 72
        fi
        writer_code=0
        wait "$writer" || writer_code=$?
        if [ "$writer_code" -ne 0 ]; then
            log_failure
            # Keep the read FD open continuously. A failed writer must not
            # SIGPIPE the native process or block it on a full orphaned FIFO.
            while IFS= read -r discarded <&6; do :; done
        fi
        exit "$writer_code"
    ) &
    logger=$!
    if ! printf '%s\n' "$logger" > "$log_records/logger"; then
        # Its FIFO read cannot connect until start_logger returns to the
        # foreground writer, so no native logger child has been started on this path.
        kill -KILL "$logger" 2>/dev/null || :
        wait "$logger" 2>/dev/null || :
        logger=""
        log_failure
        return 1
    fi
}
logging_settled() {
    # A dead supervisor does not prove its recorded sink has stopped. Keep
    # live/ambiguous ownership rather than erase the only descendant record.
    candidate=$1
    if [ ! -e "$candidate/sink" ] && [ ! -L "$candidate/sink" ]; then return 0; fi
    [ -f "$candidate/sink" ] && [ ! -L "$candidate/sink" ] &&
    [ "$(wc -c < "$candidate/sink")" -le 32 ] || return 1
    sink=$(cat "$candidate/sink")
    case "$sink" in ''|*[!0-9]*) return 1 ;; esac
    [ "$sink" -gt 0 ] && ! kill -0 "$sink" 2>/dev/null
}
stop_log_writer() {
    [ -f "$log_records/writer" ] && [ ! -L "$log_records/writer" ] &&
    [ "$(wc -c < "$log_records/writer")" -le 32 ] || return 0
    writer=$(cat "$log_records/writer")
    case "$writer" in ''|*[!0-9]*) return 0 ;; esac
    writer_parent=$(ps -o ppid= -p "$writer" 2>/dev/null | tr -d ' ')
    [ "$writer_parent" = "$logger" ] || return 0
    if [ -f "$log_records/sink" ] && [ ! -L "$log_records/sink" ] &&
       [ "$(wc -c < "$log_records/sink")" -le 32 ]; then
        sink=$(cat "$log_records/sink")
        case "$sink" in ''|*[!0-9]*) sink="" ;; esac
        if [ -n "$sink" ] && [ "$(ps -o ppid= -p "$sink" 2>/dev/null | tr -d ' ')" = "$writer" ]; then
            kill -KILL "$sink" 2>/dev/null || :
            # An explicitly stopped supervisor must get a chance to reap its
            # captured sink before the outer escalation terminates it.
            kill -CONT "$writer" 2>/dev/null || :
            sink_reap=0
            while kill -0 "$sink" 2>/dev/null && [ "$sink_reap" -lt 50 ]; do
                sleep .01; sink_reap=$((sink_reap + 1))
            done
        fi
    fi
    kill -KILL "$writer" 2>/dev/null || :
}
drain_logger() {
    [ -n "$logger" ] || return 0
    sleep 6 >/dev/null 2>&1 &
    log_clock=$!
    (
        while kill -0 "$log_clock" 2>/dev/null && kill -0 "$logger" 2>/dev/null; do sleep .01; done
        stop_log_writer
        # Give its actual child owner a chance to reap the killed writer.
        log_reap=0
        while kill -0 "$logger" 2>/dev/null && [ "$log_reap" -lt 50 ]; do
            sleep .01; log_reap=$((log_reap + 1))
        done
        kill -KILL "$logger" 2>/dev/null || :
    ) &
    log_watcher=$!
    logger_code=0
    wait "$logger" || logger_code=$?
    while kill -0 "$logger" 2>/dev/null; do wait "$logger" || logger_code=$?; done
    kill -KILL "$log_watcher" "$log_clock" 2>/dev/null || :
    wait "$log_watcher" 2>/dev/null || :
    wait "$log_clock" 2>/dev/null || :
    logger=""
    [ "$logger_code" -eq 0 ] || log_failure
}
log_fifo="$claim/output"
log_fifo_id=""
finish_log() {
    exec 1>&7 2>&8
    drain_logger
    if [ -n "$log_fifo_id" ] && same "$claim" "$claim_id" && [ ! -L "$claim" ] &&
       same "$log_fifo" "$log_fifo_id" && [ -p "$log_fifo" ] && [ ! -L "$log_fifo" ] &&
       logging_settled "$claim"; then
        rm -f "$log_fifo" "$claim/logger" "$claim/writer" "$claim/sink"
    fi
}

# Structural evidence supplements owner liveness. A dead PID by itself never
# authorizes deleting an unknown lock. Only the sole bounded pid record of the
# saved exact-target lock may be retired, after the disk identities validate.
dead_lock() {
    candidate=$1
    [ -d "$candidate" ] && [ ! -L "$candidate" ] || return 1
    contents=$(ls -A "$candidate")
    if { [ "$candidate" = "$work/lock" ] || [ "$candidate" = "$claim" ]; } && [ "$contents" != pid ]; then
        if [ "$contents" = "$(printf 'logger\noutput\npid\nsink\nwriter')" ]; then
            bounded_regular "$candidate/sink" 32 || return 1
            sink_pid=$(cat "$candidate/sink")
            case "$sink_pid" in ''|*[!0-9]*) return 1 ;; esac
            [ "$sink_pid" -gt 0 ] && ! kill -0 "$sink_pid" 2>/dev/null || return 1
        else
            # An old AWK writer, or a native supervisor that died before it
            # exclusively created its sink record, has no sink descendant.
            [ "$contents" = "$(printf 'logger\noutput\npid\nwriter')" ] || return 1
        fi
        [ -p "$candidate/output" ] && [ ! -L "$candidate/output" ] || return 1
        bounded_regular "$candidate/logger" 32 || return 1
        logger_pid=$(cat "$candidate/logger")
        case "$logger_pid" in ''|*[!0-9]*) return 1 ;; esac
        [ "$logger_pid" -gt 0 ] && ! kill -0 "$logger_pid" 2>/dev/null || return 1
        bounded_regular "$candidate/writer" 32 || return 1
        writer_pid=$(cat "$candidate/writer")
        case "$writer_pid" in ''|*[!0-9]*) return 1 ;; esac
        [ "$writer_pid" -gt 0 ] && ! kill -0 "$writer_pid" 2>/dev/null || return 1
    else
        [ "$contents" = pid ] || return 1
    fi
    bounded_regular "$candidate/pid" 32 || return 1
    owner=$(cat "$candidate/pid")
    case "$owner" in ''|*[!0-9]*) return 1 ;; esac
    [ "$owner" -gt 0 ] || return 1
    if kill -0 "$owner" 2>/dev/null; then
        # A helper may explicitly ask its recovery child to settle a killed
        # native installer while retaining its own independent ownership.
        [ "$candidate" = "$work/lock" ] && [ "$owner" = "${WG_INSTALLER_RECOVERY_OWNER_PID:-}" ] || return 1
    fi
    return 0
}
retire_dead_lock() {
    candidate=$1
    exists "$candidate" || return 0
    identity=$(object_id "$candidate") || return 1
    dead_lock "$candidate" || return 1
    same "$candidate" "$identity" || return 1
    rm -f "$candidate/pid" "$candidate/logger" "$candidate/writer" "$candidate/sink" "$candidate/output" && rmdir "$candidate"
}

# Check all path families and object identities before acquiring a claim or
# changing anything. Linux's shared integration paths are derived locally.
validate_row() {
    case "$old_id:$new_id" in *[!0-9:]*|:) return 1 ;; esac
    [ -n "$new_id" ] || return 1
    [ "$row" -ne 0 ] || [ "$old_id" = "$old_root_id" ] || return 1
    parent=${live%/*}
    case "$platform:$row" in
        macos-arm64:0)
            [ "$live" = "$target" ] || return 1
            case "$backup" in "${target%/*}/.${target##*/}.previous."[0-9]*) ;; *) return 1 ;; esac
            case "$staged" in "${target%/*}/.${target##*/}.new."[0-9]*) ;; *) return 1 ;; esac ;;
        linux-x86_64:0)
            [ "$live" = "$target" ] || return 1
            case "$backup" in "${target%/*}/.waveguide-generator.previous."??????) ;; *) return 1 ;; esac
            case "$staged" in "${target%/*}/.waveguide-generator.install."??????/waveguide-generator) ;; *) return 1 ;; esac ;;
        linux-x86_64:1|linux-x86_64:3)
            applications="$integration_data/applications"
            case "$row" in 1) name=waveguide-generator.desktop; backup_prefix=.waveguide-generator.desktop.backup. ;;
                           3) name=.waveguide-generator.owner; backup_prefix=.waveguide-generator.owner.backup. ;; esac
            [ "$live" = "$applications/$name" ] || return 1
            case "$backup" in "$applications/$backup_prefix"??????) ;; *) return 1 ;; esac
            case "$row:$staged" in
                "1:$applications/.waveguide-generator."??????.desktop|"3:$applications/.waveguide-generator.owner."??????) ;;
                *) return 1 ;; esac ;;
        linux-x86_64:2|linux-x86_64:4)
            icons="$integration_data/icons/hicolor/512x512/apps"
            case "$row" in 2) name=waveguide-generator.png; backup_prefix=.waveguide-generator.icon.backup. ;;
                           4) name=.waveguide-generator.owner; backup_prefix=.waveguide-generator.owner.backup. ;; esac
            [ "$live" = "$icons/$name" ] || return 1
            case "$backup" in "$icons/$backup_prefix"??????) ;; *) return 1 ;; esac
            case "$row:$staged" in
                "2:$icons/.waveguide-generator.icon."??????|"4:$icons/.waveguide-generator.owner."??????) ;;
                *) return 1 ;; esac ;;
        linux-x86_64:5)
            [ "$live" = "$recovery_home/.local/bin/waveguide-generator" ] || return 1
            case "$backup" in "$recovery_home/.local/bin/.waveguide-generator.link.backup."??????) ;; *) return 1 ;; esac
            case "$staged" in "$recovery_home/.local/bin/.waveguide-generator.link.new."[0-9]*) ;; *) return 1 ;; esac ;;
        *) return 1 ;;
    esac
    # Any occupied recorded path must be one of this transaction's objects.
    if exists "$live"; then same "$live" "$old_id" || same "$live" "$new_id" || return 1; fi
    if exists "$backup"; then same "$backup" "$old_id" || return 1; fi
    if exists "$staged"; then same "$staged" "$new_id" || return 1; fi
    if [ "$committed" -eq 1 ]; then
        same "$live" "$new_id" || return 1
    else
        [ -z "$old_id" ] || same "$live" "$old_id" || same "$backup" "$old_id" || return 1
    fi
}
read_row() {
    IFS= read -r live <&3 && IFS= read -r backup <&3 && IFS= read -r staged <&3 &&
    IFS= read -r old_id <&3 && IFS= read -r new_id <&3
}
open_journal() {
    exec 3< "$ledger"
    IFS= read -r magic <&3 && IFS= read -r recorded_target <&3 && IFS= read -r count <&3 || return 1
    [ "$magic" = WG-INSTALL-JOURNAL-1 ] && [ "$recorded_target" = "$target" ] || return 1
    case "$platform:$count" in macos-arm64:1|linux-x86_64:6) ;; *) return 1 ;; esac
}
has_journal=0 journal_id=""
# A durable external receipt survives retirement of the native ledger. Its
# complete saved table, exact live new IDs and absent native owner prove a
# committed installation; version labels and a vanished lock do not.
if exists "$base/committed"; then
    bounded_regular "$base/committed" 16384 || fail 'commit receipt is not a bounded regular file'
    ledger="$base/committed"
    if exists "$journal"; then
        [ "$(object_id "$journal")" = "$(object_id "$ledger")" ] || fail 'journal and commit receipt disagree'
    fi
fi
if exists "$ledger"; then
    bounded_regular "$ledger" 16384 || fail 'journal is not a bounded regular file'
    journal_id=$(object_id "$ledger") || fail 'cannot identify journal'
    [ "$(tail -n 1 "$ledger")" != COMMITTED ] || committed=1
    open_journal || fail 'journal header is invalid'
    row=0
    while [ "$row" -lt "$count" ]; do
        read_row && validate_row || fail 'ambiguous objects or invalid recovery path'
        row=$((row + 1))
    done
    if [ "$committed" -eq 1 ]; then
        IFS= read -r extra <&3 && [ "$extra" = COMMITTED ] || fail 'commit receipt is incomplete'
    fi
    if IFS= read -r extra <&3 || [ -n "${extra:-}" ]; then
        fail 'extra journal data requires inspection'
    fi
    same "$ledger" "$journal_id" || fail 'journal changed during validation'
    has_journal=1
else
    # Killed during staging: no rename was authorized, and the original root
    # must still be the exact object captured by the helper before detachment.
    same "$target" "$old_root_id" || fail 'no journal and original root identity changed'
fi
exists "$lock" && ! dead_lock "$lock" && fail 'native installer is live or its lock is ambiguous'
exists "$work/lock" && ! dead_lock "$work/lock" && fail 'helper is live or its lock is ambiguous'
if exists "$claim"; then
    # Row/old-root evidence was checked above. Preserve unknown contents and
    # any live recovery owner; retire only the exact identified sole-pid object.
    retire_dead_lock "$claim" || fail 'a recovery is live or its claim is ambiguous'
fi
mkdir "$claim" 2>/dev/null || fail 'another recovery owns this destination'
printf '%s\n' "$$" > "$claim/pid"
claim_id=$(object_id "$claim") || exit 3
finish() {
    if same "$claim" "$claim_id" && logging_settled "$claim"; then rm -f "$claim/pid"; rmdir "$claim" 2>/dev/null || :; fi
}
trap finish 0
trap 'exit 3' HUP INT TERM
retire_dead_lock "$lock" || fail 'native installer ownership changed'
# Hold the native target exclusion while reconciling; startup cannot enter it.
mkdir "$lock" || fail 'native exclusion changed'
printf '%s\n' "$$" > "$lock/pid"
native_id=$(object_id "$lock") || exit 3
# Diagnostic failure cannot strand an otherwise verified old installation.
# Preserve foreign slots; the fixed bounded error record is separate evidence.
exec 7>&1 8>&2
trap 'finish_log; finish' 0
if prepare_log && mkfifo "$log_fifo" && log_fifo_id=$(object_id "$log_fifo"); then
    log_records=$claim
    fifo=$log_fifo
    WG_INSTALLER_LOG=$log WG_INSTALLER_LOG_ECHO=1
    export WG_INSTALLER_LOG WG_INSTALLER_LOG_ECHO
    if start_logger; then
        exec 1>"$log_fifo" 2>&1
    else
        log_failure
        exec 1>/dev/null 2>&1
    fi
else
    log_failure
    exec 1>/dev/null 2>&1
fi
trap '' HUP INT TERM
recovery_owner=$$
kill_recovery_child() {
    # These PIDs were captured from this parent's unreaped forks. Confirm
    # parentage too before signaling; never act on a reused foreign PID.
    child_parent=$(ps -o ppid= -p "$1" 2>/dev/null | tr -d ' ')
    [ "$child_parent" != "$recovery_owner" ] || kill -KILL "$1" 2>/dev/null || :
}
move_owned() {
    source=$1 destination=$2 expected=$3
    same "$source" "$expected" && ! exists "$destination" || return 1
    # Keep an explicit active child record: a hard-killed recovery parent must
    # not authorize a second owner while its rename child might still act.
    mv -n "$source" "$destination" &
    mover=$!
    printf '%s\n' "$mover" > "$lock/move"
    sleep 5 &
    recovery_clock=$!
    (
        while kill -0 "$recovery_clock" 2>/dev/null && kill -0 "$mover" 2>/dev/null; do sleep .01; done
        if ! kill -0 "$recovery_clock" 2>/dev/null; then kill_recovery_child "$mover"; fi
    ) &
    recovery_watchdog=$!
    wait "$mover"
    result=$?
    kill_recovery_child "$recovery_watchdog"
    kill_recovery_child "$recovery_clock"
    wait "$recovery_watchdog" 2>/dev/null || :
    wait "$recovery_clock" 2>/dev/null || :
    rm -f "$lock/move"
    [ "$result" -eq 0 ] || return 1
    ! exists "$source" && same "$destination" "$expected"
}
if [ "$has_journal" -eq 1 ] && [ "$committed" -eq 0 ]; then
    same "$ledger" "$journal_id" && open_journal || fail 'journal changed'
    row=0
    while [ "$row" -lt "$count" ]; do
        read_row && validate_row || fail 'objects changed during recovery'
        if same "$live" "$new_id"; then
            if same "$staged" "$new_id"; then
                # Native file link/unlink may have been interrupted between
                # its two operations. Remove only the proven duplicate link.
                [ ! -d "$live" ] && rm -f "$live" || fail 'duplicate directory needs inspection'
            else
                move_owned "$live" "$staged" "$new_id" || fail 'cannot evacuate exact new object'
            fi
        fi
        if [ -n "$old_id" ] && ! same "$live" "$old_id"; then
            move_owned "$backup" "$live" "$old_id" || fail 'cannot restore exact previous object'
        fi
        [ -z "$old_id" ] || same "$live" "$old_id" || fail 'previous object is not restored'
        row=$((row + 1))
    done
    same "$journal" "$journal_id" || fail 'journal changed after reconciliation'
    # Preserve the decided evidence and staged copies; never delete recovery
    # backups to make a crash look clean. Archive by no-clobber link.
    ln "$journal" "$journal.recovered.$$" && rm -f "$journal" || fail 'cannot archive decided journal'
    sync || fail 'cannot persist decided recovery'
fi
if [ "$committed" -eq 1 ]; then
    # Old backups may still exist after a killed cleanup. Preserve all of them,
    # and archive the committed evidence rather than performing a rollback.
    same "$ledger" "$journal_id" && open_journal || fail 'commit receipt changed'
    row=0
    while [ "$row" -lt "$count" ]; do
        read_row && validate_row || fail 'committed installation changed'
        row=$((row + 1))
    done
    if exists "$journal"; then
        same "$journal" "$journal_id" || fail 'committed journal changed'
        ln "$journal" "$journal.committed.$$" && rm -f "$journal" || fail 'cannot archive committed journal'
    fi
fi
when=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
if [ "$committed" -eq 1 ]; then
    outcome_fields='"result":"installed","reason":"A committed installer was recovered."'
else
    outcome_fields='"result":"failed","previousKept":true,"reason":"An interrupted installer was recovered."'
fi
printf '%s,%s,"when":"%s"}\n' "$metadata" "$outcome_fields" "$when" > "$work/outcome.json.tmp" &&
mv -f "$work/outcome.json.tmp" "$work/outcome.json" || fail 'cannot publish recovery outcome'
sync || fail 'cannot persist recovery outcome'
# The current recovery process already has its source open; removing these
# exact data files makes a later startup avoid re-consuming decided evidence.
rm -f "$base/config" "$base/committed" "$base/recover.sh" "$base/log" || fail 'cannot retire decided recovery entry'
finish_log
if ! logging_settled "$claim"; then
    # Keep the actual surviving/ambiguous sink exclusion in the shared helper
    # slot before clearing the fixed startup route. The decided app may start,
    # but a later update cannot start another writer over these diagnostics.
    retire_dead_lock "$work/lock" || fail 'cannot retain unsettled log ownership'
    move_owned "$claim" "$work/lock" "$claim_id" || fail 'cannot preserve unsettled log ownership'
    log_claim_retained=1
fi
finish
trap - 0
if ! rmdir "$base" 2>/dev/null; then
    # The native decision is already durable; native exclusion remains held. Keep
    # unidentified diagnostic occupants without blocking that decided app's
    # next start or turning a logging failure into rollback_incomplete.
    decided="$base.decided.$$"
    move_owned "$base" "$decided" "$base_id" || fail 'cannot preserve decided diagnostic material'
fi
# Remove only our exact native claim after outcome persistence. A replaced
# owner makes recovery fail; a manual start cannot outrun its failed outcome.
same "$lock" "$native_id" || fail 'native exclusion was replaced'
rm -f "$lock/pid" && rmdir "$lock" || fail 'cannot release native exclusion'
if exists "$work/lock" && [ "$log_claim_retained" -eq 0 ]; then
    retire_dead_lock "$work/lock" || fail 'helper ownership changed after recovery'
fi
exit 0
