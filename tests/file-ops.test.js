import test from 'node:test';
import assert from 'node:assert/strict';

import { saveFile, selectOutputFolder } from '../src/ui/fileOps.js';
import {
  getSelectedFolderHandle,
  resetSelectedFolder,
  setSelectedFolderHandle
} from '../src/ui/workspace/folderWorkspace.js';

test('saveFile clears the selected workspace and falls back to the picker when a folder write fails', async () => {
  const originalDocument = global.document;
  const originalWindow = global.window;

  const saved = [];
  global.document = {
    getElementById() {
      return null;
    }
  };
  global.window = {
    async showSaveFilePicker(options = {}) {
      return {
        suggestedName: options.suggestedName,
        async createWritable() {
          return {
            async write(content) {
              saved.push({
                fileName: options.suggestedName,
                content: String(content)
              });
            },
            async close() {}
          };
        }
      };
    }
  };

  setSelectedFolderHandle({
    name: 'workspace',
    async queryPermission() {
      return 'granted';
    },
    async getFileHandle() {
      throw new Error('disk full');
    }
  });

  try {
    await saveFile('manual-export', 'horn_design.txt', {
      contentType: 'text/plain'
    });

    assert.equal(getSelectedFolderHandle(), null);
    assert.deepEqual(saved, [
      {
        fileName: 'horn_design.txt',
        content: 'manual-export'
      }
    ]);
  } finally {
    resetSelectedFolder();
    global.document = originalDocument;
    global.window = originalWindow;
  }
});

test('selectOutputFolder keeps the simulation header button title in sync with the selected workspace', async () => {
  const originalDocument = global.document;
  const originalWindow = global.window;

  const chooseBtn = {
    textContent: '',
    title: '',
    attributes: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
    }
  };

  global.document = {
    getElementById(id) {
      return id === 'choose-folder-btn' ? chooseBtn : null;
    }
  };
  global.window = {
    async showDirectoryPicker() {
      return { name: 'exports' };
    }
  };

  try {
    await selectOutputFolder();

    assert.equal(chooseBtn.textContent, 'Output Folder');
    assert.equal(chooseBtn.title, 'Selected output folder: exports');
    assert.equal(chooseBtn.attributes['aria-label'], 'Selected output folder: exports');
    assert.equal(getSelectedFolderHandle()?.name, 'exports');
  } finally {
    resetSelectedFolder();
    global.document = originalDocument;
    global.window = originalWindow;
  }
});
