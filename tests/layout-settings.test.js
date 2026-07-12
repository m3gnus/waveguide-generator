import test, { beforeEach, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  RECOMMENDED_DEFAULTS,
  getCurrentLayoutSettings,
  getPanelChart,
  getPanelCharts,
  getPanelMode,
  getResultsLayout,
  getSplitFraction,
  loadLayoutSettings,
  resetLayoutSettings,
  saveLayoutSettings,
  setPanelArrangement,
  setPanelChart,
  setPanelCharts,
  setPanelMode,
  setResultsLayout,
  setSplitFraction,
} from '../src/ui/settings/layoutSettings.js';

const SETTINGS_KEY = 'waveguide-layout-settings';
const store = {};

global.localStorage = {
  getItem(key) {
    return store[key] ?? null;
  },
  setItem(key, value) {
    store[key] = value;
  },
  removeItem(key) {
    delete store[key];
  },
  clear() {
    Object.keys(store).forEach((key) => delete store[key]);
  },
};

function storeLayout(layout, schemaVersion = 1) {
  global.localStorage.setItem(SETTINGS_KEY, JSON.stringify({ schemaVersion, layout }));
}

beforeEach(() => {
  global.localStorage.clear();
  resetLayoutSettings();
});

describe('loadLayoutSettings', () => {
  test('returns independent recommended defaults when storage is empty', () => {
    global.localStorage.clear();

    const loaded = loadLayoutSettings();

    assert.deepEqual(loaded, RECOMMENDED_DEFAULTS);
    assert.notStrictEqual(loaded, RECOMMENDED_DEFAULTS);
    assert.notStrictEqual(loaded.panelCharts, RECOMMENDED_DEFAULTS.panelCharts);
  });

  test('falls back to defaults for malformed JSON and schema mismatches', () => {
    global.localStorage.setItem(SETTINGS_KEY, '{not-json');
    assert.deepEqual(loadLayoutSettings(), RECOMMENDED_DEFAULTS);

    storeLayout({ resultsLayout: 'split' }, 2);
    assert.deepEqual(loadLayoutSettings(), RECOMMENDED_DEFAULTS);

    global.localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ layout: { resultsLayout: 'split' } })
    );
    assert.deepEqual(loadLayoutSettings(), RECOMMENDED_DEFAULTS);
  });

  test('falls back to defaults when the layout domain is missing or malformed', () => {
    for (const layout of [undefined, null, 'split', 42, []]) {
      const envelope = { schemaVersion: 1 };
      if (layout !== undefined) envelope.layout = layout;
      global.localStorage.setItem(SETTINGS_KEY, JSON.stringify(envelope));
      assert.deepEqual(loadLayoutSettings(), RECOMMENDED_DEFAULTS);
    }
  });

  test('accepts known enums and repairs invalid enums independently', () => {
    storeLayout({ resultsLayout: 'split', panelMode: '2' });
    assert.deepEqual(loadLayoutSettings(), {
      ...RECOMMENDED_DEFAULTS,
      resultsLayout: 'split',
      panelMode: '2',
    });

    storeLayout({ resultsLayout: 'drawer', panelMode: 2 });
    const repaired = loadLayoutSettings();
    assert.equal(repaired.resultsLayout, RECOMMENDED_DEFAULTS.resultsLayout);
    assert.equal(repaired.panelMode, RECOMMENDED_DEFAULTS.panelMode);
  });

  test('clamps finite split fractions and repairs non-number values', () => {
    for (const [stored, expected] of [
      [-1, 0.15],
      [0.15, 0.15],
      [0.43, 0.43],
      [0.7, 0.7],
      [10, 0.7],
      ['0.5', RECOMMENDED_DEFAULTS.splitFraction],
      [null, RECOMMENDED_DEFAULTS.splitFraction],
    ]) {
      storeLayout({ splitFraction: stored });
      assert.equal(loadLayoutSettings().splitFraction, expected);
    }

    assert.equal(
      saveLayoutSettings({ splitFraction: Number.NaN }).splitFraction,
      RECOMMENDED_DEFAULTS.splitFraction
    );
    assert.equal(
      saveLayoutSettings({ splitFraction: Number.POSITIVE_INFINITY }).splitFraction,
      RECOMMENDED_DEFAULTS.splitFraction
    );
  });

  test('repairs panel chart arrays per slot', () => {
    const slotDefaults = [
      'directivity_map_h',
      'frequency_response',
      'directivity_map_v',
      'directivity_index',
      'impedance',
      'summary',
    ];
    const withSlots = (overrides) =>
      slotDefaults.map((fallback, index) => overrides[index] ?? fallback);

    const cases = [
      [null, withSlots({})],
      ['impedance', withSlots({})],
      [[], withSlots({})],
      [['impedance'], withSlots({ 0: 'impedance' })],
      [['invalid', 'directivity_index'], withSlots({ 1: 'directivity_index' })],
      [
        ['directivity_map_v', 'directivity_map'],
        withSlots({ 0: 'directivity_map_v', 1: 'directivity_map' }),
      ],
      [['summary', 'directivity_map_h'], withSlots({ 0: 'summary', 1: 'directivity_map_h' })],
      [['frequency_response', null], withSlots({ 0: 'frequency_response' })],
      [
        [
          'impedance',
          'directivity_index',
          'frequency_response',
          'summary',
          'directivity_map',
          'directivity_map_v',
          'frequency_response',
        ],
        [
          'impedance',
          'directivity_index',
          'frequency_response',
          'summary',
          'directivity_map',
          'directivity_map_v',
        ],
      ],
    ];

    for (const [panelCharts, expected] of cases) {
      storeLayout({ panelCharts });
      assert.deepEqual(loadLayoutSettings().panelCharts, expected);
    }
  });

  test('tolerantly merges known fields and discards unknown fields', () => {
    storeLayout({
      resultsLayout: 'split',
      panelMode: '1',
      splitFraction: 0.5,
      panelCharts: ['impedance', 'directivity_index'],
      futureSetting: true,
    });

    const loaded = loadLayoutSettings();

    assert.deepEqual(loaded, {
      resultsLayout: 'split',
      panelMode: '1',
      panelArrangement: 'auto',
      splitFraction: 0.5,
      panelCharts: [
        'impedance',
        'directivity_index',
        'directivity_map_v',
        'directivity_index',
        'impedance',
        'summary',
      ],
    });
    assert.equal(Object.hasOwn(loaded, 'futureSetting'), false);
  });
});

describe('layout field helpers', () => {
  test('getters and setters update every scalar field', () => {
    setResultsLayout('split');
    setPanelMode('2');
    setSplitFraction(0.52);

    assert.equal(getResultsLayout(), 'split');
    assert.equal(getPanelMode(), '2');
    assert.equal(getSplitFraction(), 0.52);
    assert.deepEqual(getCurrentLayoutSettings(), {
      ...RECOMMENDED_DEFAULTS,
      resultsLayout: 'split',
      panelMode: '2',
      splitFraction: 0.52,
    });
  });

  test('scalar setters validate enums and clamp fractions', () => {
    setResultsLayout('split');
    setPanelMode('1');

    assert.equal(setResultsLayout('unsupported').resultsLayout, 'split');
    assert.equal(setPanelMode('3').panelMode, '3');
    assert.equal(setPanelMode('6').panelMode, '6');
    assert.equal(setPanelMode('7').panelMode, 'auto');
    assert.equal(setPanelMode(2).panelMode, 'auto');
    assert.equal(setSplitFraction(0.01).splitFraction, 0.15);
    assert.equal(setSplitFraction(0.99).splitFraction, 0.7);
    assert.equal(
      setSplitFraction(Number.NEGATIVE_INFINITY).splitFraction,
      RECOMMENDED_DEFAULTS.splitFraction
    );
  });

  test('panel chart helpers update slots, repair invalid values, and return copies', () => {
    setPanelCharts(['impedance', 'directivity_index']);
    assert.equal(getPanelChart(0), 'impedance');
    assert.equal(getPanelChart(1), 'directivity_index');

    setPanelChart(1, 'frequency_response');
    assert.deepEqual(getPanelCharts().slice(0, 2), ['impedance', 'frequency_response']);

    setPanelChart(0, 'invalid');
    assert.deepEqual(getPanelCharts().slice(0, 2), ['directivity_map_h', 'frequency_response']);

    const charts = getPanelCharts();
    charts[0] = 'directivity_index';
    assert.equal(getPanelChart(0), 'directivity_map_h');
  });

  test('setPanelChart ignores indexes outside the persisted slots', () => {
    setPanelCharts(['impedance', 'directivity_index']);

    const before = getPanelCharts();
    setPanelChart(-1, 'frequency_response');
    setPanelChart(6, 'frequency_response');
    assert.deepEqual(getPanelCharts(), before);

    setPanelChart(5, 'frequency_response');
    assert.equal(getPanelChart(5), 'frequency_response');
  });

  test('panel arrangement accepts known values and repairs invalid ones', () => {
    storeLayout({ panelArrangement: 'grid' });
    assert.equal(loadLayoutSettings().panelArrangement, 'grid');

    storeLayout({ panelArrangement: 'diagonal' });
    assert.equal(loadLayoutSettings().panelArrangement, 'auto');

    assert.equal(setPanelArrangement('rows').panelArrangement, 'rows');
    assert.equal(setPanelArrangement('bogus').panelArrangement, 'auto');
  });
});

describe('persistence and reset', () => {
  test('persists the exact versioned layout envelope', () => {
    saveLayoutSettings({
      resultsLayout: 'split',
      panelMode: '2',
      splitFraction: 0.6,
      panelCharts: ['impedance', 'directivity_index'],
      ignored: 'value',
    });

    assert.deepEqual(JSON.parse(global.localStorage.getItem(SETTINGS_KEY)), {
      schemaVersion: 1,
      layout: {
        resultsLayout: 'split',
        panelMode: '2',
        panelArrangement: 'auto',
        splitFraction: 0.6,
        panelCharts: [
          'impedance',
          'directivity_index',
          'directivity_map_v',
          'directivity_index',
          'impedance',
          'summary',
        ],
      },
    });
  });

  test('reset restores and persists independent recommended defaults', () => {
    saveLayoutSettings({
      resultsLayout: 'split',
      panelMode: '2',
      splitFraction: 0.6,
      panelCharts: ['impedance', 'directivity_index'],
    });

    const reset = resetLayoutSettings();
    const persisted = JSON.parse(global.localStorage.getItem(SETTINGS_KEY));

    assert.deepEqual(reset, RECOMMENDED_DEFAULTS);
    assert.notStrictEqual(reset.panelCharts, RECOMMENDED_DEFAULTS.panelCharts);
    assert.deepEqual(persisted, {
      schemaVersion: 1,
      layout: RECOMMENDED_DEFAULTS,
    });
  });
});
