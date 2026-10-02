#!/usr/bin/env python3
"""Mutation-check installer recovery with an unmutated control per property.

Run with the test venv's Python, through the suite broker. Each pytest invocation
uses disposable installer copies; source scripts and the Git index stay untouched.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEST = 'scripts/tests/test_installer_review_followups.py'
SCRIPTS = {'linux': 'installers/linux/bundle-install.sh', 'macos': 'installers/macos/dmg-install.command',
           'bundle': 'scripts/build_bundle.py'}

# name, selected behavioral test, exact source change
MUTATIONS = [
    ('handler_exits', 'signal_at_cleanup_entry and failure',
     'trap \'INTERRUPTED=1; trap "" HUP INT TERM QUIT\' HUP INT TERM QUIT', "trap 'exit 1' HUP INT TERM QUIT"),
    ('cleanup_not_idempotent', 'cleanup_is_safe_when_reentered',
     '[ "$CLEANING" -eq 0 ] || return 0', '[ "$CLEANING" -eq 0 ] || exit 1'),
    ('refusal_modifies_lock', 'unverifiable_lock and unexpected_contents',
     'lock_busy() {', 'lock_busy() {\n    rm -f "$LOCK_PATH/pid"'),
    ('lock_pid_not_removed', 'group_signals_during_lock_release and 0',
     'run_housekeeping rm -f "$LOCK_PATH/pid"', ': # owner removal disabled'),
    ('lock_acquisition_unprotected', 'group_signals_during_resource_registration and lock_mkdir',
     'if ! run_housekeeping mkdir "$LOCK_PATH"', 'if ! mkdir "$LOCK_PATH"'),
    ('reservation_unconditionally_deleted', 'foreign_backup_reservation',
     'same_object "${BACKUP[i]}" "${RESERVATION_ID[i]}" && run_housekeeping rmdir "${BACKUP[i]}" || fail "The rollback reservation is occupied or replaced: ${BACKUP[i]}"',
     'rm -rf -- "${BACKUP[i]}"'),
    ('move_sigpipe_ignored', 'move_children_have_default_sigpipe',
     'else\n            trap - HUP INT TERM QUIT PIPE', 'else\n            trap - HUP INT TERM QUIT'),
    ('application_sigpipe_ignored', 'launched_application_has_default_sigpipe',
     '(trap - HUP INT TERM QUIT PIPE; exec "$TARGET/$LAUNCHER_NAME")', '(trap - HUP INT TERM QUIT; exec "$TARGET/$LAUNCHER_NAME")'),
    ('application_inherits_cleanup_signals', 'launched_application_keeps_hup_term',
     '(trap - HUP INT TERM QUIT PIPE; exec "$TARGET/$LAUNCHER_NAME")', '(trap - PIPE; exec "$TARGET/$LAUNCHER_NAME")'),
    ('missing_prefix_created', 'missing_update_prefix_creates_nothing',
     '[ "$UPDATE" -eq 1 ] && [ ! -d "$TARGET_PARENT" ]', '[ "$UPDATE" -eq 2 ] && [ ! -d "$TARGET_PARENT" ]'),
    ('later_forward_move_unbounded', 'later_linux_forward_deadlines and 5 and install and False',
     'sleep 30 >/dev/null 2>&1 &', 'sleep 60 >/dev/null 2>&1 &'),
    ('cleanup_parent_interruptible', 'cleanup_ignores_parent_signals',
     "    # forking: Bash 3.2 may resend a pending trapped signal in a new child.\n    trap '' HUP INT TERM QUIT",
     "    # forking: Bash 3.2 may resend a pending trapped signal in a new child.\n    trap 'INTERRUPTED=1' HUP INT TERM QUIT"),
    ('cleanup_mover_interruptible', 'cleanup_mover_inherits_ignored_signals',
     "if [ \"$CLEANING\" -eq 1 ]; then\n            trap '' HUP INT TERM QUIT",
     "if [ \"$CLEANING\" -eq 1 ]; then\n            trap - HUP INT TERM QUIT"),
    ('cancelled_watchdog_unbounded', 'cancelled_watchdog_has_bounded_reap',
     'reap_cancelled_watchdog "$watchdog_pid"', 'while kill -0 "$watchdog_pid" 2>/dev/null; do sleep 0.01; done; wait "$watchdog_pid"'),
    ('long_step_no_kill', 'term_ignoring_long_step',
     'kill -KILL "$step_pid" 2>/dev/null || :', ': # escalation disabled'),
    ('fifo_owner_opened', 'fifo_lock_owner_refuses or fifo_owner_is_never_opened',
     '[ -f "$LOCK_PATH/pid" ] && [ ! -L "$LOCK_PATH/pid" ]', ':'),
    ('preflight_foreground', 'preflight_pid_only_term',
     'run_interruptible --capture-output "$PREFLIGHT_CAPTURE/output" "$SOURCE/runtime/bin/python3.13" -c \'import gmsh\'',
     '"$SOURCE/runtime/bin/python3.13" -c \'import gmsh\' > "$PREFLIGHT_CAPTURE/output" 2>&1'),
    ('postcommit_early_exit', 'signal_just_after_commit',
     "# Once committed, finish the success message and removal of this run's backups.\ntrap '' HUP INT TERM QUIT",
     "# Once committed, finish the success message and removal of this run's backups.\nif [ \"$INTERRUPTED\" -ne 0 ]; then exit 0; fi"),
    ('quit_untrapped', 'quit_during_swap',
     'trap \'INTERRUPTED=1; trap "" HUP INT TERM QUIT\' HUP INT TERM QUIT',
     'trap \'INTERRUPTED=1; trap "" HUP INT TERM QUIT\' HUP INT TERM'),
    ('optional_tool_escapes_group', 'optional_step_stays and parent and None',
     '(exec "$TARGET/runtime/bin/python3.13" -c', 'set -m\n    (exec "$TARGET/runtime/bin/python3.13" -c'),
    ('captured_message_clock_is_sibling', 'bare_captured_message_clock',
     '    OUTPUT_CAPTURE=1', '    OUTPUT_CAPTURE=0'),
    ('reaping_interrupts_locked_diagnostic', 'bare_bash32_wait_diagnostic and False',
     '    if [ -n "${BASH_VERSION:-}" ]; then\n        # jobs only reports cached states. A deferred SIGCHLD can leave a\n        # zombie visible to kill -0 forever. A foreground no-op makes Bash\n        # reap pending children outside the interruptible wait builtin.\n        reap_polls=0\n        while kill -0 "$1" 2>/dev/null; do\n            jobs >/dev/null\n            reap_polls=$((reap_polls + 1))\n            if [ "$reap_polls" -ge 32 ]; then\n                (trap - 0; :)\n                reap_polls=0\n            fi\n        done\n        jobs >/dev/null\n    fi', ': # dead-child notification guard disabled'),
    ('reaping_requires_another_notification', 'bare_bash32_wait_reaps_a_deferred_zombie',
     '                (trap - 0; :)', '                : # proactive reap disabled'),
    ('cancellation_does_not_latch_ignore', 'interrupted_capture_inherits_ignore',
     'trap \'INTERRUPTED=1; trap "" HUP INT TERM QUIT\' HUP INT TERM QUIT',
     "trap 'INTERRUPTED=1' HUP INT TERM QUIT"),
    ('file_lock_wrong_command', 'printed_lock_command and file',
     '[ -L "$LOCK_PATH" ] || [ ! -d "$LOCK_PATH" ]', '[ -L "$LOCK_PATH" ]'),
    ('restore_signal_not_retried', 'signal_killed_restore_and_message',
     'case "$recovery_status" in 129|130|131|143) ;; *) return "$recovery_status" ;; esac',
     'return "$recovery_status"'),
    ('message_signal_not_retried', 'signal_killed_restore_and_message',
     '        [ "$print_status" -gt 128 ] || break\n    done', '        break\n    done'),
    ('cleanup_total_budget_missing', 'shared_budget_bounds_repeated_restore_timeouts',
     '[ -n "$WORK_CLOCK" ] && ! kill -0 "$WORK_CLOCK" 2>/dev/null', '[ -n "$WORK_CLOCK" ] && false'),
    ('forward_steps_use_cleanup_deadline', 'slow_forward_primitives and (metadata or housekeeping)',
     'then bound_seconds=30; fi', 'then bound_seconds=1; fi'),
    ('forward_messages_use_cleanup_deadline', 'slow_forward_primitives and (message-file or render or emit or message-remove)',
     '    print_seconds=30', '    print_seconds=1'),
    ('forward_moves_use_cleanup_deadline', 'slow_forward_primitives and move',
     'sleep 30 >/dev/null 2>&1 &', 'sleep 1 >/dev/null 2>&1 &'),
    ('forward_cancellation_waits_whole_deadline', 'forward_move_cancellation_keeps_short_grace',
     'if [ "$bound_seconds" -eq 30 ]; then', 'if [ "$bound_seconds" -eq 0 ]; then'),
    ('prompt_signal_exits_before_printer_reap', 'close_prompt_signal_reaps_emitter',
     'trap \'prompt_cancelled=1; trap "" HUP INT TERM QUIT\' HUP INT TERM QUIT',
     'trap \'exit "$status"\' HUP INT TERM QUIT'),
    ('watchdog_clock_not_reaped_by_parent', 'cancelled_watchdog_has_bounded_reap',
     '    kill -KILL "$move_clock_pid" 2>/dev/null || :\n    wait "$move_clock_pid" 2>/dev/null || :',
     '    : # parent clock cleanup disabled'),
    ('watchdog_early_cancellation_unprotected', 'watchdog_cancellation_before_registration',
     "    trap '' USR1\n    (", "    trap - USR1\n    ("),
    ('deadlines_count_polls', 'blocked_commands_have_deadlines and commit-cache and parent',
     'if ! kill -0 "$bound_clock" 2>/dev/null ||', 'if [ "$bound_fast" -ge 100 ] ||'),
    ('housekeeping_unbounded', 'blocked_commands_have_deadlines and cleanup-rm and parent',
     'wait_for_child "$housekeeping_pid" 3 budgeted "$*"',
     'while kill -0 "$housekeeping_pid" 2>/dev/null; do sleep 0.01; done; wait "$housekeeping_pid"'),
    ('stat_unbounded', 'blocked_commands_have_deadlines and restore-stat and parent',
     'wait_for_child "$output_pid" 3 "${OUTPUT_BOUND:-budgeted}" "$*"',
     'while kill -0 "$output_pid" 2>/dev/null; do sleep 0.01; done; wait "$output_pid"'),
    ('cache_refresh_unbounded', 'blocked_commands_have_deadlines and commit-cache and parent',
     'run_optional update-desktop-database "$APPLICATIONS"', 'update-desktop-database "$APPLICATIONS"'),
    ('printer_emits_while_rendering', 'printer_retries and render-after-write',
     '(command printf "$@") > "$print_file"', '(command printf "$@") | tee "$print_file"'),
    ('prompt_ignores_signals', 'close_prompt_cancellation',
     "trap 'exit \"$status\"' HUP INT TERM QUIT", "trap '' HUP INT TERM QUIT"),
    ('gatekeeper_header_disallows_app', 'gatekeeper_header',
     'Both the app and this script', 'Only this script'),
    ('write_failure_blames_account', 'unwritable_parent',
     '${LOCK_PATH%/*} cannot be written.', '${LOCK_PATH%/*} is not writable by this account.'),
    ('staging_not_documented', 'cleanup_documentation',
     'safe to delete', 'left behind'),
    ('terminal_close_documented_as_crash', 'cleanup_documentation',
     'a crash, power loss or a forced quit', 'a crash, power loss or a closed Terminal window'),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Directory for controls, mutants, logs and result table')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name, selected, old, new in MUTATIONS:
        row = {'property': name, 'selection': selected}
        for mode in ('control', 'mutant'):
            directory = args.output / name / mode
            directory.mkdir(parents=True, exist_ok=True)
            changed = 0
            for platform, filename in SCRIPTS.items():
                body = (ROOT / filename).read_text()
                if mode == 'mutant' and old in body:
                    assert body.count(old) == 1 or platform == 'bundle', (name, filename)
                    body = body.replace(old, new)
                    if name == 'deadlines_count_polls':
                        body = body.replace('            sleep 0.01\n', '            sleep 0.01\n            bound_fast=$((bound_fast + 1))\n')
                    if name == 'later_forward_move_unbounded':
                        body = body.replace('then bound_seconds=30; fi', 'then bound_seconds=60; fi')
                    changed += 1
                suffix = 'py' if platform == 'bundle' else 'sh'
                (directory / f'{platform}.{suffix}').write_text(body)
                if platform != 'bundle':
                    shell = '/bin/sh' if platform == 'macos' else '/bin/bash'
                    subprocess.run([shell, '-n', str(directory / f'{platform}.sh')], check=True, capture_output=True)
            if mode == 'mutant':
                assert changed, name
            # pytest_configure imports the exact fixture modules and redirects
            # their source constants before collection/fixture construction.
            (directory / 'installer_mutant.py').write_text('''import os
from pathlib import Path

def pytest_configure(config):
    from scripts.tests import test_dmg_install_update as mac
    from scripts.tests import test_linux_bundle_install_update as linux
    root = Path(os.environ['INSTALLER_MUTANT_ROOT'])
    mac.SCRIPT = root / 'macos.sh'
    linux.SCRIPT = root / 'linux.sh'
    from scripts import build_bundle
    exec(compile((root / 'bundle.py').read_text(), str(root / 'bundle.py'), 'exec'), build_bundle.__dict__)
''')
            # Every selected case, not the default sample (scripts/tests/conftest.py).
            env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'WG_INSTALLER_ALL_CASES': '1', 'INSTALLER_MUTANT_ROOT': str(directory.resolve()),
                   'PYTHONPATH': str(directory.resolve()) + os.pathsep + str(ROOT)}
            command = [sys.executable, 'scripts/run_tests.py', TEST, '-k', selected, '-q', '-p', 'no:cacheprovider', '-p', 'installer_mutant']
            with (directory / 'pytest.log').open('w') as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            output = (directory / 'pytest.log').read_text()
            row[mode] = {'exit': result.returncode, 'log': str((directory / 'pytest.log').relative_to(args.output)),
                         'summary': output.splitlines()[-1] if output.splitlines() else 'no output'}
            print(f'{name} {mode}: {row[mode]["summary"]}', flush=True)
        # A collection/harness error is not a detected mutation.
        row['caught'] = row['control']['exit'] == 0 and row['mutant']['exit'] == 1 and 'failed' in row['mutant']['summary']
        records.append(row)
        (args.output / 'results.json').write_text(json.dumps(records, indent=2) + '\n')
    table = '| Property | CONTROL | Mutated | Caught |\n| --- | --- | --- | --- |\n'
    for row in records:
        table += f'| {row["property"]} | {row["control"]["summary"]} | {row["mutant"]["summary"]} | {row["caught"]} |\n'
    (args.output / 'results.md').write_text(table)
    print(table, flush=True)
    return 0 if all(row['caught'] for row in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
