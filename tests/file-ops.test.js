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
  const originalFetch = global.fetch;

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
  global.fetch = async () => {
    throw new TypeError('network unavailable');
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
    global.fetch = originalFetch;
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

test('saveFile writes to backend workspace when folder picker support is unavailable', async () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.document = {
    getElementById() {
      return null;
    }
  };
  global.window = {};
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      }
    };
  };

  try {
    await saveFile('manual-export', 'horn_design.txt', {
      contentType: 'text/plain'
    });

    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    const body = fetchCalls[0].options.body;
    assert.equal(body.get('workspace_subdir'), null);
    const fileBlob = body.get('file');
    assert.equal(fileBlob?.name, 'horn_design.txt');
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.fetch = originalFetch;
  }
});

test('saveFile sends workspace_subdir for backend workspace writes', async () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.document = {
    getElementById() {
      return null;
    }
  };
  global.window = {};
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      }
    };
  };

  try {
    await saveFile('bundle-export', 'horn_12_results.csv', {
      contentType: 'text/csv',
      workspaceSubdir: 'horn_12'
    });

    assert.equal(fetchCalls.length, 1);
    const body = fetchCalls[0].options.body;
    assert.equal(body.get('workspace_subdir'), 'horn_12');
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.fetch = originalFetch;
  }
});
