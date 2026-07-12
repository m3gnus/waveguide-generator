import test, { describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildResultsDockCacheKey,
  buildResultsDockRequest,
  createLruImageCache,
  createPanelRequestGuard,
  resolveResultsDockColumns,
  resolveResultsDockPanelCount,
  setupResultsDock,
} from '../src/ui/results/resultsDock.js';
import { resetLayoutSettings, setResultsLayout } from '../src/ui/settings/layoutSettings.js';
import { displayResults } from '../src/ui/simulation/results.js';

function makeResults({ offset = 0 } = {}) {
  return {
    spl_on_axis: {
      frequencies: [100, 200],
      spl: [80 + offset, 84 + offset],
      phase_degrees: [10, 20],
    },
    di: {
      frequencies: [100, 200],
      horizontal: [3 + offset, 4 + offset],
      vertical: [2 + offset, 3 + offset],
    },
    impedance: {
      frequencies: [100, 200],
      real: [1 + offset, 2 + offset],
      imaginary: [0.1 + offset, 0.2 + offset],
    },
    directivity: {
      vertical: [[[0, -2 - offset]]],
      horizontal: [[[0, -1 - offset]]],
    },
    metadata: {
      impedance_normalization: 'rho_c',
    },
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe('resolveResultsDockPanelCount', () => {
  test('requires both the minimum width and 2.2 aspect ratio in auto mode', () => {
    assert.equal(resolveResultsDockPanelCount({ width: 899, height: 400, mode: 'auto' }), 1);
    assert.equal(resolveResultsDockPanelCount({ width: 900, height: 400, mode: 'auto' }), 2);
    assert.equal(resolveResultsDockPanelCount({ width: 1000, height: 500, mode: 'auto' }), 1);
    assert.equal(resolveResultsDockPanelCount({ width: 1100, height: 500, mode: 'auto' }), 2);
  });

  test('enters at 900px and retains two panels through the 40px hysteresis band', () => {
    assert.equal(
      resolveResultsDockPanelCount({
        width: 899.99,
        height: 400,
        previousCount: 1,
      }),
      1
    );
    assert.equal(resolveResultsDockPanelCount({ width: 900, height: 400, previousCount: 1 }), 2);
    assert.equal(
      resolveResultsDockPanelCount({
        width: 859.99,
        height: 400,
        previousCount: 2,
      }),
      1
    );
    assert.equal(resolveResultsDockPanelCount({ width: 860, height: 400, previousCount: 2 }), 2);
  });

  test('applies the same 40px hysteresis when aspect ratio sets the threshold', () => {
    assert.equal(
      resolveResultsDockPanelCount({
        width: 1099.99,
        height: 500,
        previousCount: 1,
      }),
      1
    );
    assert.equal(resolveResultsDockPanelCount({ width: 1100, height: 500, previousCount: 1 }), 2);
    assert.equal(
      resolveResultsDockPanelCount({
        width: 1059.99,
        height: 500,
        previousCount: 2,
      }),
      1
    );
    assert.equal(resolveResultsDockPanelCount({ width: 1060, height: 500, previousCount: 2 }), 2);
  });

  test('column resolver honors explicit arrangements', () => {
    assert.equal(resolveResultsDockColumns({ panelCount: 4, arrangement: 'rows' }), 1);
    assert.equal(resolveResultsDockColumns({ panelCount: 4, arrangement: 'columns' }), 4);
    assert.equal(resolveResultsDockColumns({ panelCount: 3, arrangement: 'grid' }), 2);
    assert.equal(resolveResultsDockColumns({ panelCount: 6, arrangement: 'grid' }), 3);
    assert.equal(resolveResultsDockColumns({ panelCount: 1, arrangement: 'columns' }), 1);
  });

  test('column resolver auto mode adapts to dock size and count', () => {
    // Two panels reuse the side-by-side room rule.
    assert.equal(
      resolveResultsDockColumns({ panelCount: 2, arrangement: 'auto', width: 899, height: 300 }),
      1
    );
    assert.equal(
      resolveResultsDockColumns({ panelCount: 2, arrangement: 'auto', width: 900, height: 300 }),
      2
    );
    // More panels form a near-square grid, collapsing on narrow docks.
    assert.equal(
      resolveResultsDockColumns({ panelCount: 4, arrangement: 'auto', width: 1200, height: 400 }),
      2
    );
    assert.equal(
      resolveResultsDockColumns({ panelCount: 6, arrangement: 'auto', width: 1200, height: 400 }),
      3
    );
    assert.equal(
      resolveResultsDockColumns({ panelCount: 4, arrangement: 'auto', width: 500, height: 400 }),
      1
    );
  });

  test('forced modes ignore dimensions and prior state', () => {
    assert.equal(resolveResultsDockPanelCount({ width: 2000, height: 200, mode: '1' }), 1);
    assert.equal(resolveResultsDockPanelCount({ width: 0, height: 0, mode: '2' }), 2);
    assert.equal(resolveResultsDockPanelCount({ width: 0, height: 0, mode: '4' }), 4);
    assert.equal(resolveResultsDockPanelCount({ width: 0, height: 0, mode: '6' }), 6);
    // Values beyond the maximum clamp instead of overflowing.
    assert.equal(resolveResultsDockPanelCount({ width: 0, height: 0, mode: '9' }), 6);
  });
});

describe('buildResultsDockCacheKey', () => {
  const descriptor = {
    jobId: 'job-current',
    chartKey: 'frequency_response',
    smoothing: '1/3',
    refLevel: -6,
    theme: 'dark',
    compareJobId: 'job-reference',
  };

  test('produces a fixed canonical JSON key independent of input property order', () => {
    const expected =
      '{"jobId":"job-current","chartKey":"frequency_response","smoothing":"1/3","refLevel":-6,"theme":"dark","compareJobId":"job-reference"}';
    assert.equal(buildResultsDockCacheKey(descriptor), expected);
    assert.equal(
      buildResultsDockCacheKey({
        compareJobId: descriptor.compareJobId,
        theme: descriptor.theme,
        refLevel: descriptor.refLevel,
        smoothing: descriptor.smoothing,
        chartKey: descriptor.chartKey,
        jobId: descriptor.jobId,
      }),
      expected
    );
  });

  test('changes when any render-affecting field changes', () => {
    const base = buildResultsDockCacheKey(descriptor);
    const variants = [
      { jobId: 'job-other' },
      { chartKey: 'impedance' },
      { smoothing: '1/6' },
      { refLevel: -3 },
      { theme: 'light' },
      { compareJobId: null },
    ];

    for (const changed of variants) {
      assert.notEqual(buildResultsDockCacheKey({ ...descriptor, ...changed }), base);
    }
  });
});

describe('buildResultsDockRequest comparison mapping', () => {
  const current = makeResults();
  const comparison = makeResults({ offset: 10 });
  const reference = { results: comparison, label: 'Reference job' };

  test('summary panels build a client-rendered request with no payload', () => {
    const request = buildResultsDockRequest({
      results: current,
      chartKey: 'summary',
      reference,
    });

    assert.equal(request.kind, 'summary');
    assert.equal(request.chartKey, 'summary');
    assert.equal(Object.hasOwn(request, 'payload'), false);
  });

  test('maps a comparison descriptor to the pinned line-chart reference block', () => {
    const request = buildResultsDockRequest({
      results: current,
      chartKey: 'frequency_response',
      smoothing: 'none',
      referenceLevel: -6,
      theme: 'dark',
      reference,
    });

    assert.equal(request.kind, 'line');
    assert.equal(request.chartKey, 'frequency_response');
    assert.deepEqual(request.payload.reference, {
      label: 'Reference job',
      frequencies: [100, 200],
      spl: [90, 94],
      di: {
        horizontal: [13, 14],
        vertical: [12, 13],
      },
      di_frequencies: [100, 200],
      impedance_frequencies: [100, 200],
      impedance_real: [11, 12],
      impedance_imaginary: [10.1, 10.2],
      impedance_units: 'Z/(rho*c)',
      impedance_normalization: 'rho_c',
    });
    assert.equal(request.payload.theme, 'dark');
    assert.equal(Object.hasOwn(request.payload, 'reference_frequencies'), false);
  });

  test('maps a comparison descriptor to the pinned directivity reference fields', () => {
    const request = buildResultsDockRequest({
      results: current,
      chartKey: 'directivity_map',
      smoothing: 'none',
      referenceLevel: -3,
      theme: 'light',
      reference,
    });

    assert.equal(request.kind, 'directivity');
    assert.equal(request.chartKey, 'directivity_map');
    assert.equal(request.payload.reference_level, -3);
    assert.equal(request.payload.theme, 'light');
    assert.deepEqual(request.payload.reference_frequencies, [100, 200]);
    assert.deepEqual(request.payload.reference_directivity, {
      horizontal: [[[0, -11]]],
      vertical: [[[0, -12]]],
    });
    assert.equal(request.payload.reference_label, 'Reference job');
    assert.equal(Object.hasOwn(request.payload, 'reference'), false);
  });
});

test('createLruImageCache enforces capacity and refreshes recency on get', () => {
  const cache = createLruImageCache(2);
  assert.equal(cache instanceof Map, true);
  cache.set('first', 'image-1');
  cache.set('second', 'image-2');
  assert.equal(cache.get('first'), 'image-1');

  cache.set('third', 'image-3');

  assert.equal(cache.size, 2);
  assert.equal(cache.has('first'), true);
  assert.equal(cache.has('second'), false);
  assert.equal(cache.has('third'), true);
});

test('createPanelRequestGuard ignores an old deferred result and accepts the current one', async () => {
  const guard = createPanelRequestGuard(1);
  const oldRequest = deferred();
  const currentRequest = deferred();
  const committed = [];

  async function commitWhenCurrent(promise) {
    const token = guard.begin(0);
    const value = await promise;
    if (!guard.isCurrent(0, token)) return false;
    committed.push(value);
    return true;
  }

  const oldCommit = commitWhenCurrent(oldRequest.promise);
  const currentCommit = commitWhenCurrent(currentRequest.promise);

  oldRequest.resolve('old image');
  assert.equal(await oldCommit, false);
  assert.deepEqual(committed, []);

  currentRequest.resolve('current image');
  assert.equal(await currentCommit, true);
  assert.deepEqual(committed, ['current image']);
});

test('displayResults notifies the dock through the panel app reference', () => {
  const calls = [];
  const results = makeResults();
  const job = { id: 'job-current', status: 'complete' };
  const panel = {
    lastResults: null,
    app: {
      resultsDock: {
        onResults(nextResults, nextJob) {
          calls.push([nextResults, nextJob]);
        },
      },
    },
  };

  displayResults(panel, results, job);

  assert.equal(panel.lastResults, results);
  assert.deepEqual(calls, [[results, job]]);
});

test('classic layout stays hidden without loading results or requesting charts', async () => {
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;
  const originalResizeObserver = globalThis.ResizeObserver;
  let fetchCalls = 0;
  let ensurePanelCalls = 0;

  function makeElement() {
    const classes = new Set(['is-hidden']);
    return {
      classList: {
        toggle(name, force) {
          if (force) classes.add(name);
          else classes.delete(name);
        },
        contains(name) {
          return classes.has(name);
        },
      },
      style: {
        setProperty() {},
      },
      setAttribute() {},
    };
  }

  const element = makeElement();
  const resizer = makeElement();
  globalThis.document = {
    getElementById(id) {
      if (id === 'results-dock') return element;
      if (id === 'viewport-results-resizer') return resizer;
      return null;
    },
  };
  globalThis.window = {
    matchMedia() {
      return { matches: false, addEventListener() {} };
    },
  };
  globalThis.ResizeObserver = undefined;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error('classic layout must not fetch');
  };
  // Split view is the recommended default, so classic must be chosen explicitly.
  resetLayoutSettings();
  setResultsLayout('classic');

  try {
    const app = {
      onResize() {},
      ensureSimulationPanel() {
        ensurePanelCalls += 1;
        return Promise.resolve(null);
      },
    };
    const dock = setupResultsDock(app);
    await Promise.resolve();

    assert.equal(dock.visible, false);
    assert.equal(element.classList.contains('is-hidden'), true);
    assert.equal(resizer.classList.contains('is-hidden'), true);
    assert.equal(ensurePanelCalls, 0);
    assert.equal(fetchCalls, 0);
  } finally {
    globalThis.document = originalDocument;
    globalThis.window = originalWindow;
    globalThis.fetch = originalFetch;
    if (originalResizeObserver === undefined) delete globalThis.ResizeObserver;
    else globalThis.ResizeObserver = originalResizeObserver;
  }
});
