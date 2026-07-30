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
  assert.match(shellInstaller, /sys\.prefix != sys\.base_prefix/);
  assert.match(windowsInstaller, /sys\.prefix != sys\.base_prefix/);
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
