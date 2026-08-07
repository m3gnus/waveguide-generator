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
  previewSocket: {
    subscribe: () => () => undefined,
    getSnapshot: () => previewSnapshot,
    refresh: () => refreshCalls.push(1),
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

  it('surfaces a mesher geometry warning over a scene that still renders', () => {
    render([UNREACHABLE]);
    const banner = host.querySelector<HTMLElement>('.viewport-warning-banner');
    expect(banner).not.toBeNull();
    expect(banner?.getAttribute('role')).toBe('status');
    expect(banner?.textContent).toContain('guiding curve unreachable');
    expect(banner?.textContent).toContain('581.3 mm instead of the requested 500.0 mm');
  });

  it('counts the remaining warnings instead of stacking banners', () => {
    render([UNREACHABLE, 'canonical azimuth reference clamped to 4096 samples']);
    const banners = host.querySelectorAll('.viewport-warning-banner');
    expect(banners).toHaveLength(1);
    expect(banners[0]?.textContent).toContain('+1 more');
  });

  it('stays out of the way when the mesher reports nothing', () => {
    render(undefined);
    expect(host.querySelector('.viewport-warning-banner')).toBeNull();
    render([]);
    expect(host.querySelector('.viewport-warning-banner')).toBeNull();
  });

  it('drops below a preview error rather than overlapping it', () => {
    render([UNREACHABLE], { error: 'ATH expression is unsupported' });
    expect(host.querySelector('.viewport-warning-banner')?.className).toContain('below-error');
  });
});
