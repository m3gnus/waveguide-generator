import test from 'node:test';
import assert from 'node:assert/strict';

import {
  getSelectedFolderLabel,
  selectOutputFolder,
  subscribeFolderWorkspace,
} from '../src/ui/workspace/folderWorkspace.js';

test('selectOutputFolder uses backend folder selection', async () => {
  const originalFetch = global.fetch;
  const updates = [];
  const unsubscribe = subscribeFolderWorkspace((snapshot) => updates.push(snapshot));

  global.fetch = async (url, options) => {
    if (url.includes('/api/workspace/select') && options?.method === 'POST') {
      return {
        ok: true,
        async json() {
          return { selected: true, path: '/Users/test/exports' };
        },
      };
    }
    throw new Error('unexpected fetch');
  };

  try {
    const selected = await selectOutputFolder();

    assert.equal(selected, '/Users/test/exports');
    assert.equal(getSelectedFolderLabel(), 'exports');
    assert.ok(updates.length >= 2);
  } finally {
    unsubscribe();
    global.fetch = originalFetch;
  }
});

test('folder workspace listener failures do not write normal-runtime console warnings', async () => {
  const originalFetch = global.fetch;
  const originalDebug = globalThis.__WAVEGUIDE_DEBUG__;
  const originalWarn = console.warn;
  const warnings = [];
  let armed = false;
  const unsubscribe = subscribeFolderWorkspace(() => {
    if (!armed) {
      return;
    }
    throw new Error('listener failure');
  });

  globalThis.__WAVEGUIDE_DEBUG__ = false;
  console.warn = (...args) => {
    warnings.push(args);
  };
  global.fetch = async () => ({
    ok: true,
    async json() {
      return { selected: true, path: '/Users/test/other-exports' };
    },
  });

  try {
    armed = true;
    await selectOutputFolder();
  } finally {
    unsubscribe();
    global.fetch = originalFetch;
    console.warn = originalWarn;
    if (typeof originalDebug === 'undefined') {
      delete globalThis.__WAVEGUIDE_DEBUG__;
    } else {
      globalThis.__WAVEGUIDE_DEBUG__ = originalDebug;
    }
  }

  assert.deepEqual(warnings, []);
});

test('selectOutputFolder returns null when backend selection is cancelled', async () => {
  const originalFetch = global.fetch;

  global.fetch = async (url, options) => {
    if (url.includes('/api/workspace/select') && options?.method === 'POST') {
      return {
        ok: true,
        async json() {
          return { selected: false };
        },
      };
    }
    throw new Error('unexpected fetch');
  };

  try {
    const selected = await selectOutputFolder();
    assert.equal(selected, null);
  } finally {
    global.fetch = originalFetch;
  }
});
