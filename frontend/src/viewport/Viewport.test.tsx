import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecodedFrame } from '../api/frame';
import type { PreviewSnapshot } from '../api/previewSocket';
import { resetDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';

const frame: DecodedFrame = {
  header: {
    v: 1,
    kind: 'preview',
    epoch: 4,
    seq: 9,
    designRevision: 0,
    lod: 'fine',
    evalMs: 1_388,
    sections: [],
    surfaces: [{
      role: 'horn.inner',
      positions: 'horn.positions',
      normals: 'horn.normals',
      indices: 'horn.indices',
      shading: 'smooth',
      normalMethod: 'analytic-parametric',
    }],
  },
  sections: {
    'horn.positions': new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
    'horn.normals': new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
    'horn.indices': new Uint32Array([0, 1, 2]),
  },
};

const previewSnapshot: PreviewSnapshot = {
  connection: 'connected',
  epoch: 4,
  frame,
  displayedRevision: 0,
  lastValidRevision: 0,
  stale: true,
  dropped: 0,
  error: 'ATH expression is unsupported',
  errorRevision: 0,
};

const refreshCalls: number[] = [];

vi.mock('../api/previewSocket', () => ({
  PREVIEW_FINE_IDLE_MS: 140,
  previewSocket: {
    subscribe: () => () => undefined,
    getSnapshot: () => previewSnapshot,
    refresh: () => refreshCalls.push(1),
    setCurvatureWanted: vi.fn(),
  },
}));

import { Viewport } from './Viewport';

describe('Viewport preview errors', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    useDocumentStore.setState({ filename: 'loaded-design.cfg' });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<Viewport />));
  });

  afterEach(() => {
    act(() => root.unmount());
    useDocumentStore.setState({ filename: 'tritonia_mk2.cfg' });
    host.remove();
  });

  it('shows a dismissible alert while a retained scene is present', () => {
    const alert = host.querySelector<HTMLElement>('[role="alert"]');
    expect(alert?.textContent).toContain('Displayed geometry is not the current design');
    expect(host.textContent).toContain('WebGL unavailable');
    expect(host.textContent).not.toContain('Waiting for geometry');
    expect(host.querySelector('.error-badge')?.textContent).toContain('ERROR');
    expect(host.querySelector('.viewport-title b')?.textContent).toBe('loaded-design');

    act(() => alert?.querySelector<HTMLButtonElement>('[aria-label="Dismiss preview error"]')?.click());
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('offers a retry that re-requests the current design', () => {
    refreshCalls.length = 0;
    const retry = [...host.querySelectorAll<HTMLButtonElement>('[role="alert"] button')]
      .find((button) => button.textContent === 'Retry');
    expect(retry).toBeDefined();
    act(() => retry?.click());
    expect(refreshCalls).toHaveLength(1);
    expect(host.querySelector('.error-badge')?.textContent).toContain('REFRESHING');
  });

  it('offers a refresh beside the badge whenever the view lags the design', () => {
    refreshCalls.length = 0;
    const refresh = host.querySelector<HTMLButtonElement>('.viewport-refresh');
    expect(refresh?.textContent).toContain('Refresh');
    act(() => refresh?.click());
    expect(refreshCalls).toHaveLength(1);
  });

  it('presents geometry evaluation and on-screen time as overlapping timings', () => {
    const timing = [...host.querySelectorAll<HTMLElement>('.viewport-live > span')]
      .find((element) => element.textContent?.startsWith('geometry '));
    // 'request→paint' was developer telemetry sitting in a user-facing readout.
    expect(timing?.textContent).toBe('geometry 1388.0 ms · on screen —');
    expect(timing?.textContent).not.toContain('+ client');
  });
});

describe('Viewport geometry warnings', () => {
  let host: HTMLDivElement;
  let root: Root;
  const savedError = previewSnapshot.error;

  const render = (warnings: string[] | undefined, { error = null as string | null } = {}) => {
    previewSnapshot.error = error;
    frame.header.previewMetadata = warnings ? { warnings } : undefined;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<Viewport />));
  };

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    previewSnapshot.error = savedError;
    frame.header.previewMetadata = undefined;
  });

  // The mesher reports a guiding curve its coverage solver could not reach.
  // The geometry still builds, so nothing else in the viewport would say that
  // the mouth is off the guide shape and the coverage angle is pinned.
  const UNREACHABLE =
    'guiding curve unreachable at every probed azimuth: the coverage angle is pinned at 0.5 deg, '
    + 'so the mouth radius is 581.3 mm instead of the requested 500.0 mm; shorten the horn (Length), '
    + 'reduce the termination shape s, or widen the guiding curve';

  it('keeps a mesher geometry warning behind a compact disclosure', () => {
    render([UNREACHABLE]);
    const warning = host.querySelector<HTMLDetailsElement>('.viewport-warning');
    expect(warning).not.toBeNull();
    expect(warning?.getAttribute('role')).toBe('status');
    expect(warning?.open).toBe(false);
    expect(warning?.querySelector('summary')?.textContent).toContain('1 warning');

    act(() => warning?.querySelector('summary')?.click());
    expect(warning?.open).toBe(true);
    expect(warning?.textContent).toContain('guiding curve unreachable');
    expect(warning?.textContent).toContain('581.3 mm instead of the requested 500.0 mm');
  });

  it('groups all geometry warnings under one compact indicator', () => {
    render([UNREACHABLE, 'canonical azimuth reference clamped to 4096 samples']);
    const warnings = host.querySelectorAll('.viewport-warning');
    expect(warnings).toHaveLength(1);
    expect(warnings[0]?.querySelector('summary')?.textContent).toContain('2 warnings');
    expect(warnings[0]?.querySelectorAll('li')).toHaveLength(2);
  });

  it('stays out of the way when the mesher reports nothing', () => {
    render(undefined);
    expect(host.querySelector('.viewport-warning')).toBeNull();
    act(() => root.unmount());
    host.remove();
    render([]);
    expect(host.querySelector('.viewport-warning')).toBeNull();
  });

  it('stays compact when a preview error is also present', () => {
    render([UNREACHABLE], { error: 'ATH expression is unsupported' });
    expect(host.querySelector('.viewport-error-banner')).not.toBeNull();
    expect(host.querySelector<HTMLDetailsElement>('.viewport-warning')?.open).toBe(false);
  });
});
