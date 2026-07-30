import assert from 'node:assert/strict';
import test from 'node:test';

import { checkForUpdates, getInstallUpdateCommand } from '../src/app/updates.js';

function response(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    async json() {
      return payload;
    },
  };
}

async function withGlobals(values, callback) {
  const originals = new Map();
  for (const [key, value] of Object.entries(values)) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    });
  }

  try {
    return await callback();
  } finally {
    for (const [key, descriptor] of originals) {
      if (descriptor) {
        Object.defineProperty(globalThis, key, descriptor);
      } else {
        delete globalThis[key];
      }
    }
  }
}

test('getInstallUpdateCommand returns the documented platform entry point', () => {
  assert.equal(getInstallUpdateCommand('Win32'), 'install\\install-and-update.bat');
  assert.equal(getInstallUpdateCommand('macOS'), 'bash install/install-and-update.sh');
  assert.equal(getInstallUpdateCommand('Linux x86_64'), 'bash install/install-and-update.sh');
});

test('available update recommends the full updater instead of raw git pull', async () => {
  const suggestions = [];
  const messages = [];

  await withGlobals(
    {
      navigator: { platform: 'Linux x86_64' },
      fetch: async (_url, options) => {
        assert.equal(options.method, 'POST');
        return response({
          behindCount: 2,
          aheadCount: 0,
          upstreamRef: 'origin/main',
          currentCommit: '1111111111111111111111111111111111111111',
          remoteCommit: '2222222222222222222222222222222222222222',
        });
      },
    },
    async () => {
      await checkForUpdates(null, {
        async showCommandSuggestion(value) {
          suggestions.push(value);
          return false;
        },
        showMessage(message, options) {
          messages.push({ message, options });
        },
      });
    }
  );

  assert.equal(suggestions.length, 1);
  assert.equal(suggestions[0].command, 'bash install/install-and-update.sh');
  assert.doesNotMatch(suggestions[0].command, /git pull/);
  assert.match(suggestions[0].subtitle, /Stop the running app/);
  assert.match(messages[0].message, /full updater|Stop the app/i);
});

test('diverged checkout does not recommend a fast-forward update', async () => {
  const suggestions = [];
  const messages = [];

  await withGlobals(
    {
      fetch: async (_url, options) => {
        assert.equal(options.method, 'POST');
        return response({
          behindCount: 3,
          aheadCount: 1,
          upstreamRef: 'origin/feature/work',
          currentCommit: '1111111',
          remoteCommit: '2222222',
        });
      },
    },
    async () => {
      await checkForUpdates(null, {
        showCommandSuggestion(value) {
          suggestions.push(value);
        },
        showMessage(message, options) {
          messages.push({ message, options });
        },
      });
    }
  );

  assert.equal(suggestions.length, 0);
  assert.equal(messages[0].options.type, 'warning');
  assert.match(messages[0].message, /diverged/);
});
