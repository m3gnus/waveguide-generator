import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecodedFrame } from '../api/frame';
import type { PreviewSnapshot } from '../api/previewSocket';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';

const canvasMock = vi.hoisted(() => ({ cameraNonces: [] as number[] }));

const frame: DecodedFrame = {
  header: {
    v: 1,
    kind: 'preview',
    epoch: 4,
    seq: 9,
    designRevision: 1,
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
  connection: 'reconnecting',
  epoch: null,
  frame,
  displayedRevision: 1,
  lastValidRevision: 1,
  stale: true,
  dropped: 0,
  error: null,
  errorRevision: null,
};

vi.mock('../api/previewSocket', () => ({
  PREVIEW_FINE_IDLE_MS: 140,
  previewSocket: {
    subscribe: () => () => undefined,
    getSnapshot: () => previewSnapshot,
    refresh: vi.fn(),
  },
}));

vi.mock('./ViewportCanvas', () => ({
  canRenderWebGL: () => true,
  ViewportCanvas: ({ cameraRequest }: { cameraRequest: { nonce: number } }) => {
    canvasMock.cameraNonces.push(cameraRequest.nonce);
    return null;
  },
}));

import { Viewport } from './Viewport';

describe('Viewport camera revision policy', () => {
  let host: HTMLDivElement;
  let root: Root;

  const latestNonce = () => canvasMock.cameraNonces.at(-1);

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    canvasMock.cameraNonces.length = 0;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<Viewport />));
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  it('resets the requested view for discontinuous revisions but not ordinary edits', () => {
    expect(latestNonce()).toBe(0);

    act(() => useDesignStore.getState().updateValue('R', 150));
    expect(latestNonce()).toBe(0);

    act(() => useDesignStore.getState().undo());
    expect(latestNonce()).toBe(1);

    act(() => useDesignStore.getState().redo());
    expect(latestNonce()).toBe(2);

    act(() => useDesignStore.getState().setFamily('OSSE'));
    expect(latestNonce()).toBe(3);

    act(() => useDesignStore.getState().loadDesign(designForFamily('R-OSSE')));
    expect(latestNonce()).toBe(4);
  });
});
