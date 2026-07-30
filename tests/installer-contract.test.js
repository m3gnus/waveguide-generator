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
  assert.match(windowsInstaller, /call install\\install\.bat --after-pull/);
});
