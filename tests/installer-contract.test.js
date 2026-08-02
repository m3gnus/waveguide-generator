import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const shellInstaller = fs.readFileSync(path.join(rootDir, 'install', 'install.sh'), 'utf8');
const windowsInstaller = fs.readFileSync(path.join(rootDir, 'install', 'install.bat'), 'utf8');

test('installers enforce the runtime prerequisites they consume', () => {
  assert.match(shellInstaller, /Node\.js 20\.19 or newer is required/);
  assert.match(windowsInstaller, /Node\.js 20\.19 or newer is required/);
  assert.match(shellInstaller, /Git is required to install pinned backend dependencies/);
  assert.match(windowsInstaller, /Git is required to install pinned backend dependencies/);
  assert.match(shellInstaller, /package-lock\.json/);
  assert.match(windowsInstaller, /package-lock\.json/);
  assert.doesNotMatch(shellInstaller, /Falling back to npm install/);
  assert.doesNotMatch(windowsInstaller, /Falling back to npm install/);
});

test('installers preserve and replace an invalid virtual environment', () => {
  assert.match(shellInstaller, /\.venv\.incompatible/);
  assert.match(windowsInstaller, /\.venv\.incompatible/);

  // Both installers delegate to install/check_venv.py. Windows must, because
  // cmd.exe mis-parsed the inline form inside a parenthesised block; the shell
  // installer does too so the supported-Python rule has one definition rather
  // than drifting between the two scripts.
  const checkVenv = fs.readFileSync(
    new URL('../install/check_venv.py', import.meta.url),
    'utf8'
  );
  assert.match(checkVenv, /sys\.prefix != sys\.base_prefix/);
  assert.match(windowsInstaller, /check_venv\.py/);
  assert.match(shellInstaller, /check_venv\.py/);
  assert.doesNotMatch(shellInstaller, /sys\.prefix != sys\.base_prefix/);
});

test('both installers keep at most one incompatible venv backup', () => {
  // A timestamped backup name accumulated multi-hundred-MB directories on every
  // run. Both installers now reuse one name and sweep the old scheme's leftovers.
  for (const installer of [shellInstaller, windowsInstaller]) {
    assert.match(installer, /\.venv\.incompatible\.\*/);
    assert.doesNotMatch(installer, /venv\.incompatible\.\$\(date/);
    assert.doesNotMatch(installer, /venv\.incompatible\.!RANDOM!/);
  }
});

test('both installers verify a solve can run, not just that imports succeed', () => {
  // Counting OpenCL devices reported "bempp ready with OpenCL acceleration" on
  // hosts where every solve then failed. check_solver_engine.py imports the
  // engine the solve path actually uses.
  for (const installer of [shellInstaller, windowsInstaller]) {
    assert.match(installer, /check_solver_engine\.py/);
    assert.doesNotMatch(installer, /bempp ready with OpenCL acceleration/);
  }
});

test('a solver readiness check is reachable without an installer run', () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.join(rootDir, 'package.json'), 'utf8')
  );
  assert.match(pkg.scripts['check:solver'], /check_solver_engine\.py/);
});

test('Metal helper build runs before solve-backend selection', () => {
  for (const installer of [shellInstaller, windowsInstaller]) {
    assert.ok(
      installer.indexOf('Building Metal native release helper when available') <
        installer.indexOf('Checking Metal BEM backend')
    );
  }
});

test('strict preflight failures are fatal before the completion banner', () => {
  assert.match(
    shellInstaller,
    /Backend preflight detected missing\/unsupported required checks[\s\S]*?exit 1[\s\S]*?Install \/ update complete/
  );
  assert.match(
    windowsInstaller,
    /Backend preflight detected missing\/unsupported required checks[\s\S]*?exit \/b 1[\s\S]*?Install \/ update complete/
  );
});

test('updated code restarts through the freshly pulled installer', () => {
  assert.match(shellInstaller, /WAVEGUIDE_INSTALL_AFTER_PULL=1 exec bash/);

  // The Windows installer must NOT re-exec itself in place. `git pull` rewrites
  // install.bat, and cmd.exe re-reads a running batch file by byte offset, so
  // the old `call install\install.bat --after-pull` resumed at a meaningless
  // offset and executed fragments of unrelated lines. install.bat now exits 10
  // and install-and-update.bat relaunches from a fresh %TEMP% copy.
  assert.doesNotMatch(
    windowsInstaller,
    /call install\\install\.bat --after-pull/,
    'install.bat must not call itself after a pull; it has already been '
      + 'overwritten and cmd.exe has lost its place in the file.'
  );
  assert.match(
    windowsInstaller,
    /exit \/b 10/,
    'install.bat should exit 10 to request a relaunch by install-and-update.bat.'
  );
  assert.match(
    windowsInstaller,
    /--after-pull/,
    'install.bat must still accept --after-pull when relaunched.'
  );
});

test('windows relauncher passes paths to PowerShell through the environment', () => {
  const relauncher = fs.readFileSync(
    path.join(rootDir, 'install', 'install-and-update.bat'),
    'utf8'
  );
  for (const name of ['WG_TMP_INSTALLER', 'WG_ROOT', 'WG_LOG']) {
    assert.match(relauncher, new RegExp(`\\$env:${name}`));
    assert.doesNotMatch(relauncher, new RegExp(`'%${name}%'`));
  }
  assert.match(relauncher, /Tee-Object[\s\S]*exit \$LASTEXITCODE/);
});

test('windows installer probes the venv with a script, not an inline -c', () => {
  // A `python -c "...(3,10) <= sys.version_info[:2] < (3,15)..."` probe inside a
  // parenthesised cmd block was mis-parsed: it reported failure while the same
  // command exited 0 at the prompt. The installer then discarded a healthy
  // .venv on every run, rebuilt it (~3 min), and left a backup behind each
  // time. Reruns went from 195s to 58s once this moved into a file.
  assert.match(
    windowsInstaller,
    /check_venv\.py/,
    'install.bat must validate .venv via install\\check_venv.py'
  );
  assert.doesNotMatch(
    windowsInstaller,
    /python\.exe -c "import sys; sys\.exit\(0 if sys\.prefix/,
    'the inline venv probe is mis-parsed by cmd inside a block; use the script'
  );
});

test('windows installer keeps at most one incompatible venv backup', () => {
  // The old scheme appended !RANDOM!!RANDOM! and never cleaned up, so repeated
  // runs accumulated multi-hundred-MB directories. Four had piled up.
  assert.doesNotMatch(
    windowsInstaller,
    /\.venv\.incompatible\.!RANDOM!/,
    'do not create a uniquely-named backup on every run'
  );
  assert.match(windowsInstaller, /rd \/s \/q "\.venv\.incompatible"/);
  assert.match(
    windowsInstaller,
    /for \/d %%d in \("\.venv\.incompatible\.\*"\)/,
    'stale backups from the old naming scheme should be swept up'
  );
});

test('windows installer skips Microsoft Store python execution aliases', () => {
  assert.match(
    windowsInstaller,
    /WindowsApps/,
    'install.bat must recognise Store execution aliases'
  );
  assert.match(
    windowsInstaller,
    /STORE_ALIAS_SEEN/,
    'install.bat should tell the user when it skipped a Store alias'
  );
});

test('windows installers never expand a path variable inside a block', () => {
  // cmd.exe expands %VAR% when it PARSES a parenthesised block, before running
  // it. A path containing ')' therefore closes the block early. Verified with a
  // real install into "C:\...\Hörnlab – Wörkspace (tëst)\wg": the installer died
  // in 1.2s with "\wg was unexpected at this time." before printing anything.
  // Delayed expansion (!VAR!) defers substitution to execution time and is
  // immune. Quoted "%VAR%" is also safe, because the quotes survive parsing.
  const pathVars = ['WG_ROOT', 'WG_LOG', 'CD', 'TEMP', 'WG_TMP_INSTALLER'];

  for (const [name, source] of [
    ['install.bat', windowsInstaller],
    ['install-and-update.bat', fs.readFileSync(
      new URL('../install/install-and-update.bat', import.meta.url), 'utf8')],
  ]) {
    for (const line of source.split(/\r?\n/)) {
      if (!/^\s+/.test(line)) continue;          // only indented (in-block) lines
      if (/^\s*(rem|::)/i.test(line)) continue;  // comments are inert
      for (const v of pathVars) {
        // Safe forms: "%VAR%" (cmd quoting survives the parse) and PowerShell's
        // $env:VAR form, which prevents cmd from expanding the path at all.
        // Unsafe: a bare %VAR% that the parser expands before the block runs.
        const bare = new RegExp(`(^|[^"'%])%${v}%([^"']|$)`);
        assert.equal(
          bare.test(line),
          false,
          `${name}: unquoted %${v}% inside a block breaks on paths containing `
            + `')'. Use !${v}! or quote it.\n  ${line.trim()}`
        );
      }
    }
  }
});

test('windows launcher calls npm.cmd so failures stay visible', () => {
  const launcher = fs.readFileSync(
    new URL('../launch/windows.bat', import.meta.url),
    'utf8'
  );

  // Without `call`, control transfers to npm.cmd permanently and the `pause`
  // below never runs, so a double-clicked launcher closes instantly on error.
  assert.match(launcher, /call npm\.cmd start/);
  assert.match(launcher, /pause/);
});

test('windows entry point runs the installer from a copy outside the repo', () => {
  const entryPoint = fs.readFileSync(
    new URL('../install/install-and-update.bat', import.meta.url),
    'utf8'
  );

  // Staging into %TEMP% is what makes the update safe: git may then rewrite
  // install.bat freely, because the executing file is not inside the repo.
  assert.match(entryPoint, /%TEMP%/, 'entry point must stage the installer in %TEMP%');
  assert.match(entryPoint, /copy \/y/i, 'entry point must copy the installer before running it');
  assert.match(entryPoint, /--root/, 'the staged copy needs the repository root passed explicitly');
  assert.match(entryPoint, /"10"/, 'entry point must handle the relaunch exit code');
});
