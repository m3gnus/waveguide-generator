import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';

/**
 * Regression tests for a clean-machine Windows install failure.
 *
 * install/install.bat used to do `set "PYTHON_BIN=py"` to remember which
 * launcher it had picked. In cmd.exe every `set` creates an *environment*
 * variable, so PYTHON_BIN was inherited by the `node scripts/*.js` children the
 * installer spawns at the end. scripts/backend-python.js honors env.PYTHON_BIN
 * ahead of the .waveguide/backend-python.path marker, so the final strict
 * preflight audited the system Python instead of the .venv the installer had
 * just built, reported every dependency MISSING, and aborted a fully working
 * install with exit code 1.
 *
 * install/install.sh is not affected: a bare `VAR=value` assignment in bash is
 * shell-local and is not exported to children. This is a Windows-only hazard,
 * which is exactly why it needs a test rather than a code review habit.
 */

const INSTALL_BAT = new URL('../install/install.bat', import.meta.url);
const BACKEND_PYTHON_JS = new URL('../scripts/backend-python.js', import.meta.url);

/** Environment variables that override backend interpreter selection. */
const INTERPRETER_OVERRIDE_ENV_VARS = ['PYTHON_BIN', 'WG_BACKEND_PYTHON'];

test('backend-python.js honors exactly the documented interpreter override vars', () => {
  const source = fs.readFileSync(BACKEND_PYTHON_JS, 'utf8');
  const honored = [...source.matchAll(/env\?\.([A-Z0-9_]+)/g)].map((m) => m[1]);

  assert.deepEqual(
    [...new Set(honored)].sort(),
    [...INTERPRETER_OVERRIDE_ENV_VARS].sort(),
    'If a new interpreter override env var is added, add it to '
      + 'INTERPRETER_OVERRIDE_ENV_VARS so the installer is checked against it too.'
  );
});

test('install.bat does not export any interpreter override env var', () => {
  const source = fs.readFileSync(INSTALL_BAT, 'utf8');

  for (const name of INTERPRETER_OVERRIDE_ENV_VARS) {
    // Matches both `set NAME=` and `set "NAME=`, case-insensitively, because
    // cmd.exe variable names are case-insensitive.
    const assignment = new RegExp(`^\\s*set\\s+"?${name}=`, 'im');
    assert.equal(
      assignment.test(source),
      false,
      `install.bat assigns ${name}, which cmd.exe exports to child processes. `
        + 'That silently overrides the .waveguide/backend-python.path marker in '
        + 'scripts/backend-python.js and makes the final preflight audit the '
        + 'wrong interpreter. Use a differently named variable (WG_SETUP_PYTHON).'
    );
  }
});

test('install.bat still records the venv interpreter marker it just built', () => {
  const source = fs.readFileSync(INSTALL_BAT, 'utf8');

  assert.match(
    source,
    /backend-python\.path/,
    'install.bat must write the .waveguide/backend-python.path marker so the '
      + 'backend resolver can find the venv it created.'
  );
  assert.match(
    source,
    /WG_SETUP_PYTHON/,
    'install.bat should track its chosen bootstrap interpreter in '
      + 'WG_SETUP_PYTHON, a name the backend resolver deliberately ignores.'
  );
});
