import test from 'node:test';
import assert from 'node:assert/strict';

import { saveFile } from '../src/ui/fileOps.js';
import { selectOutputFolder } from '../src/ui/workspace/folderWorkspace.js';
import {
  getSelectedFolderHandle,
  getSelectedFolderLabel,
  resetSelectedFolder
} from '../src/ui/workspace/folderWorkspace.js';

test('saveFile falls back to showSaveFilePicker when backend write fails', async () => {
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

test('selectOutputFolder calls backend and updates workspace label', async () => {
  const originalFetch = global.fetch;

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
    const result = await selectOutputFolder();

    assert.equal(result, '/Users/test/exports');
    assert.equal(getSelectedFolderHandle(), null);
    assert.equal(getSelectedFolderLabel(), 'exports');
  } finally {
    resetSelectedFolder();
    global.fetch = originalFetch;
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

test('saveFile uses backend workspace when no folder is selected', async () => {
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
    assert.equal(fetchCalls[0].options.body.get('workspace_subdir'), null);
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.fetch = originalFetch;
  }
});
