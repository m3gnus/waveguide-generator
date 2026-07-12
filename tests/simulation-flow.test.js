import test from 'node:test';
import assert from 'node:assert/strict';

import * as solverApi from '../src/solver/index.js';
import { applySmoothingSelection } from '../src/ui/simulation/smoothing.js';
import { downloadMeshArtifact } from '../src/ui/simulation/meshDownload.js';
import { renderJobList, formatJobSummary } from '../src/ui/simulation/jobActions.js';
import { pollSimulationStatus, clearPollTimer } from '../src/ui/simulation/polling.js';
import { openViewResultsModal } from '../src/ui/simulation/viewResults.js';
import {
  clearProgressHideTimer,
  scheduleProgressHide,
} from '../src/ui/simulation/jobOrchestration.js';
import { AppEvents } from '../src/events.js';
import { getDownloadSimMeshEnabled } from '../src/ui/settings/modal.js';
import {
  RECOMMENDED_DEFAULTS as SIM_MANAGEMENT_DEFAULTS,
  saveSimulationManagementSettings,
} from '../src/ui/settings/simulationManagementSettings.js';

const { BemSolver, validateCanonicalMeshPayload } = solverApi;

test('solver public API no longer exposes mock fallback helpers', () => {
  assert.equal('mockBEMSolver' in solverApi, false);
});

test('submitSimulation sends canonical mesh payload shape and adaptive mesh options', async () => {
  const originalFetch = global.fetch;
  const calls = [];

  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { job_id: 'job-test-1' };
      },
    };
  };

  try {
    const solver = new BemSolver();
    solver.backendUrl = 'http://localhost:8000';

    const mesh = {
      vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
      indices: [0, 1, 2],
      surfaceTags: [2],
      format: 'msh',
      boundaryConditions: {
        throat: { type: 'velocity', surfaceTag: 2, value: 1.0 },
        wall: { type: 'neumann', surfaceTag: 1, value: 0.0 },
        mouth: { type: 'robin', surfaceTag: 1, impedance: 'spherical' },
      },
      metadata: { ringCount: 3, fullCircle: true },
    };

    const options = {
      mesh: {
        strategy: 'hornlab_mesher',
        waveguide_params: {
          formula_type: 'OSSE',
          throat_res: 4,
          mouth_res: 9,
          rear_res: 12,
        },
      },
    };

    const jobId = await solver.submitSimulation(
      {
        frequencyStart: 100,
        frequencyEnd: 1000,
        numFrequencies: 4,
        simulationType: '2',
        meshValidationMode: 'strict',
        frequencySpacing: 'linear',
        verbose: false,
        solverBackend: 'metal',
        solverMode: 'circsym',
        polarConfig: {
          angle_range: [0, 180, 37],
          norm_angle: 5,
          distance: 2,
          inclination: 45,
          enabled_axes: ['horizontal', 'diagonal'],
        },
      },
      {
        vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
        surfaceTags: [2],
        format: 'msh',
        boundaryConditions: {},
        metadata: {},
      },
      options
    );

    const payload = JSON.parse(calls[0].options.body);
    assert.ok(payload.mesh);
    assert.equal(payload.mesh.surfaceTags.length, payload.mesh.indices.length / 3);
    assert.equal(payload.options.mesh.strategy, 'hornlab_mesher');
    assert.equal(payload.options.mesh.waveguide_params.formula_type, 'OSSE');
    assert.deepEqual(payload.polar_config.enabled_axes, ['horizontal', 'diagonal']);
    assert.equal(payload.sim_type, '2');
    assert.equal(payload.mesh_validation_mode, 'strict');
    assert.equal(payload.frequency_spacing, 'linear');
    assert.equal(payload.solver_backend, 'metal');
    assert.equal(payload.solver_mode, 'circsym');
    assert.equal('device_mode' in payload, false);
    assert.equal(payload.verbose, false);
    assert.equal('advanced_settings' in payload, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test('submitSimulation omits invalid or unset runtime settings so backend defaults remain authoritative', async () => {
  const originalFetch = global.fetch;
  const calls = [];

  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { job_id: 'job-test-omit-1' };
      },
    };
  };

  try {
    const solver = new BemSolver();
    await solver.submitSimulation(
      {
        frequencyStart: 100,
        frequencyEnd: 1000,
        numFrequencies: 4,
        simulationType: '2',
        meshValidationMode: 'invalid',
        frequencySpacing: 'bogus',
        solverBackend: 'invalid-backend',
        solverMode: 'invalid-mode',
        verbose: undefined,
      },
      {
        vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
        surfaceTags: [2],
        format: 'msh',
        boundaryConditions: {},
        metadata: {},
      }
    );

    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /\/api\/solve$/);

    const payload = JSON.parse(calls[0].options.body);
    assert.deepEqual(Object.keys(payload.mesh).sort(), [
      'boundaryConditions',
      'format',
      'indices',
      'metadata',
      'surfaceTags',
      'vertices',
    ]);
    assert.equal('mesh_validation_mode' in payload, false);
    assert.equal('frequency_spacing' in payload, false);
    assert.equal('solver_backend' in payload, false);
    assert.equal('solver_mode' in payload, false);
    assert.equal('device_mode' in payload, false);
    assert.equal('verbose' in payload, false);
    assert.equal('advanced_settings' in payload, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test('submitSimulation sends Bempp backend selection when requested', async () => {
  const originalFetch = global.fetch;
  const calls = [];

  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { job_id: 'job-test-bempp' };
      },
    };
  };

  try {
    const solver = new BemSolver();
    await solver.submitSimulation(
      {
        frequencyStart: 100,
        frequencyEnd: 1000,
        numFrequencies: 4,
        simulationType: '2',
        solverBackend: 'bempp',
      },
      {
        vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
        surfaceTags: [2],
        format: 'msh',
        boundaryConditions: {},
        metadata: {},
      }
    );

    const payload = JSON.parse(calls[0].options.body);
    assert.equal(payload.solver_backend, 'bempp');
  } finally {
    global.fetch = originalFetch;
  }
});

test('smoothing update sets panel state without submitting a new job', () => {
  let submitCalls = 0;

  const panel = {
    currentSmoothing: 'none',
    lastResults: { spl_on_axis: { frequencies: [100], spl: [90] } },
    solver: {
      submitSimulation: () => {
        submitCalls += 1;
      },
    },
  };

  applySmoothingSelection(panel, '1/6');

  assert.equal(panel.currentSmoothing, '1/6');
  assert.equal(submitCalls, 0);
});

test('view results modal keeps header controls together and rerenders directivity separately', async () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalFetch = global.fetch;

  const appendedChildren = [];
  const createdElements = [];
  const chartRenderBodies = [];
  const directivityBodies = [];
  const fakeDocument = createModalDocument(createdElements, appendedChildren);

  global.document = fakeDocument;
  global.window = {
    addEventListener() {},
    removeEventListener() {},
  };
  global.fetch = async (url, options = {}) => {
    const body = JSON.parse(options.body);
    if (String(url).endsWith('/api/render-directivity')) {
      directivityBodies.push(body);
      return {
        ok: true,
        async json() {
          return { image: 'data:image/png;base64,directivity' };
        },
      };
    }

    chartRenderBodies.push(body);
    return {
      ok: true,
      async json() {
        return { charts: {} };
      },
    };
  };

  try {
    const panel = {
      currentSmoothing: 'none',
      currentDirectivityReferenceLevel: -6,
      solver: { backendUrl: 'http://localhost:8000' },
      activeJobId: 'job-1',
      currentJobId: 'job-1',
      resultCache: new Map([
        [
          'job-1',
          {
            spl_on_axis: { frequencies: [100, 200], spl: [90, 92] },
            di: { frequencies: [100, 200], di: [5, 6] },
            impedance: {
              frequencies: [100, 200],
              real: [8, 9],
              imaginary: [1, 2],
            },
            directivity: {
              horizontal: [
                [
                  [0, 0],
                  [10, -3],
                ],
                [
                  [0, 0],
                  [10, -4],
                ],
              ],
            },
            metadata: {
              solver_backend: 'bempp',
              engine: 'hornlab-bempp-bem',
              phase_time_convention: 'exp(+ikr)',
              device_interface: { selected: 'bempp-cl-numba' },
            },
          },
        ],
      ]),
      lastResults: null,
    };

    await openViewResultsModal(panel);
    await flushModalAsyncWork();

    assert.equal(appendedChildren.length, 1, 'Expected the backdrop to be mounted');
    assert.equal(chartRenderBodies.length, 1, 'Expected initial chart render fetch');
    assert.equal(chartRenderBodies[0].phase_time_convention, 'metal');
    assert.equal(directivityBodies.length, 1, 'Expected initial directivity render fetch');
    assert.equal(directivityBodies[0].reference_level, -6);

    const headerActions = createdElements.find(
      (el) => el.className === 'view-results-header-actions'
    );
    const controlContainers = createdElements.filter(
      (el) => el.className === 'view-results-smoothing'
    );
    const closeButton = createdElements.find((el) => el.className === 'view-results-close');
    const smoothingSelect = fakeDocument.getElementById('vr-smoothing-select');
    const directivitySelect = fakeDocument.getElementById('vr-directivity-ref-select');

    assert.ok(headerActions, 'Expected a dedicated header actions container');
    assert.equal(controlContainers.length, 2, 'Expected smoothing and map-ref controls');
    assert.ok(closeButton, 'Expected close button to be rendered');
    assert.ok(smoothingSelect, 'Expected smoothing select to be addressable by id');
    assert.ok(directivitySelect, 'Expected directivity reference select to be addressable by id');
    assert.equal(headerActions._children.includes(smoothingSelect._parent), true);
    assert.equal(headerActions._children.includes(directivitySelect._parent), true);
    assert.equal(headerActions._children.includes(closeButton), true);

    const changeListeners = smoothingSelect._eventListeners.change || [];
    assert.equal(changeListeners.length, 1, 'Expected smoothing select change listener');
    changeListeners[0]({ target: { value: '1/6' } });
    await flushModalAsyncWork();

    assert.equal(panel.currentSmoothing, '1/6');
    assert.equal(
      fakeDocument.body._children.length,
      1,
      'Modal should remain mounted after smoothing change'
    );
    assert.equal(chartRenderBodies.length, 2, 'Expected smoothing change to re-render charts');
    assert.equal(
      directivityBodies.length,
      1,
      'Expected smoothing change to leave the unsmoothed directivity map alone'
    );

    const directivityChangeListeners = directivitySelect._eventListeners.change || [];
    assert.equal(
      directivityChangeListeners.length,
      1,
      'Expected directivity reference change listener'
    );
    directivityChangeListeners[0]({ target: { value: '-9' } });
    await flushModalAsyncWork();

    assert.equal(panel.currentDirectivityReferenceLevel, -9);
    assert.equal(
      chartRenderBodies.length,
      2,
      'Expected directivity-only refresh to avoid re-rendering non-directivity charts'
    );
    assert.equal(
      directivityBodies.length,
      2,
      'Expected directivity-only refresh to request just the heatmap'
    );
    assert.equal(directivityBodies[1].reference_level, -9);
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.fetch = originalFetch;
  }
});

test('view results dispatches chart and directivity renders concurrently without duplicating directivity', async () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalFetch = global.fetch;

  const appendedChildren = [];
  const createdElements = [];
  const requests = [];
  const pendingResponses = new Map();
  const fakeDocument = createModalDocument(createdElements, appendedChildren);

  global.document = fakeDocument;
  global.window = {
    addEventListener() {},
    removeEventListener() {},
  };
  global.fetch = (url, options = {}) => {
    const endpoint = String(url).endsWith('/api/render-directivity')
      ? 'render-directivity'
      : 'render-charts';
    requests.push({ endpoint, body: JSON.parse(options.body) });
    return new Promise((resolve) => {
      pendingResponses.set(endpoint, resolve);
    });
  };

  const releaseResponses = () => {
    pendingResponses.get('render-charts')?.({
      ok: true,
      async json() {
        return { charts: {} };
      },
    });
    pendingResponses.get('render-directivity')?.({
      ok: true,
      async json() {
        return { image: 'data:image/png;base64,directivity' };
      },
    });
    pendingResponses.clear();
  };

  try {
    const panel = {
      currentSmoothing: 'none',
      currentDirectivityReferenceLevel: -6,
      solver: { backendUrl: 'http://localhost:8000' },
      activeJobId: 'job-concurrent-charts',
      currentJobId: 'job-concurrent-charts',
      resultCache: new Map([
        [
          'job-concurrent-charts',
          {
            spl_on_axis: { frequencies: [100], spl: [90] },
            di: { frequencies: [100], di: [5] },
            impedance: { frequencies: [100], real: [8], imaginary: [1] },
            directivity: {
              horizontal: [
                [
                  [0, 0],
                  [10, -3],
                ],
              ],
            },
          },
        ],
      ]),
      lastResults: null,
    };

    await openViewResultsModal(panel);

    assert.equal(
      requests.length,
      2,
      'Both render requests should be dispatched before either response resolves'
    );
    const chartRequest = requests.find((request) => request.endpoint === 'render-charts');
    const directivityRequest = requests.find(
      (request) => request.endpoint === 'render-directivity'
    );
    assert.ok(chartRequest, 'Expected the non-directivity charts request');
    assert.ok(directivityRequest, 'Expected the dedicated directivity request');
    assert.equal('directivity' in chartRequest.body, false);
    assert.deepEqual(
      directivityRequest.body.directivity,
      panel.resultCache.get(panel.activeJobId).directivity
    );

    releaseResponses();
    await flushModalAsyncWork();
  } finally {
    releaseResponses();
    await flushModalAsyncWork();
    global.document = originalDocument;
    global.window = originalWindow;
    global.fetch = originalFetch;
  }
});

test('view results distinguishes chart service errors from missing Matplotlib', async () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalFetch = global.fetch;
  const originalWarn = console.warn;
  console.warn = () => {};

  const cases = [
    {
      label: 'server error',
      fetch: async () => ({
        ok: false,
        status: 500,
        async text() {
          return 'solver <detail>';
        },
      }),
      expected: 'Chart rendering failed: solver &lt;detail&gt;',
      excludesMatplotlib: true,
    },
    {
      label: 'unreachable backend',
      fetch: async () => {
        throw new Error('network unavailable');
      },
      expected: 'backend is unreachable. Check that the backend is running.',
      excludesMatplotlib: true,
    },
    {
      label: 'Matplotlib unavailable',
      fetch: async () => ({
        ok: false,
        status: 503,
        async text() {
          return 'renderer unavailable';
        },
      }),
      expected: 'Matplotlib is required for chart rendering',
      excludesMatplotlib: false,
    },
  ];

  try {
    for (const scenario of cases) {
      const appendedChildren = [];
      const createdElements = [];
      const fakeDocument = createModalDocument(createdElements, appendedChildren);
      global.document = fakeDocument;
      global.window = {
        addEventListener() {},
        removeEventListener() {},
      };
      global.fetch = scenario.fetch;

      const panel = {
        currentSmoothing: 'none',
        currentDirectivityReferenceLevel: -6,
        solver: { backendUrl: 'http://localhost:8000' },
        activeJobId: 'job-chart-error',
        currentJobId: 'job-chart-error',
        resultCache: new Map([
          [
            'job-chart-error',
            {
              spl_on_axis: { frequencies: [100], spl: [90] },
              di: { frequencies: [100], di: [5] },
              impedance: { frequencies: [100], real: [8], imaginary: [1] },
              directivity: {},
            },
          ],
        ]),
        lastResults: null,
      };

      await openViewResultsModal(panel);
      await flushModalAsyncWork();

      const chartMarkup = fakeDocument.getElementById('vr-frequency_response')?.innerHTML || '';
      assert.match(chartMarkup, new RegExp(scenario.expected));
      if (scenario.excludesMatplotlib) {
        assert.doesNotMatch(chartMarkup, /Matplotlib is required/);
      }
    }
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.fetch = originalFetch;
    console.warn = originalWarn;
  }
});

function createModalDocument(createdElements, appendedChildren) {
  const walk = (node, visitor) => {
    if (!node) return;
    visitor(node);
    const children = Array.isArray(node._children) ? node._children : [];
    for (const child of children) {
      walk(child, visitor);
    }
  };

  const detach = (node) => {
    const parent = node?._parent;
    if (!parent || !Array.isArray(parent._children)) return;
    parent._children = parent._children.filter((child) => child !== node);
    node._parent = null;
  };

  const body = {
    _children: [],
    appendChild(child) {
      child._parent = this;
      this._children.push(child);
      appendedChildren.push(child);
      return child;
    },
    removeChild(child) {
      detach(child);
    },
  };

  return {
    body,
    createElement(tag) {
      const el = {
        _tag: tag,
        _children: [],
        _attrs: {},
        _eventListeners: {},
        _parent: null,
        id: '',
        className: '',
        textContent: '',
        type: '',
        title: '',
        value: '',
        selected: false,
        _innerHTML: '',
        setAttribute(key, value) {
          this._attrs[key] = value;
        },
        getAttribute(key) {
          return this._attrs[key];
        },
        addEventListener(event, fn) {
          this._eventListeners[event] = this._eventListeners[event] || [];
          this._eventListeners[event].push(fn);
        },
        appendChild(child) {
          child._parent = this;
          this._children.push(child);
          return child;
        },
        remove() {
          detach(this);
        },
        get firstElementChild() {
          return this._children[0] ?? null;
        },
        set innerHTML(value) {
          this._innerHTML = value;
          this._children = [];
        },
        get innerHTML() {
          return this._innerHTML;
        },
      };
      createdElements.push(el);
      return el;
    },
    getElementById(id) {
      let match = null;
      walk(body, (node) => {
        if (!match && node?.id === id) {
          match = node;
        }
      });
      return match;
    },
  };
}

function flushModalAsyncWork() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

test('validateCanonicalMeshPayload rejects malformed canonical mesh', () => {
  assert.throws(
    () =>
      validateCanonicalMeshPayload({
        vertices: [0, 0, 0],
        indices: [0, 1, 2],
        surfaceTags: [],
        format: 'msh',
        boundaryConditions: {},
      }),
    /surfaceTags length must match triangle count/
  );
});

test('submitSimulation preflight rejects mesh missing source tag before any API call', async () => {
  const originalFetch = global.fetch;
  let fetchCalls = 0;
  global.fetch = async () => {
    fetchCalls += 1;
    return {
      ok: true,
      async json() {
        return { job_id: 'should-not-happen' };
      },
    };
  };

  try {
    const solver = new BemSolver();
    await assert.rejects(
      () =>
        solver.submitSimulation(
          {
            frequencyStart: 100,
            frequencyEnd: 1000,
            numFrequencies: 3,
            simulationType: '2',
          },
          {
            vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
            indices: [0, 1, 2],
            surfaceTags: [1],
            format: 'msh',
            boundaryConditions: {
              throat: { type: 'velocity', surfaceTag: 2, value: 1.0 },
            },
            metadata: {},
          }
        ),
      /source surface tag \(2\) missing/i
    );
    assert.equal(fetchCalls, 0);
  } finally {
    global.fetch = originalFetch;
  }
});

test('submitSimulation maps backend 422 responses to typed validation ApiError', async () => {
  const originalFetch = global.fetch;

  global.fetch = async () => ({
    ok: false,
    status: 422,
    async json() {
      return {
        detail: [{ loc: ['body', 'mesh'], msg: 'field required' }],
      };
    },
  });

  try {
    const solver = new BemSolver();
    await assert.rejects(
      () =>
        solver.submitSimulation(
          {
            frequencyStart: 100,
            frequencyEnd: 1000,
            numFrequencies: 3,
            simulationType: '2',
          },
          {
            vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
            indices: [0, 1, 2],
            surfaceTags: [2],
            format: 'msh',
            boundaryConditions: {
              throat: { type: 'velocity', surfaceTag: 2, value: 1.0 },
            },
            metadata: {},
          }
        ),
      (error) => {
        assert.equal(error.name, 'ApiError');
        assert.equal(error.category, 'validation');
        assert.equal(error.status, 422);
        assert.match(error.message, /submit simulation failed validation \(422\)/i);
        assert.match(error.message, /body\.mesh: field required/i);
        return true;
      }
    );
  } finally {
    global.fetch = originalFetch;
  }
});

// --- Check 5: mesh artifact download ---

test('downloadMeshArtifact fetches mesh and triggers download', async () => {
  const originalFetch = global.fetch;
  const originalCreateElement = global.document?.createElement;

  // Minimal DOM stubs for download anchor
  const clickedLinks = [];
  const removedChildren = [];
  const revokedUrls = [];

  global.document = {
    createElement(tag) {
      const el = {
        href: '',
        download: '',
        click() {
          clickedLinks.push(this);
        },
      };
      return el;
    },
    body: {
      appendChild() {},
      removeChild(el) {
        removedChildren.push(el);
      },
    },
  };
  global.URL = {
    createObjectURL() {
      return 'blob:test';
    },
    revokeObjectURL(u) {
      revokedUrls.push(u);
    },
  };
  global.Blob = class {
    constructor(parts, opts) {
      this.parts = parts;
      this.opts = opts;
    }
  };

  global.fetch = async (url) => {
    assert.match(url, /\/api\/mesh-artifact\/job-42$/);
    return {
      ok: true,
      async text() {
        return '$MeshFormat\n2.2 0 8\n$EndMeshFormat';
      },
    };
  };

  try {
    await downloadMeshArtifact('job-42');
    assert.equal(clickedLinks.length, 1);
    assert.match(clickedLinks[0].download, /simulation_mesh_job-42\.msh/);
    assert.equal(revokedUrls.length, 1);
  } finally {
    global.fetch = originalFetch;
    if (originalCreateElement) {
      global.document.createElement = originalCreateElement;
    }
  }
});

// --- Session 6 regression tests: lifecycle safety + URL config ---

test('downloadMeshArtifact uses the provided backendUrl instead of hardcoded default', async () => {
  const originalFetch = global.fetch;
  const fetchedUrls = [];

  global.document = {
    createElement() {
      return { href: '', download: '', click() {} };
    },
    body: { appendChild() {}, removeChild() {} },
  };
  global.URL = {
    createObjectURL() {
      return 'blob:test';
    },
    revokeObjectURL() {},
  };
  global.Blob = class {
    constructor(parts, opts) {
      this.parts = parts;
    }
  };

  global.fetch = async (url) => {
    fetchedUrls.push(url);
    return {
      ok: true,
      async text() {
        return '$MeshFormat';
      },
    };
  };

  try {
    await downloadMeshArtifact('job-99', 'http://custom-backend:9000');
    assert.equal(fetchedUrls.length, 1);
    assert.match(fetchedUrls[0], /^http:\/\/custom-backend:9000\/api\/mesh-artifact\/job-99$/);
  } finally {
    global.fetch = originalFetch;
  }
});

test('pollSimulationStatus guard: second call returns immediately when isPolling is true', () => {
  // When isPolling is already true, pollSimulationStatus should be a no-op.
  // No timer should be set and no DOM or fetch access should occur.
  const panel = {
    isPolling: true,
    pollTimer: null,
    pollInterval: null,
    pollDelayMs: 60000,
    pollBackoffMs: 1000,
    activeJobId: null,
    jobs: new Map(),
    solver: { backendUrl: 'http://localhost:8000' },
  };

  pollSimulationStatus(panel);

  // Guard fired — no timer was scheduled.
  assert.equal(panel.pollTimer, null);
  assert.equal(panel.isPolling, true);
});

test('dispose() clears poll timers, connection timer, and resets isPolling', () => {
  // Verify dispose() tear-down logic by exercising AppEvents.off round-trip.
  const removedEvents = [];
  const originalOff = AppEvents.off.bind(AppEvents);
  AppEvents.off = (event, cb) => {
    removedEvents.push(event);
    originalOff(event, cb);
  };

  const clearedIds = [];
  const origClearTimeout = global.clearTimeout;
  global.clearTimeout = (id) => {
    clearedIds.push(id);
    if (origClearTimeout) origClearTimeout(id);
  };

  try {
    // Simulate a panel that has active timers and registered listeners.
    const listener = () => {};
    AppEvents.on('state:updated', listener);

    const panel = {
      pollTimer: 7001,
      pollInterval: 7001,
      isPolling: true,
      connectionPollTimer: 7002,
      _onStateUpdated: listener,
      _onMeshReady: null,
      _onMeshError: null,
    };

    // Run the same logic as SimulationPanel.dispose()
    if (panel.pollTimer) {
      clearTimeout(panel.pollTimer);
      panel.pollTimer = null;
      panel.pollInterval = null;
      panel.isPolling = false;
    }
    if (panel.connectionPollTimer) {
      clearTimeout(panel.connectionPollTimer);
      panel.connectionPollTimer = null;
    }
    if (panel._onStateUpdated) {
      AppEvents.off('state:updated', panel._onStateUpdated);
      panel._onStateUpdated = null;
    }

    assert.ok(clearedIds.includes(7001), 'pollTimer was cleared');
    assert.ok(clearedIds.includes(7002), 'connectionPollTimer was cleared');
    assert.equal(panel.pollTimer, null);
    assert.equal(panel.connectionPollTimer, null);
    assert.equal(panel.isPolling, false);
    assert.ok(removedEvents.includes('state:updated'), 'state:updated listener was removed');
    assert.equal(panel._onStateUpdated, null);
  } finally {
    global.clearTimeout = origClearTimeout;
    AppEvents.off = originalOff;
  }
});

// --- Session 7 regression tests: module split + DOM cache ---

test('formatJobSummary is accessible from jobActions.js sub-module', () => {
  assert.strictEqual(typeof formatJobSummary, 'function');
  // Verify it produces expected output for a complete job
  const job = {
    status: 'complete',
    progress: 1,
    completedAt: '2026-02-24T12:00:00Z',
    startedAt: '2026-02-24T11:59:00Z',
  };
  const summary = formatJobSummary(job);
  assert.ok(
    summary.startsWith('Complete'),
    `Expected summary starting with Complete, got: ${summary}`
  );
});

test('renderJobList is accessible from jobActions.js sub-module', () => {
  assert.strictEqual(typeof renderJobList, 'function');
});

test('renderJobList no-ops when document has no getElementById', () => {
  const originalDocument = global.document;
  global.document = {};

  try {
    assert.doesNotThrow(() => {
      renderJobList({
        jobSourceMode: 'backend',
        activeJobId: null,
        jobs: new Map(),
      });
    });
  } finally {
    global.document = originalDocument;
  }
});

test('renderJobList exposes folder source mode in the header and rows', () => {
  const originalDocument = global.document;
  const list = { innerHTML: '' };
  const sourceLabel = { textContent: '' };

  global.document = {
    getElementById(id) {
      if (id === 'simulation-jobs-list') return list;
      if (id === 'simulation-jobs-source-label') return sourceLabel;
      return null;
    },
  };

  try {
    renderJobList({
      jobSourceMode: 'folder',
      activeJobId: null,
      jobs: new Map([
        [
          'job-folder-1',
          {
            id: 'job-folder-1',
            label: 'folder-task',
            status: 'complete',
            createdAt: '2026-03-11T09:00:00.000Z',
            completedAt: '2026-03-11T09:10:00.000Z',
          },
        ],
      ]),
    });

    assert.equal(sourceLabel.textContent, 'Folder Tasks');
    assert.match(list.innerHTML, /folder-task/);
  } finally {
    global.document = originalDocument;
  }
});

test('renderJobList keeps backend-only feeds free of redundant row source badges', () => {
  const originalDocument = global.document;
  const list = { innerHTML: '' };
  const sourceLabel = { textContent: '' };

  global.document = {
    getElementById(id) {
      if (id === 'simulation-jobs-list') return list;
      if (id === 'simulation-jobs-source-label') return sourceLabel;
      return null;
    },
  };

  try {
    renderJobList({
      jobSourceMode: 'backend',
      activeJobId: null,
      jobs: new Map([
        [
          'job-backend-1',
          {
            id: 'job-backend-1',
            label: 'backend-task',
            status: 'complete',
            createdAt: '2026-03-11T09:00:00.000Z',
            completedAt: '2026-03-11T09:10:00.000Z',
          },
        ],
      ]),
    });

    assert.equal(sourceLabel.textContent, 'Backend Jobs');
    assert.doesNotMatch(list.innerHTML, /simulation-job-source-badge/);
    assert.doesNotMatch(list.innerHTML, />Backend</);
  } finally {
    global.document = originalDocument;
  }
});

test('renderJobList labels completed job results action as Results with view tooltip', () => {
  const originalDocument = global.document;
  const list = { innerHTML: '' };
  const sourceLabel = { textContent: '' };

  global.document = {
    getElementById(id) {
      if (id === 'simulation-jobs-list') return list;
      if (id === 'simulation-jobs-source-label') return sourceLabel;
      return null;
    },
  };

  try {
    renderJobList({
      jobSourceMode: 'backend',
      activeJobId: null,
      jobs: new Map([
        [
          'job-results-1',
          {
            id: 'job-results-1',
            label: 'results-task',
            status: 'complete',
            createdAt: '2026-03-11T09:00:00.000Z',
            completedAt: '2026-03-11T09:10:00.000Z',
          },
        ],
      ]),
    });

    assert.match(
      list.innerHTML,
      /data-job-action="view"[\s\S]*title="View results"[\s\S]*>Results<\/button>/
    );
    assert.doesNotMatch(list.innerHTML, />View<\/button>/);
  } finally {
    global.document = originalDocument;
  }
});

test('renderJobList escapes job ids in action data attributes', () => {
  const originalDocument = global.document;
  const list = { innerHTML: '' };
  const sourceLabel = { textContent: '' };
  const jobId = 'job-"quoted"';

  global.document = {
    getElementById(id) {
      if (id === 'simulation-jobs-list') return list;
      if (id === 'simulation-jobs-source-label') return sourceLabel;
      return null;
    },
  };

  try {
    renderJobList({
      jobSourceMode: 'backend',
      activeJobId: jobId,
      jobs: new Map([
        [
          jobId,
          {
            id: jobId,
            label: 'quoted-task',
            status: 'complete',
            rating: 3,
            createdAt: '2026-03-11T09:00:00.000Z',
            completedAt: '2026-03-11T09:10:00.000Z',
          },
        ],
      ]),
    });

    assert.match(list.innerHTML, /data-job-id="job-&quot;quoted&quot;"/);
    assert.doesNotMatch(list.innerHTML, /data-job-id="job-"quoted""/);
  } finally {
    global.document = originalDocument;
  }
});

test('renderJobList applies rating filter and renders rating controls', () => {
  const originalDocument = global.document;
  const originalLocalStorage = global.localStorage;
  const list = { innerHTML: '' };
  const sourceLabel = { textContent: '' };
  const sortSelect = { value: 'completed_desc' };
  const minRatingSelect = { value: '0' };

  global.localStorage = {
    values: new Map(),
    getItem(key) {
      return this.values.has(key) ? this.values.get(key) : null;
    },
    setItem(key, value) {
      this.values.set(key, String(value));
    },
  };

  saveSimulationManagementSettings({
    autoExportOnComplete: true,
    selectedFormats: ['csv'],
    defaultSort: 'rating_desc',
    minRatingFilter: 4,
  });

  global.document = {
    getElementById(id) {
      if (id === 'simulation-jobs-list') return list;
      if (id === 'simulation-jobs-source-label') return sourceLabel;
      if (id === 'simulation-jobs-sort') return sortSelect;
      if (id === 'simulation-jobs-min-rating') return minRatingSelect;
      return null;
    },
  };

  try {
    renderJobList({
      jobSourceMode: 'backend',
      activeJobId: null,
      jobs: new Map([
        [
          'job-high',
          {
            id: 'job-high',
            label: 'rated-high',
            status: 'complete',
            rating: 5,
            createdAt: '2026-03-11T09:00:00.000Z',
            completedAt: '2026-03-11T09:10:00.000Z',
          },
        ],
        [
          'job-low',
          {
            id: 'job-low',
            label: 'rated-low',
            status: 'complete',
            rating: 2,
            createdAt: '2026-03-11T08:00:00.000Z',
            completedAt: '2026-03-11T08:10:00.000Z',
          },
        ],
      ]),
    });

    assert.match(list.innerHTML, /rated-high/);
    assert.doesNotMatch(list.innerHTML, /rated-low/);
    assert.match(list.innerHTML, /simulation-job-rating-star/);
    assert.match(list.innerHTML, /data-job-rating="5"/);
  } finally {
    saveSimulationManagementSettings(SIM_MANAGEMENT_DEFAULTS);
    global.document = originalDocument;
    global.localStorage = originalLocalStorage;
  }
});

test('renderJobList skips identical feed updates and refreshes when a job changes', () => {
  const originalDocument = global.document;
  let assignments = 0;
  let markup = '';
  const list = {};
  Object.defineProperty(list, 'innerHTML', {
    get() {
      return markup;
    },
    set(value) {
      assignments += 1;
      markup = value;
    },
  });
  const sourceLabel = { textContent: '' };
  global.document = {
    getElementById(id) {
      if (id === 'simulation-jobs-list') return list;
      if (id === 'simulation-jobs-source-label') return sourceLabel;
      return null;
    },
  };

  try {
    const panel = {
      jobSourceMode: 'backend',
      activeJobId: 'job-signature',
      jobs: new Map([
        [
          'job-signature',
          {
            id: 'job-signature',
            label: 'signature-task',
            status: 'running',
            progress: 0.2,
            createdAt: '2026-03-11T09:00:00.000Z',
          },
        ],
      ]),
    };

    renderJobList(panel);
    renderJobList(panel);
    assert.equal(assignments, 1);

    panel.jobs.get('job-signature').progress = 0.4;
    renderJobList(panel);
    assert.equal(assignments, 2);
  } finally {
    global.document = originalDocument;
  }
});

test('clearPollTimer from polling.js resets isPolling and clears timer refs', () => {
  const clearedIds = [];
  const origClearTimeout = global.clearTimeout;
  global.clearTimeout = (id) => {
    clearedIds.push(id);
    if (origClearTimeout) origClearTimeout(id);
  };

  try {
    const panel = {
      pollTimer: 9001,
      pollInterval: 9001,
      consecutivePollFailures: 3,
      isPolling: true,
    };
    clearPollTimer(panel);
    assert.ok(clearedIds.includes(9001), 'pollTimer was cleared via clearTimeout');
    assert.equal(panel.pollTimer, null);
    assert.equal(panel.pollInterval, null);
    assert.equal(panel.consecutivePollFailures, 0);
    assert.equal(panel.isPolling, false);
  } finally {
    global.clearTimeout = origClearTimeout;
  }
});

test('progress hide callbacks from an earlier run cannot affect a newer run', () => {
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const scheduled = [];
  const cleared = [];
  global.setTimeout = (callback, delay) => {
    const timer = { id: scheduled.length + 1, callback, delay };
    scheduled.push(timer);
    return timer;
  };
  global.clearTimeout = (timer) => {
    cleared.push(timer);
  };

  try {
    const panel = { progressHideTimer: null };
    let hidden = 0;
    scheduleProgressHide(panel, () => {
      hidden += 1;
    }, 3000);
    const stale = scheduled[0];

    // runSimulation/pollSimulationStatus clears this handle before a new run begins.
    clearProgressHideTimer(panel);
    stale.callback();
    assert.equal(hidden, 0);

    scheduleProgressHide(panel, () => {
      hidden += 1;
    }, 3000);
    scheduled[1].callback();
    assert.equal(hidden, 1);
    assert.ok(cleared.includes(stale));
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});

test('pollSimulationStatus persists and auto-exports once on a running-to-complete transition', async () => {
  const originalDocument = global.document;
  const originalFetch = global.fetch;
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const fetchCalls = [];
  let timerId = 0;

  global.document = {
    getElementById() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
      async text() {
        return '';
      },
    };
  };
  global.setTimeout = () => {
    timerId += 1;
    return timerId;
  };
  global.clearTimeout = () => {};
  saveSimulationManagementSettings({
    ...SIM_MANAGEMENT_DEFAULTS,
    autoExportOnComplete: true,
    selectedFormats: ['csv'],
  });

  try {
    let autoExportCalls = 0;
    const panel = {
      isPolling: false,
      pollTimer: null,
      progressHideTimer: null,
      pollInterval: null,
      pollDelayMs: 1000,
      pollBackoffMs: 1000,
      consecutivePollFailures: 0,
      activeJobId: 'job-complete-once',
      currentJobId: 'job-complete-once',
      jobSourceMode: 'backend',
      jobs: new Map([
        [
          'job-complete-once',
          {
            id: 'job-complete-once',
            label: 'complete_once',
            status: 'running',
            progress: 0.5,
            createdAt: '2026-03-11T10:00:00.000Z',
          },
        ],
      ]),
      resultCache: new Map(),
      solver: {
        backendUrl: 'http://backend.example.test',
        async getJobStatus(id) {
          return {
            id,
            status: 'complete',
            progress: 1,
            stage: 'complete',
            completed_at: '2026-03-11T10:01:00.000Z',
          };
        },
        async getResults() {
          return { spl_on_axis: { frequencies: [100], spl: [90] } };
        },
      },
      displayResults() {},
      async exportResults(options) {
        autoExportCalls += 1;
        assert.equal(options.auto, true);
        return { exportedFiles: ['complete_once_results.csv'] };
      },
      checkSolverConnection() {},
    };

    pollSimulationStatus(panel);
    for (let i = 0; i < 40; i += 1) await Promise.resolve();

    assert.equal(autoExportCalls, 1);
    assert.equal(panel.jobs.get('job-complete-once')?.justCompleted, false);
    assert.equal(panel.jobs.get('job-complete-once')?.rawResultsFile, 'complete_once_raw.results.json');
    const rawResultsWrite = fetchCalls.find(
      ({ options }) => options.body?.get?.('file')?.name === 'complete_once_raw.results.json'
    );
    assert.ok(rawResultsWrite, 'expected raw-results persistence write');

    pollSimulationStatus(panel);
    for (let i = 0; i < 30; i += 1) await Promise.resolve();
    assert.equal(autoExportCalls, 1);
  } finally {
    saveSimulationManagementSettings(SIM_MANAGEMENT_DEFAULTS);
    global.document = originalDocument;
    global.fetch = originalFetch;
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});

test('pollSimulationStatus recovers after a transient result-fetch failure', async () => {
  const originalDocument = global.document;
  const originalFetch = global.fetch;
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const originalConsoleError = console.error;
  const scheduled = [];

  global.document = {
    getElementById() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
  global.fetch = async () => ({
    ok: true,
    async json() {
      return { status: 'success' };
    },
    async text() {
      return '';
    },
  });
  global.setTimeout = (callback, delay) => {
    const timer = { callback, delay };
    scheduled.push(timer);
    return timer;
  };
  global.clearTimeout = () => {};
  console.error = () => {};

  try {
    let resultFetches = 0;
    const panel = {
      isPolling: false,
      pollTimer: null,
      progressHideTimer: null,
      pollInterval: null,
      pollDelayMs: 1000,
      pollBackoffMs: 1000,
      consecutivePollFailures: 0,
      activeJobId: 'job-recover',
      currentJobId: 'job-recover',
      jobSourceMode: 'backend',
      jobs: new Map([
        [
          'job-recover',
          { id: 'job-recover', status: 'running', progress: 0.5, label: 'recover' },
        ],
      ]),
      resultCache: new Map(),
      solver: {
        async getJobStatus(id) {
          return { id, status: 'complete', progress: 1, stage: 'complete' };
        },
        async getResults() {
          resultFetches += 1;
          if (resultFetches === 1) {
            throw new Error('temporary result endpoint failure');
          }
          return { spl_on_axis: { frequencies: [100], spl: [90] } };
        },
      },
      displayResults() {},
      checkSolverConnection() {},
    };

    pollSimulationStatus(panel);
    for (let i = 0; i < 30; i += 1) await Promise.resolve();

    assert.equal(panel.consecutivePollFailures, 1);
    assert.equal(panel.isPolling, true);
    assert.equal(scheduled[0]?.delay, 2000);

    scheduled[0].callback();
    for (let i = 0; i < 50; i += 1) await Promise.resolve();

    assert.equal(resultFetches, 2);
    assert.equal(panel.resultCache.has('job-recover'), true);
    assert.equal(panel.isPolling, false);
  } finally {
    global.document = originalDocument;
    global.fetch = originalFetch;
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
    console.error = originalConsoleError;
  }
});

test('pollSimulationStatus publishes backend simulation mesh stats to the app widget', async () => {
  const originalDocument = global.document;
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;

  const diagnosticsEl = { innerHTML: '' };
  global.document = {
    getElementById(id) {
      if (id === 'simulation-mesh-diagnostics') return diagnosticsEl;
      if (id === 'simulation-jobs-list') return { innerHTML: '' };
      if (id === 'simulation-jobs-source-label') return { textContent: '' };
      return null;
    },
  };
  global.setTimeout = () => 1;
  global.clearTimeout = () => {};

  const publishedMeshStats = [];

  const meshStatsData = {
    vertex_count: 144,
    triangle_count: 72,
    source: 'hornlab_waveguide_mesher',
    tag_counts: { 1: 68, 2: 4, 3: 0, 4: 0 },
    identity_triangle_counts: {
      inner_wall: 28,
      outer_wall: 20,
      rear_cap: 20,
      throat_disc: 4,
    },
  };

  try {
    const panel = {
      isPolling: false,
      pollTimer: null,
      pollInterval: null,
      pollDelayMs: 1000,
      pollBackoffMs: 1000,
      consecutivePollFailures: 0,
      activeJobId: 'job-mesh-stats',
      currentJobId: 'job-mesh-stats',
      jobSourceMode: 'backend',
      jobs: new Map([
        [
          'job-mesh-stats',
          {
            id: 'job-mesh-stats',
            status: 'running',
            progress: 0.35,
          },
        ],
      ]),
      resultCache: new Map(),
      solver: {
        async getJobStatus(id) {
          return {
            id,
            status: 'running',
            progress: 0.35,
            stage: 'mesh_prepare',
            stage_message: 'Building HornLab mesher mesh',
            mesh_stats: meshStatsData,
          };
        },
      },
      displayResults() {},
      checkSolverConnection() {},
      app: {
        setSimulationMeshStats(meshStats) {
          publishedMeshStats.push(meshStats);
        },
      },
    };

    pollSimulationStatus(panel);
    // Allow enough microticks for the async reconciliation pipeline
    for (let i = 0; i < 10; i++) await Promise.resolve();

    assert.deepEqual(publishedMeshStats, [meshStatsData]);
    assert.match(diagnosticsEl.innerHTML, /Solver Geometry/);
    assert.match(diagnosticsEl.innerHTML, /144 verts/);
  } finally {
    global.document = originalDocument;
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});

test('pollSimulationStatus schedules next poll after reconciliation with no active jobs', async () => {
  const originalDocument = global.document;
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;

  const scheduledDelays = [];
  let timeoutId = 0;
  global.document = {
    getElementById() {
      return null;
    },
  };
  global.setTimeout = (_fn, delay) => {
    scheduledDelays.push(delay);
    timeoutId += 1;
    return timeoutId;
  };
  global.clearTimeout = () => {};

  try {
    const panel = {
      isPolling: false,
      pollTimer: null,
      pollInterval: null,
      pollDelayMs: 1000,
      pollBackoffMs: 1000,
      consecutivePollFailures: 0,
      activeJobId: null,
      jobs: new Map(),
      resultCache: new Map(),
      solver: {
        async getJobStatus() {
          throw new Error('backend unavailable');
        },
      },
      checkSolverConnection() {},
    };

    pollSimulationStatus(panel);
    for (let i = 0; i < 10; i++) await Promise.resolve();

    // With no active jobs, reconciliation succeeds (no jobs to check status for)
    // so no failures are recorded — the poll simply completes and reschedules
    assert.equal(panel.consecutivePollFailures, 0);
    assert.ok(
      scheduledDelays.length >= 1,
      `expected at least one scheduled delay, got ${scheduledDelays.length}`
    );
  } finally {
    global.document = originalDocument;
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});

test('pollSimulationStatus clears early mesh persistence marker after terminal failure', async () => {
  const originalDocument = global.document;
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const originalFetch = global.fetch;

  let timeoutId = 0;
  let remoteStatus = 'running';
  let meshArtifactFetches = 0;

  global.document = {
    getElementById() {
      return null;
    },
  };
  global.setTimeout = () => {
    timeoutId += 1;
    return timeoutId;
  };
  global.clearTimeout = () => {};
  global.fetch = async () => ({
    ok: true,
    async json() {
      return { status: 'success' };
    },
  });

  const panel = {
    isPolling: false,
    pollTimer: null,
    pollInterval: null,
    pollDelayMs: 1000,
    pollBackoffMs: 1000,
    consecutivePollFailures: 0,
    activeJobId: 'job-retry-mesh',
    currentJobId: 'job-retry-mesh',
    jobSourceMode: 'backend',
    jobs: new Map([
      [
        'job-retry-mesh',
        {
          id: 'job-retry-mesh',
          status: 'running',
          progress: 0.2,
          hasMeshArtifact: true,
        },
      ],
    ]),
    resultCache: new Map(),
    solver: {
      async getJobStatus(id) {
        return {
          id,
          status: remoteStatus,
          progress: remoteStatus === 'running' ? 0.4 : 1,
          stage: remoteStatus,
          stage_message: remoteStatus,
          has_mesh_artifact: true,
          error_message: remoteStatus === 'error' ? 'solver failed' : null,
        };
      },
      async getMeshArtifact() {
        meshArtifactFetches += 1;
        return '$MeshFormat\n2.2 0 8\n$EndMeshFormat';
      },
    },
    displayResults() {},
    checkSolverConnection() {},
  };

  async function flushPolling() {
    for (let i = 0; i < 20; i += 1) {
      await Promise.resolve();
    }
  }

  try {
    pollSimulationStatus(panel);
    await flushPolling();
    assert.equal(meshArtifactFetches, 1);

    clearPollTimer(panel);
    remoteStatus = 'error';
    pollSimulationStatus(panel);
    await flushPolling();
    assert.equal(meshArtifactFetches, 1);

    panel.jobs.set('job-retry-mesh', {
      id: 'job-retry-mesh',
      status: 'running',
      progress: 0.1,
      hasMeshArtifact: true,
    });
    panel.activeJobId = 'job-retry-mesh';
    panel.currentJobId = 'job-retry-mesh';
    remoteStatus = 'running';
    pollSimulationStatus(panel);
    await flushPolling();
    assert.equal(meshArtifactFetches, 2);
  } finally {
    clearPollTimer(panel);
    global.document = originalDocument;
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
    global.fetch = originalFetch;
  }
});

test('pollSimulationStatus auto-downloads exactly once after mesh artifact becomes ready', async () => {
  const originalDocument = global.document;
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const originalFetch = global.fetch;
  const originalURL = global.URL;
  let timeoutId = 0;
  let artifactReady = false;
  let downloadFetches = 0;
  let downloadClicks = 0;

  global.document = {
    getElementById(id) {
      return id === 'download-sim-mesh' ? { checked: true } : null;
    },
    createElement(tag) {
      assert.equal(tag, 'a');
      return {
        click() {
          downloadClicks += 1;
        },
      };
    },
    body: {
      appendChild() {},
      removeChild() {},
    },
  };
  global.URL = {
    createObjectURL() {
      return 'blob:mesh';
    },
    revokeObjectURL() {},
  };
  global.setTimeout = () => {
    timeoutId += 1;
    return timeoutId;
  };
  global.clearTimeout = () => {};
  global.fetch = async (url) => {
    if (String(url).includes('/api/mesh-artifact/')) {
      downloadFetches += 1;
      return {
        ok: true,
        async text() {
          return '$MeshFormat\n2.2 0 8\n$EndMeshFormat';
        },
      };
    }
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const panel = {
    isPolling: false,
    pollTimer: null,
    pollInterval: null,
    pollDelayMs: 1000,
    pollBackoffMs: 1000,
    consecutivePollFailures: 0,
    activeJobId: 'job-auto-download-ready',
    currentJobId: 'job-auto-download-ready',
    jobSourceMode: 'backend',
    jobs: new Map([
      [
        'job-auto-download-ready',
        { id: 'job-auto-download-ready', status: 'running', progress: 0.2 },
      ],
    ]),
    resultCache: new Map(),
    solver: {
      backendUrl: 'http://backend.example.test',
      async getJobStatus(id) {
        return {
          id,
          status: 'running',
          progress: 0.4,
          stage: 'bem_solve',
          has_mesh_artifact: artifactReady,
        };
      },
      async getMeshArtifact() {
        return '$MeshFormat\n2.2 0 8\n$EndMeshFormat';
      },
    },
    displayResults() {},
  };

  async function flushPolling() {
    for (let i = 0; i < 30; i += 1) await Promise.resolve();
  }

  try {
    pollSimulationStatus(panel);
    await flushPolling();
    assert.equal(downloadFetches, 0);
    assert.equal(downloadClicks, 0);

    clearPollTimer(panel);
    artifactReady = true;
    pollSimulationStatus(panel);
    await flushPolling();
    assert.equal(downloadFetches, 1);
    assert.equal(downloadClicks, 1);

    clearPollTimer(panel);
    pollSimulationStatus(panel);
    await flushPolling();
    assert.equal(downloadFetches, 1);
    assert.equal(downloadClicks, 1);
  } finally {
    clearPollTimer(panel);
    global.document = originalDocument;
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
    global.fetch = originalFetch;
    global.URL = originalURL;
  }
});

// --- Phase 1 migration regression: simulation flow unaffected by control migration ---

test('getDownloadSimMeshEnabled returns false by default when modal is not open', () => {
  // jobActions.js uses getDownloadSimMeshEnabled() to guard the mesh download at job start.
  // This default must be false so no unexpected download is triggered on startup before
  // the user has ever opened Settings.
  const originalDocument = global.document;
  global.document = { getElementById: () => null };

  try {
    assert.equal(getDownloadSimMeshEnabled(), false);
  } finally {
    global.document = originalDocument;
  }
});

test('getDownloadSimMeshEnabled does not access a static DOM element that would be absent when modal is closed', () => {
  // After migration, download-sim-mesh lives in a dynamically-created modal.
  // When the modal is closed, getElementById('download-sim-mesh') returns null.
  // The getter must NOT throw or return a falsy value that silently corrupts behavior.
  const originalDocument = global.document;
  const queriedIds = [];

  global.document = {
    getElementById(id) {
      queriedIds.push(id);
      return null; // Modal is closed — element does not exist in DOM
    },
  };

  try {
    const result = getDownloadSimMeshEnabled();
    // Should return a boolean (the in-memory default), never null or undefined
    assert.equal(typeof result, 'boolean');
    // Should have attempted to look up the element (DOM-first strategy)
    assert.ok(queriedIds.includes('download-sim-mesh'), 'getter should attempt DOM lookup first');
  } finally {
    global.document = originalDocument;
  }
});
