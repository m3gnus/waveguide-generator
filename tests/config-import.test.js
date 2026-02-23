import test from 'node:test';
import assert from 'node:assert/strict';

import { handleFileUpload } from '../src/app/configImport.js';
import { GlobalState } from '../src/state.js';

function installDocumentMock({ outputName = 'horn_design', counter = '1' } = {}) {
  const prefixEl = { value: outputName };
  const counterEl = { value: String(counter) };

  return {
    doc: {
      getElementById(id) {
        if (id === 'export-prefix') return prefixEl;
        if (id === 'export-counter') return counterEl;
        return null;
      }
    },
    prefixEl,
    counterEl
  };
}

function installFileReaderMock() {
  global.FileReader = class MockFileReader {
    readAsText(file) {
      if (typeof this.onload === 'function') {
        this.onload({ target: { result: file.__content ?? '' } });
      }
    }
  };
}

test('handleFileUpload sets output fields from imported filename and clears file input value', () => {
  const originalFileReader = global.FileReader;
  const originalDocument = global.document;
  const originalUpdate = GlobalState.update;

  const { doc, prefixEl, counterEl } = installDocumentMock();
  global.document = doc;
  installFileReaderMock();

  const updates = [];
  GlobalState.update = (...args) => {
    updates.push(args);
  };

  const fileInputTarget = {
    files: [
      {
        name: '260219superhorn35.cfg',
        __content: 'OSSE = {\nL = 120\n}\n'
      }
    ],
    value: '260219superhorn35.cfg'
  };

  try {
    handleFileUpload({ target: fileInputTarget });

    assert.equal(prefixEl.value, '260219superhorn');
    assert.equal(counterEl.value, '35');
    assert.equal(updates.length, 1);
    assert.equal(updates[0][1], 'OSSE');
    assert.equal(fileInputTarget.value, '');
  } finally {
    global.FileReader = originalFileReader;
    global.document = originalDocument;
    GlobalState.update = originalUpdate;
  }
});

test('handleFileUpload leaves output fields unchanged on parse failure and still resets file input', () => {
  const originalFileReader = global.FileReader;
  const originalDocument = global.document;
  const originalUpdate = GlobalState.update;
  const originalConsoleError = console.error;

  const { doc, prefixEl, counterEl } = installDocumentMock({ outputName: 'existing_name', counter: '42' });
  global.document = doc;
  installFileReaderMock();

  let updateCalls = 0;
  GlobalState.update = () => {
    updateCalls += 1;
  };

  const errors = [];
  console.error = (message) => {
    errors.push(String(message));
  };

  const fileInputTarget = {
    files: [
      {
        name: 'waveguide_27.cfg',
        __content: 'NotAConfig = 1\n'
      }
    ],
    value: 'waveguide_27.cfg'
  };

  try {
    handleFileUpload({ target: fileInputTarget });

    assert.equal(prefixEl.value, 'existing_name');
    assert.equal(counterEl.value, '42');
    assert.equal(updateCalls, 0);
    assert.equal(fileInputTarget.value, '');
    assert.ok(
      errors.some((msg) => msg.includes('Could not find OSSE or R-OSSE block in config file.'))
    );
  } finally {
    global.FileReader = originalFileReader;
    global.document = originalDocument;
    GlobalState.update = originalUpdate;
    console.error = originalConsoleError;
  }
});
