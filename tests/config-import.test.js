import test from 'node:test';
import assert from 'node:assert/strict';

import { handleFileUpload } from '../src/app/configImport.js';
import { importMWGConfig } from '../src/modules/design/useCases.js';
import { DesignModule } from '../src/modules/design/index.js';
import { buildWaveguidePayload } from '../src/solver/waveguidePayload.js';
import {
  deriveExportFieldsFromFileName,
  resetParameterChangeTracking,
  setExportFields
} from '../src/ui/fileOps.js';
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

function createUiAdapter() {
  return {
    deriveExportFieldsFromFileName,
    setExportFields,
    resetParameterChangeTracking,
    showError(message) {
      console.error(message);
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
    handleFileUpload({ target: fileInputTarget }, createUiAdapter());

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
    handleFileUpload({ target: fileInputTarget }, createUiAdapter());

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

test('ATH flat config maps diameter aliases and marks total-length mode', () => {
  const result = importMWGConfig(
    `
Coverage.Angle = 52
Length = 150
Slot.Length = 45 - 0*sin(p)
Throat.Diameter = 36
OS.k = 0.9
Term.n = 3
Term.q = 0.996
Term.s = 0.9
`,
    'm2-clone.cfg'
  );

  assert.equal(result.success, true);
  assert.equal(result.type, 'OSSE');
  assert.equal(result.params.r0, 18);
  assert.equal(result.params.L, 150);
  assert.equal(result.params.slotLength, '45 - 0*sin(p)');
  assert.equal(result.params._athLengthMode, 'total');
});

test('ATH m2-style flat config preserves importer topology and defaults through backend payload', () => {
  const result = importMWGConfig(
    `
ABEC.SimType = 1
ABEC.f1 = 500
ABEC.f2 = 10000
ABEC.NumFrequencies = 20
Coverage.Angle = 62 - 10*sin(p)^2 - 10*sin(2*(p+pi/4))^4
Length = 150
Mesh.AngularSegments = 100
Mesh.CornerSegments = 4
Mesh.LengthSegments = 32
Mesh.MouthResolution = 10
Mesh.ThroatResolution = 3
Morph.CornerRadius = 8
Morph.TargetShape = 1
OS.k = 0.9
Slot.Length = 45 - 42*sin(2*p)^4
Term.n = 3 + 5*sin(2*p)^2
Term.q = 0.996
Term.s = 0.9
Throat.Diameter = 36
Throat.Profile = 1
`,
    'm2-clone.cfg'
  );

  assert.equal(result.success, true);
  assert.equal(result.params.a0, 0);
  assert.equal(result.params.simType, 1);
  assert.equal(result.params._athLengthMode, 'total');
  assert.equal(result.params.slotLength, '45 - 42*sin(2*p)^4');
  assert.equal(result.params.samplingMode, 'ath-default-zmap');
  assert.equal(result.params.wallThickness, 0);
  assert.equal(result.params.rearResolution, 10);
  assert.equal(result.params.sourceShape, 1);

  const designTask = DesignModule.task(
    DesignModule.importState({ type: result.type, params: result.params }, { applyVerticalOffset: true })
  );
  const meshParams = DesignModule.output.backendMeshSimulationParams(designTask);
  const payload = buildWaveguidePayload(meshParams, '2.2');

  assert.equal(payload.a0, 0);
  assert.equal(payload.length_mode, 'total');
  assert.equal(payload.sim_type, 1);
  assert.equal(payload.slot_length, '45 - 42*sin(2*p)^4');
  assert.equal(payload.sampling_mode, 'ath-default-zmap');
  assert.equal(payload.wall_thickness, 0);
  assert.equal(payload.rear_res, 10);
  assert.equal(payload.source_shape, 1);
});
