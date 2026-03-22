import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ensureFolderWritePermission,
  getSelectedFolderHandle,
  getSelectedFolderLabel,
  requestFolderSelection,
  resetSelectedFolder,
  setSelectedFolderHandle,
  subscribeFolderWorkspace,
  supportsFolderSelection
} from '../src/ui/workspace/folderWorkspace.js';

test('supportsFolderSelection always returns false (File System Access API removed)', () => {
  assert.equal(supportsFolderSelection({ showDirectoryPicker: async () => ({}) }), false);
  assert.equal(supportsFolderSelection({}), false);
  assert.equal(supportsFolderSelection(null), false);
});

test('requestFolderSelection delegates to backend folder selection', async () => {
  const originalFetch = global.fetch;
  resetSelectedFolder();
  const updates = [];
  const unsubscribe = subscribeFolderWorkspace((snapshot) => updates.push(snapshot));

  global.fetch = async (url, options) => {
    if (url.includes('/api/workspace/select') && options?.method === 'POST') {
      return {
        ok: true,
        async json() {
          return { selected: true, path: '/Users/test/exports' };
        }
      };
    }
    throw new Error('unexpected fetch');
  };

  try {
    const selected = await requestFolderSelection();

    assert.equal(selected, '/Users/test/exports');
    assert.equal(getSelectedFolderHandle(), null);
    assert.equal(getSelectedFolderLabel(), 'exports');
    assert.ok(updates.length >= 2);
  } finally {
    unsubscribe();
    resetSelectedFolder();
    global.fetch = originalFetch;
  }
});

test('requestFolderSelection returns null when backend selection is cancelled', async () => {
  const originalFetch = global.fetch;

  global.fetch = async (url, options) => {
    if (url.includes('/api/workspace/select') && options?.method === 'POST') {
      return {
        ok: true,
        async json() {
          return { selected: false };
        }
      };
    }
    throw new Error('unexpected fetch');
  };

  try {
    const selected = await requestFolderSelection();
    assert.equal(selected, null);
  } finally {
    global.fetch = originalFetch;
  }
});

test('ensureFolderWritePermission always returns false (File System Access API removed)', async () => {
  const result1 = await ensureFolderWritePermission({
    async queryPermission() {
      return 'granted';
    }
  });
  assert.equal(result1, false);

  const result2 = await ensureFolderWritePermission();
  assert.equal(result2, false);
});
