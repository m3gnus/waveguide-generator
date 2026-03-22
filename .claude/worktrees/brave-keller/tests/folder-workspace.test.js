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

test('supportsFolderSelection returns true only when showDirectoryPicker exists', () => {
  assert.equal(supportsFolderSelection({ showDirectoryPicker: async () => ({}) }), true);
  assert.equal(supportsFolderSelection({}), false);
  assert.equal(supportsFolderSelection(null), false);
});

test('requestFolderSelection updates selected handle and label', async () => {
  resetSelectedFolder();
  const updates = [];
  const unsubscribe = subscribeFolderWorkspace((snapshot) => updates.push(snapshot));

  try {
    const selected = await requestFolderSelection({
      async showDirectoryPicker() {
        return { name: 'exports' };
      }
    });

    assert.equal(selected?.name, 'exports');
    assert.equal(getSelectedFolderHandle()?.name, 'exports');
    assert.equal(getSelectedFolderLabel(), 'exports');
    assert.ok(updates.length >= 2);
  } finally {
    unsubscribe();
    resetSelectedFolder();
  }
});

test('requestFolderSelection ignores AbortError without mutating state', async () => {
  setSelectedFolderHandle({ name: 'existing' });

  const selected = await requestFolderSelection({
    async showDirectoryPicker() {
      const error = new Error('cancelled');
      error.name = 'AbortError';
      throw error;
    }
  });

  assert.equal(selected, null);
  assert.equal(getSelectedFolderHandle()?.name, 'existing');
  resetSelectedFolder();
});

test('ensureFolderWritePermission handles granted and denied handles', async () => {
  const granted = await ensureFolderWritePermission({
    async queryPermission() {
      return 'granted';
    }
  });
  assert.equal(granted, true);

  const denied = await ensureFolderWritePermission({
    async queryPermission() {
      return 'denied';
    }
  });
  assert.equal(denied, false);
});
