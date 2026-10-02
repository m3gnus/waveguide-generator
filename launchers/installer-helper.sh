#!/bin/sh
# Copied outside the installation before handoff; host tools only.
set -u
platform=$1 asset=$2 payload=$3 target=$4 parent_pid=$5 work=$6 metadata=$7
expected_sha=$8 journal_json=$9 recovery=${10}
lock="$work/lock" log="$work/install.log" outcome="$work/outcome.json"
mount="" child="" logger="" target_lock_id="" recovery_decided=0
recovery_id=""
logger_failed=0
target_lock="${target%/*}/.${target##*/}.install.lock"
umask 077
if [ "$platform" = macos-arm64 ]; then PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH; fi
# No automatic stale-lock deletion. A PID alone cannot prove that recovery
# files are safe to remove. A leftover lock and journal require inspection.
mkdir "$lock" 2>/dev/null || exit 4
printf '%s\n' "$$" > "$lock/pid"
lock_id=$(ls -di "$lock" | awk '{print $1}')
recovery_id=$(stat -c '%d:%i' -- "$recovery" 2>/dev/null || stat -f '%d:%i' "$recovery" 2>/dev/null)
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
finish() {
    [ -z "$mount" ] || hdiutil detach "$mount" -quiet >/dev/null 2>&1 || :
    if [ "$(ls -di "$lock" 2>/dev/null | awk '{print $1}')" = "$lock_id" ] && logging_settled "$lock"; then
        rm -f "$lock/pid" "$lock/output" "$lock/logger" "$lock/writer" "$lock/sink"; rmdir "$lock" 2>/dev/null || :
    fi
    # The native installer adopts this same object and records its own PID.
    # The helper must never remove a lock another owner adopted/replaced.
    if [ -n "$target_lock_id" ] && [ "$(object_id "$target_lock" 2>/dev/null)" = "$target_lock_id" ] &&
       [ -f "$target_lock/pid" ] && [ ! -L "$target_lock/pid" ] && [ "$(cat "$target_lock/pid")" = "$$" ]; then
        rm -f "$target_lock/pid"; rmdir "$target_lock" 2>/dev/null || :
    fi
    if [ "$recovery_decided" -eq 1 ] && [ "$(object_id "$recovery" 2>/dev/null)" = "$recovery_id" ]; then
        rm -f "$recovery/config" "$recovery/recover.sh" "$recovery/log" "$recovery/committed"
        rmdir "$recovery" 2>/dev/null || :
    fi
}
trap finish 0
trap 'exit 1' HUP INT TERM
record() {
    verdict=$1
    when=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    # metadata was JSON-encoded by the launcher. It is data, never evaluated.
    backup=""
    if [ "$verdict" = rollback_incomplete ]; then
        # Ledger and actual recovery objects are distinct diagnostics. Select
        # only a bounded ledger's existing displaced path, JSON-escape it.
        actual_backup=""
        if [ -f "$target_lock.journal" ] && [ ! -L "$target_lock.journal" ] && [ "$(wc -c < "$target_lock.journal")" -le 16384 ]; then
            exec 5< "$target_lock.journal"
            IFS= read -r backup_magic <&5 && IFS= read -r backup_target <&5 && IFS= read -r backup_count <&5 || backup_count=0
            if [ "$backup_magic" = WG-INSTALL-JOURNAL-1 ] && [ "$backup_target" = "$target" ]; then
                case "$backup_count" in 1|6) ;; *) backup_count=0 ;; esac
                backup_row=0
                while [ "$backup_row" -lt "$backup_count" ]; do
                    IFS= read -r backup_live <&5 && IFS= read -r candidate_backup <&5 &&
                    IFS= read -r backup_staged <&5 && IFS= read -r candidate_old_id <&5 && IFS= read -r backup_new_id <&5 || break
                    if [ -n "$candidate_old_id" ] && [ "$(object_id "$candidate_backup" 2>/dev/null)" = "$candidate_old_id" ]; then
                        actual_backup=$candidate_backup
                        break
                    fi
                    backup_row=$((backup_row + 1))
                done
            fi
            exec 5<&-
        fi
        escaped=$(printf '%s' "$actual_backup" | awk '{gsub(/\\/,"\\\\"); gsub(/"/,"\\\""); printf "%s",$0}')
        backup=",\"journalPath\":$journal_json"
        [ -z "$actual_backup" ] || backup="$backup,\"backupPath\":\"$escaped\""
    fi
    [ "${previous_kept:-0}" -ne 1 ] || backup="$backup,\"previousKept\":true"
    [ "$logger_failed" -eq 0 ] || backup="$backup,\"reason\":\"Installer output could not be retained.\""
    printf '%s,"result":"%s","when":"%s"%s}\n' "$metadata" "$verdict" "$when" "$backup" > "$outcome.tmp" && mv -f "$outcome.tmp" "$outcome"
}
fail() { record failed; exit 1; }
object_id() {
    stat -c '%d:%i' -- "$1" 2>/dev/null || stat -f '%d:%i' "$1" 2>/dev/null
}
# Reserve the actual destination before waiting for the old application. This
# closes the gap in which a user could start the old version during mounting.
if [ -e "$target_lock.journal" ] || [ -L "$target_lock.journal" ]; then
    record rollback_incomplete; exit 3
fi
mkdir "$target_lock" 2>/dev/null || fail
target_lock_id=$(object_id "$target_lock") || fail
printf '%s\n' "$$" > "$target_lock/pid" || fail
WG_INSTALLER_HANDOFF_LOCK_ID=$target_lock_id
WG_INSTALLER_HANDOFF_OWNER_PID=$$
WG_INSTALLER_HANDOFF_RECOVERY=$recovery
export WG_INSTALLER_HANDOFF_LOCK_ID WG_INSTALLER_HANDOFF_OWNER_PID WG_INSTALLER_HANDOFF_RECOVERY
# The launcher owns and stops its backend before entering this helper. Wait
# for the window owner too, with a cap, before any installer can rename it.
elapsed=0
while kill -0 "$parent_pid" 2>/dev/null; do
    [ "$elapsed" -lt 120 ] || fail
    sleep 1; elapsed=$((elapsed + 1))
done
# The helper verifies the downloaded bytes again after waiting; its own host
# shell never assumes that an earlier Python hash froze the file forever.
if [ "$platform" = macos-arm64 ]; then
    actual_sha=$(shasum -a 256 "$asset" | awk '{print $1}') || fail
else
    actual_sha=$(sha256sum "$asset" | awk '{print $1}') || fail
fi
[ "$actual_sha" = "$expected_sha" ] || fail
if [ "$platform" = macos-arm64 ]; then
    mount="$payload/mount"
    mkdir "$mount" || fail
    # An explicit fresh mountpoint, no parsed-volume-name fallback.
    hdiutil attach "$asset" -mountpoint "$mount" -nobrowse -readonly -quiet >/dev/null 2>&1 || fail
    [ -f "$mount/Install Waveguide Generator.command" ] && [ ! -L "$mount/Install Waveguide Generator.command" ] || fail
    set -- /bin/sh "$mount/Install Waveguide Generator.command" --update "$target"
elif [ "$platform" = linux-x86_64 ]; then
    [ -f "$payload/install.sh" ] && [ ! -L "$payload/install.sh" ] || fail
    [ "${target##*/}" = waveguide-generator ] || fail
    set -- /bin/bash "$payload/install.sh" --update --prefix "${target%/*}"
else
    fail
fi
prepare_log() {
    [ -f "$recovery/log" ] && [ ! -L "$recovery/log" ] && [ -x "$recovery/log" ] || return 1
    WG_INSTALLER_LOG="$log" "$recovery/log" --check
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

# Use a private FIFO so the installer runs in the foreground. Caught parent
# signals reset to default on exec; async shell SIGINT inheritance never reaches
# the native installer. Defer parent signals through native rollback and drain.
fifo="$lock/output"
mkfifo "$fifo" || fail
trap ':' HUP INT TERM
prepare_log || { log_failure; fail; }
WG_INSTALLER_LOG=$log; export WG_INSTALLER_LOG
log_records=$lock
start_logger || fail
"$@" > "$fifo" 2>&1
code=$?
drain_logger
if logging_settled "$lock"; then
    rm -f "$lock/output" "$lock/logger" "$lock/writer" "$lock/sink"
else
    log_failure
fi
case "$code" in
    0) recovery_decided=1; record installed ;;
    1)
        # Exit 1 alone is not a proof that a raced destination retained the
        # original version. Require the root identity captured before handoff.
        captured_root=$(sed -n '3p' "$recovery/config")
        if [ -n "$captured_root" ] && [ "$(object_id "$target")" = "$captured_root" ]; then
            recovery_decided=1; previous_kept=1; record failed
        else
            record rollback_incomplete; exit 3
        fi ;;
    3) record rollback_incomplete; exit 3 ;;
    *)
        # A hard-killed native installer ran no trap. A separate host-shell
        # entry reconciles exact saved identities before the previous version
        # can be started; ambiguous/live owners preserve all recovery material.
        WG_INSTALLER_RECOVERY_OWNER_PID=$$; export WG_INSTALLER_RECOVERY_OWNER_PID
        if /bin/sh "$recovery/recover.sh" "$target"; then
            recovery_decided=1
        else
            record rollback_incomplete; exit 3
        fi ;;
esac
finish
trap - 0
if [ "$platform" = macos-arm64 ]; then
    open "$target" >/dev/null 2>&1 || exit 1
else
    "$target/waveguide-generator" >/dev/null 2>&1 </dev/null &
fi
exit 0
