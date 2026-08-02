import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';

import { App } from '../src/app/App.js';
import { AppEvents } from '../src/events.js';
import { AppState } from '../src/state.js';
import { UiModule } from '../src/modules/ui/index.js';
import { FreeformProfileEditor } from '../src/ui/freeformProfileEditor.js';
import { getViewportStateCacheKey } from '../src/app/viewportCacheKey.js';

const authoritativeFixture = JSON.parse(
  readFileSync(new URL('./fixtures/freeform-authoritative.json', import.meta.url), 'utf8')
);

function syntheticViewportGrid(rows) {
  const angles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
  const innerPoints = [];
  for (let index = 0; index < angles.length; index += 1) {
    const angle = angles[index];
    const source = index === 1 ? rows.V : rows.H;
    for (const [z, radius] of source) {
      innerPoints.push(radius * Math.cos(angle), radius * Math.sin(angle), z);
    }
  }
  return {
    angle_list: angles,
    grid_n_phi: angles.length,
    grid_n_length: rows.H.length - 1,
    inner_points: innerPoints,
    full_circle: true,
    quadrants: 1234,
    slice_map: Array.from({ length: rows.H.length }, (_, index) => index),
  };
}

async function waitFor(predicate, message, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(message);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

function createHeadlessEditorPanel(app) {
  let onAuthoritative = null;
  return {
    createCount: 0,
    editor: null,
    createFullPanel() {
      if (onAuthoritative) AppEvents.off('freeform:authoritative', onAuthoritative);
      this.createCount += 1;
      const editor = Object.assign(Object.create(FreeformProfileEditor.prototype), {
        authoritative: null,
        cacheKey: getViewportStateCacheKey(app.currentState),
        destroyed: false,
        draw() {},
      });
      onAuthoritative = (payload) => editor.setAuthoritative(payload);
      AppEvents.on('freeform:authoritative', onAuthoritative);
      this.editor = editor;
    },
    setProfileError() {},
    dispose() {
      if (onAuthoritative) AppEvents.off('freeform:authoritative', onAuthoritative);
    },
  };
}

test('FREEFORM storage boot and state update request backend viewport and reach editor', async () => {
  const storedState = {
    type: 'FREEFORM',
    params: { ...authoritativeFixture.params, type: 'FREEFORM' },
  };
  const originalDocument = globalThis.document;
  const originalFetch = globalThis.fetch;
  const originalLocalStorage = globalThis.localStorage;
  const originalRequestAnimationFrame = globalThis.requestAnimationFrame;
  globalThis.document = {
    getElementById(id) {
      // A render-only-on-demand preference must not suppress the only geometry
      // engine available to a server-only formula.
      return id === 'live-update' ? { checked: false } : null;
    },
  };
  globalThis.localStorage = {
    getItem(key) {
      return key === 'ath_state' ? JSON.stringify(storedState) : null;
    },
    setItem() {},
  };
  globalThis.requestAnimationFrame = (callback) => {
    callback();
    return 1;
  };

  const freeform = authoritativeFixture.freeform;
  const grid = syntheticViewportGrid({
    H: [freeform.curveSamples.H[0], freeform.curveSamples.H.at(-1)],
    V: [freeform.curveSamples.V[0], freeform.curveSamples.V.at(-1)],
  });
  const postedRequests = [];
  globalThis.fetch = async (url, options) => {
    postedRequests.push({ url, method: options.method, body: JSON.parse(options.body) });
    return {
      ok: true,
      json: async () => ({
        formula: 'FREEFORM',
        mode: 'bare',
        params: storedState.params,
        grid,
        enclosure: null,
        metadata: { freeform },
      }),
    };
  };

  const state = new AppState();
  const app = Object.assign(Object.create(App.prototype), {
    scene: new THREE.Scene(),
    renderer: {},
    stats: { innerText: '' },
    currentState: null,
    renderRequested: false,
    schedulePanelAutoSize() {},
  });
  const coordinator = UiModule.output.app(
    UiModule.task(
      UiModule.importApp(app, {
        loadSimulationPanel: async () => ({ SimulationPanel: class {} }),
        fileOps: { markParametersChanged() {} },
      })
    )
  );
  app.uiCoordinator = coordinator;
  app.paramPanel = createHeadlessEditorPanel(app);
  coordinator.bind();

  try {
    // Mirrors App's constructor tail after the state singleton loaded storage.
    app.onStateUpdate(state.get());
    await waitFor(
      () => postedRequests.length === 1,
      'FREEFORM boot did not POST a viewport build'
    );
    await waitFor(
      () => app.paramPanel.editor?.authoritative !== null,
      'FREEFORM boot authoritative payload did not reach the editor'
    );
    assert.equal(app.paramPanel.createCount, 1);
    assert.match(postedRequests[0].url, /\/api\/mesh\/viewport$/);
    assert.equal(postedRequests[0].method, 'POST');
    assert.equal(postedRequests[0].body.formula_type, 'FREEFORM');

    app._lastModelRenderAt = 0;
    state.update({ length: state.get().params.length + 1 });
    await waitFor(
      () => postedRequests.length === 2,
      'FREEFORM state update did not POST a viewport rebuild'
    );
    await waitFor(
      () => app.paramPanel.editor?.authoritative !== null,
      'FREEFORM updated authoritative payload did not reach the editor'
    );
    assert.equal(app.paramPanel.createCount, 2);
    assert.equal(app.paramPanel.editor.cacheKey, getViewportStateCacheKey(state.get()));
  } finally {
    coordinator.dispose();
    app.paramPanel.dispose();
    app._viewportFetch?.controller?.abort();
    clearTimeout(app._viewportCooldownRetryTimer);
    globalThis.document = originalDocument;
    globalThis.fetch = originalFetch;
    globalThis.localStorage = originalLocalStorage;
    globalThis.requestAnimationFrame = originalRequestAnimationFrame;
  }
});
