import test from 'node:test';
import assert from 'node:assert/strict';

import { renderModel } from '../src/app/scene.js';

function makeServerOnlyApp() {
  return {
    scene: {},
    renderer: {},
    currentState: { type: 'ICW', params: {} },
    uiCoordinator: {
      readDisplayModeSetting() {
        return 'solid';
      },
    },
    requestRenderCalls: 0,
    requestRender() {
      this.requestRenderCalls += 1;
    },
  };
}

test('server-only viewport cooldown schedules a retry at cooldown expiry', () => {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const scheduled = [];
  globalThis.setTimeout = (callback, delay) => {
    const timer = { callback, delay };
    scheduled.push(timer);
    return timer;
  };
  globalThis.clearTimeout = () => {};

  try {
    const app = makeServerOnlyApp();
    app._viewportBackendDownAt = Date.now() - 14900;

    renderModel(app);

    assert.equal(scheduled.length, 1);
    assert.ok(scheduled[0].delay >= 0);
    assert.ok(scheduled[0].delay <= 250);

    scheduled[0].callback();
    assert.equal(app._viewportCooldownRetryTimer, null);
    assert.equal(app.requestRenderCalls, 1);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});
