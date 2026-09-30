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
SCRIPTS = {'linux': 'installers/linux/bundle-install.sh', 'macos': 'installers/macos/dmg-install.command'}

# name, selected behavioral test, exact source change
MUTATIONS = [
    ('handler_exits', 'signal_at_cleanup_entry and failure',
     "trap 'INTERRUPTED=1' HUP INT TERM", "trap 'exit 1' HUP INT TERM"),
    ('cleanup_not_idempotent', 'cleanup_is_safe_when_reentered',
     '[ "$CLEANING" -eq 0 ] || return 0', '[ "$CLEANING" -eq 0 ] || exit 1'),
    ('refusal_modifies_lock', 'unverifiable_lock and unexpected_contents',
     'lock_busy() {', 'lock_busy() {\n    rm -f "$LOCK_PATH/pid"'),
    ('lock_pid_not_removed', 'group_signals_during_lock_release and 0',
     'run_housekeeping rm -f "$LOCK_PATH/pid"', ': # owner removal disabled'),
    ('lock_acquisition_unprotected', 'group_signals_during_resource_registration and lock_mkdir',
     'if ! run_housekeeping mkdir "$LOCK_PATH"', 'if ! mkdir "$LOCK_PATH"'),
    ('reservation_unconditionally_deleted', 'foreign_backup_reservation',
     'same_object "${BACKUP[i]}" "${RESERVATION_ID[i]}" && rmdir "${BACKUP[i]}" || fail "The rollback reservation is occupied or replaced: ${BACKUP[i]}"',
     'rm -rf -- "${BACKUP[i]}"'),
    ('move_sigpipe_ignored', 'move_children_have_default_sigpipe',
     'trap - HUP INT TERM PIPE', 'trap - HUP INT TERM'),
    ('application_sigpipe_ignored', 'launched_application_has_default_sigpipe',
     '(trap - PIPE; exec "$TARGET/$LAUNCHER_NAME")', '(exec "$TARGET/$LAUNCHER_NAME")'),
    ('missing_prefix_created', 'missing_update_prefix_creates_nothing',
     '[ "$UPDATE" -eq 1 ] && [ ! -d "$TARGET_PARENT" ]', '[ "$UPDATE" -eq 2 ] && [ ! -d "$TARGET_PARENT" ]'),
    ('later_forward_move_unbounded', 'later_linux_forward_deadlines and 5 and install and False',
     'sleep 5 &', 'sleep 60 &'),
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
                    assert body.count(old) == 1, (name, filename)
                    body = body.replace(old, new, 1)
                    changed += 1
                (directory / f'{platform}.sh').write_text(body)
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
''')
            env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'INSTALLER_MUTANT_ROOT': str(directory.resolve()),
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
