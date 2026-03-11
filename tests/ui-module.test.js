import test from 'node:test';
import assert from 'node:assert/strict';

import { AppEvents } from '../src/events.js';
import { UiModule } from '../src/modules/ui/index.js';

function makeMeshPayload(overrides = {}) {
  return {
    vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
    indices: [0, 1, 2],
    surfaceTags: [2],
    format: 'msh',
    boundaryConditions: {
      throat: { type: 'velocity', surfaceTag: 2, value: 1.0 },
      wall: { type: 'neumann', surfaceTag: 1, value: 0.0 },
      mouth: { type: 'robin', surfaceTag: 1, impedance: 'spherical' }
    },
    metadata: { fullCircle: true, ringCount: 3 },
    ...overrides
  };
}

test('UiModule app coordinator binds AppEvents and lazily creates the simulation panel', async () => {
  const originalDocument = global.document;
  global.document = {
    getElementById() {
      return null;
    }
  };

  try {
    const calls = [];
    class FakeSimulationPanel {
      constructor() {
        this.created = true;
      }
    }

    const app = {
      simulationPanel: null,
      onStateUpdate(state) {
        calls.push(['state', state]);
      },
      provideMeshForSimulation() {
        calls.push(['mesh']);
      },
      schedulePanelAutoSize() {
        calls.push(['tab']);
      }
    };

    const coordinator = UiModule.output.app(
      UiModule.task(
        UiModule.importApp(app, {
          loadSimulationPanel: async () => ({ SimulationPanel: FakeSimulationPanel })
        })
      )
    );

    coordinator.bind();
    AppEvents.emit('state:updated', { id: 's1' });
    AppEvents.emit('simulation:mesh-requested');
    AppEvents.emit('ui:tab-changed', { tab: 'simulation' });

    const panel = await coordinator.ensureSimulationPanel();
    const samePanel = await coordinator.ensureSimulationPanel();

    assert.deepEqual(calls, [
      ['state', { id: 's1' }],
      ['mesh'],
      ['tab']
    ]);
    assert.ok(panel instanceof FakeSimulationPanel);
    assert.equal(panel.app, app);
    assert.equal(panel, samePanel);
    assert.equal(app.simulationPanel, panel);

    coordinator.dispose();
  } finally {
    global.document = originalDocument;
  }
});

test('UiModule simulation-panel coordinator resolves mesh requests and syncs state updates', async () => {
  const syncedStates = [];
  const requestedEvents = [];
  const onMeshRequested = () => {
    requestedEvents.push('mesh-requested');
  };
  AppEvents.on('simulation:mesh-requested', onMeshRequested);

  try {
    const panel = {
      syncSimulationSettings(state) {
        syncedStates.push(state);
      }
    };

    const coordinator = UiModule.output.simulationPanel(
      UiModule.task(UiModule.importSimulationPanel(panel))
    );

    coordinator.bind();
    AppEvents.emit('state:updated', { freqStart: 100 });

    const meshPromise = coordinator.prepareMesh();
    AppEvents.emit('simulation:mesh-ready', makeMeshPayload());

    const mesh = await meshPromise;

    assert.deepEqual(syncedStates, [{ freqStart: 100 }]);
    assert.equal(requestedEvents.length, 1);
    assert.equal(mesh.format, 'msh');

    coordinator.dispose();
  } finally {
    AppEvents.off('simulation:mesh-requested', onMeshRequested);
  }
});

test('UiModule simulation-panel coordinator removes listeners and rejects pending mesh on dispose', async () => {
  const syncedStates = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const clearedIds = [];

  global.setTimeout = (fn, _delay) => {
    return { fn, id: 41 };
  };
  global.clearTimeout = (id) => {
    clearedIds.push(id);
  };

  try {
    const panel = {
      syncSimulationSettings(state) {
        syncedStates.push(state);
      }
    };

    const coordinator = UiModule.output.simulationPanel(
      UiModule.task(UiModule.importSimulationPanel(panel))
    );

    coordinator.bind();
    const meshPromise = coordinator.prepareMesh(5000);
    coordinator.dispose();

    await assert.rejects(meshPromise, /disposed while waiting for mesh data/i);

    AppEvents.emit('state:updated', { freqEnd: 2000 });
    assert.deepEqual(syncedStates, []);
    assert.equal(clearedIds.length, 1);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});
