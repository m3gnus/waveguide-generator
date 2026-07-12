import test from 'node:test';
import assert from 'node:assert/strict';

import { applySmoothing } from '../src/results/smoothing.js';
import {
  CHART_TYPES,
  buildDirectivityPayload,
  buildLineChartsPayload,
  requestDirectivityMap,
  requestLineCharts,
} from '../src/ui/simulation/chartRequests.js';

function makeLineResults({ offset = 0, metadata = {} } = {}) {
  return {
    spl_on_axis: {
      frequencies: [100, 120, 140, 160, 180],
      spl: [80, 91, 83, 96, 85].map((value) => value + offset),
      phase_degrees: [1, 2, 3, 4, 5],
    },
    di: {
      frequencies: [200, 240, 280, 320, 360],
      horizontal: [1, 5, 2, 7, 3].map((value) => value + offset),
      vertical: [2, 8, 3, 9, 4].map((value) => value + offset),
      diagonal: [3, 6, 4, 8, 5].map((value) => value + offset),
    },
    impedance: {
      frequencies: [300, 360, 420, 480, 540],
      real: [10, 25, 12, 30, 14].map((value) => value + offset),
      imaginary: [-8, 3, -5, 6, -2].map((value) => value + offset),
    },
    metadata,
  };
}

test('CHART_TYPES exposes the four dock and modal chart choices in display order', () => {
  assert.deepEqual(CHART_TYPES, [
    { key: 'directivity_map', label: 'Polar Directivity Map' },
    { key: 'impedance', label: 'Acoustic Impedance' },
    { key: 'directivity_index', label: 'Directivity Index' },
    { key: 'frequency_response', label: 'Frequency Response (SPL On-Axis)' },
  ]);
  assert.equal(Object.isFrozen(CHART_TYPES), true);
  assert.equal(CHART_TYPES.every(Object.isFrozen), true);
});

test('buildLineChartsPayload preserves the classic no-reference body and convention flags', () => {
  const results = makeLineResults({
    metadata: {
      directivity: { effective_distance_m: 2.75 },
      phase_time_convention: 'exp(+ikr)',
      impedance_units: 'Z/(rho*c)',
    },
  });

  const payload = buildLineChartsPayload(results, {
    smoothing: 'none',
    theme: 'dark',
  });

  assert.deepEqual(payload, {
    frequencies: results.spl_on_axis.frequencies,
    spl: results.spl_on_axis.spl,
    phase_degrees: results.spl_on_axis.phase_degrees,
    phase_reference_distance_m: 2.75,
    phase_time_convention: 'metal',
    di: {
      horizontal: results.di.horizontal,
      vertical: results.di.vertical,
      diagonal: results.di.diagonal,
    },
    di_frequencies: results.di.frequencies,
    impedance_frequencies: results.impedance.frequencies,
    impedance_real: results.impedance.real,
    impedance_imaginary: results.impedance.imaginary,
    theme: 'dark',
    impedance_units: 'Z/(rho*c)',
    impedance_normalization: 'rho_c',
  });
  assert.equal(Object.hasOwn(payload, 'reference'), false);
});

test('buildLineChartsPayload applies identical smoothing to primary and reference series', () => {
  const primary = makeLineResults({ offset: 0 });
  const referenceResults = makeLineResults({
    offset: 20,
    metadata: { impedance_normalization: 'rho-c' },
  });
  const primarySnapshot = structuredClone(primary);
  const referenceSnapshot = structuredClone(referenceResults);

  const payload = buildLineChartsPayload(primary, {
    smoothing: '1/1',
    theme: 'light',
    reference: { results: referenceResults, label: 'Previous run' },
  });

  assert.deepEqual(
    payload.spl,
    applySmoothing(primary.spl_on_axis.frequencies, primary.spl_on_axis.spl, '1/1')
  );
  for (const plane of ['horizontal', 'vertical', 'diagonal']) {
    assert.deepEqual(
      payload.di[plane],
      applySmoothing(primary.di.frequencies, primary.di[plane], '1/1')
    );
  }
  assert.deepEqual(
    payload.impedance_real,
    applySmoothing(primary.impedance.frequencies, primary.impedance.real, '1/1')
  );
  assert.deepEqual(
    payload.impedance_imaginary,
    applySmoothing(primary.impedance.frequencies, primary.impedance.imaginary, '1/1')
  );

  assert.equal(payload.reference.label, 'Previous run');
  assert.deepEqual(
    payload.reference.spl,
    applySmoothing(
      referenceResults.spl_on_axis.frequencies,
      referenceResults.spl_on_axis.spl,
      '1/1'
    )
  );
  for (const plane of ['horizontal', 'vertical', 'diagonal']) {
    assert.deepEqual(
      payload.reference.di[plane],
      applySmoothing(referenceResults.di.frequencies, referenceResults.di[plane], '1/1')
    );
  }
  assert.deepEqual(
    payload.reference.impedance_real,
    applySmoothing(referenceResults.impedance.frequencies, referenceResults.impedance.real, '1/1')
  );
  assert.deepEqual(
    payload.reference.impedance_imaginary,
    applySmoothing(
      referenceResults.impedance.frequencies,
      referenceResults.impedance.imaginary,
      '1/1'
    )
  );
  assert.equal(payload.reference.impedance_units, 'Z/(rho*c)');
  assert.equal(payload.reference.impedance_normalization, 'rho_c');
  assert.equal(Object.hasOwn(payload.reference, 'phase_degrees'), false);

  assert.notDeepEqual(payload.spl, primary.spl_on_axis.spl);
  assert.notDeepEqual(payload.reference.spl, referenceResults.spl_on_axis.spl);
  assert.deepEqual(primary, primarySnapshot, 'primary results must not be mutated');
  assert.deepEqual(referenceResults, referenceSnapshot, 'reference results must not be mutated');
});

test('buildDirectivityPayload normalizes plane dictionaries and maps reference fields', () => {
  const results = {
    spl_on_axis: { frequencies: [100, 200] },
    directivity: {
      vertical: [
        [
          [0, 0],
          [30, -5],
        ],
      ],
      ignored: { malformed: true },
      diagonal: [
        [
          [0, 0],
          [30, -6],
        ],
      ],
      horizontal: [
        [
          [0, 0],
          [30, -4],
        ],
      ],
      auxiliary: [
        [
          [0, 0],
          [30, -7],
        ],
      ],
    },
  };
  const referenceResults = {
    spl_on_axis: { frequencies: [125, 250] },
    directivity: {
      diagonal: [
        [
          [0, 0],
          [30, -8],
        ],
      ],
      malformed: null,
      horizontal: [
        [
          [0, 0],
          [30, -3],
        ],
      ],
    },
  };

  const payload = buildDirectivityPayload(results, {
    referenceLevel: -9,
    theme: 'midnight',
    reference: { results: referenceResults, label: 'Baseline' },
  });

  assert.deepEqual(Object.keys(payload.directivity), [
    'horizontal',
    'vertical',
    'diagonal',
    'auxiliary',
  ]);
  assert.deepEqual(payload.frequencies, [100, 200]);
  assert.equal(payload.reference_level, -9);
  assert.equal(payload.theme, 'midnight');
  assert.deepEqual(payload.reference_frequencies, [125, 250]);
  assert.deepEqual(Object.keys(payload.reference_directivity), ['horizontal', 'diagonal']);
  assert.equal(payload.reference_label, 'Baseline');
  assert.equal(Object.hasOwn(payload.directivity, 'ignored'), false);
  assert.equal(Object.hasOwn(payload.reference_directivity, 'malformed'), false);

  const withoutReference = buildDirectivityPayload(results, {
    referenceLevel: -6,
    theme: 'light',
  });
  assert.equal(Object.hasOwn(withoutReference, 'reference_frequencies'), false);
  assert.equal(Object.hasOwn(withoutReference, 'reference_directivity'), false);
  assert.equal(Object.hasOwn(withoutReference, 'reference_label'), false);
});

test('requestLineCharts posts the exact JSON body and normalizes a successful response', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const payload = { frequencies: [100], spl: [90], theme: 'dark' };

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    return {
      ok: true,
      status: 200,
      async json() {
        return { charts: { frequency_response: 'base64-fr' } };
      },
    };
  };

  try {
    const result = await requestLineCharts('http://backend.test', payload);

    assert.deepEqual(result, {
      ok: true,
      charts: { frequency_response: 'base64-fr' },
      status: 200,
      detail: '',
      kind: null,
    });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, 'http://backend.test/api/render-charts');
    assert.deepEqual(requests[0].init, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    assert.deepEqual(JSON.parse(requests[0].init.body), payload);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('requestDirectivityMap posts the exact JSON body and returns its image', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const payload = {
    frequencies: [100],
    directivity: { horizontal: [[[0, 0]]] },
    reference_level: -6,
  };

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    return {
      ok: true,
      status: 201,
      async json() {
        return { image: 'base64-map' };
      },
    };
  };

  try {
    const result = await requestDirectivityMap('http://backend.test', payload);

    assert.deepEqual(result, {
      ok: true,
      image: 'base64-map',
      status: 201,
      detail: '',
      kind: null,
    });
    assert.equal(requests[0].url, 'http://backend.test/api/render-directivity');
    assert.equal(requests[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(requests[0].init.body), payload);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('request helpers normalize 503, other HTTP errors, and network failures', async () => {
  const originalFetch = globalThis.fetch;

  try {
    globalThis.fetch = async () => ({
      ok: false,
      status: 503,
      async text() {
        return 'matplotlib unavailable';
      },
    });
    assert.deepEqual(await requestLineCharts('http://backend.test', {}), {
      ok: false,
      charts: {},
      status: 503,
      detail: 'matplotlib unavailable',
      kind: 'matplotlib-missing',
    });

    globalThis.fetch = async () => ({
      ok: false,
      status: 422,
      async text() {
        return '{"detail":"invalid directivity"}';
      },
    });
    assert.deepEqual(await requestDirectivityMap('http://backend.test', {}), {
      ok: false,
      image: null,
      status: 422,
      detail: '{"detail":"invalid directivity"}',
      kind: 'http',
    });

    globalThis.fetch = async () => {
      throw new TypeError('fetch failed');
    };
    assert.deepEqual(await requestLineCharts('http://backend.test', {}), {
      ok: false,
      charts: {},
      status: 0,
      detail: 'fetch failed',
      kind: 'network',
    });
    assert.deepEqual(await requestDirectivityMap('http://backend.test', {}), {
      ok: false,
      image: null,
      status: 0,
      detail: 'fetch failed',
      kind: 'network',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
