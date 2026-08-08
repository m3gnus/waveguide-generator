import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecodedFrame } from '../api/frame';
import type { PreviewSnapshot } from '../api/previewSocket';
import { resetDesignStore, useDesignStore } from '../stores/design';

const fiberMock = vi.hoisted(() => ({ canvasRenders: 0 }));

vi.mock('@react-three/fiber', () => ({
  Canvas: () => {
    fiberMock.canvasRenders += 1;
    return null;
  },
  useFrame: vi.fn(),
  useThree: vi.fn(),
}));

vi.mock('@react-three/drei', () => ({
  GizmoHelper: () => null,
  GizmoViewport: () => null,
  OrbitControls: () => null,
}));

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
  connection: 'connected',
  epoch: 4,
  frame,
  displayedRevision: 1,
  lastValidRevision: 1,
  stale: false,
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

import { resetWebGLProbe } from './ViewportCanvas';
import { Viewport } from './Viewport';

describe('Viewport render isolation', () => {
  let host: HTMLDivElement;
  let root: Root;
  let getContext: ReturnType<typeof vi.spyOn>;
  let webGL2Descriptor: PropertyDescriptor | undefined;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    fiberMock.canvasRenders = 0;
    webGL2Descriptor = Object.getOwnPropertyDescriptor(globalThis, 'WebGL2RenderingContext');
    Object.defineProperty(globalThis, 'WebGL2RenderingContext', { configurable: true, value: class WebGL2RenderingContext {} });
    getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext);
    resetWebGLProbe();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<Viewport />));
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    getContext.mockRestore();
    if (webGL2Descriptor) Object.defineProperty(globalThis, 'WebGL2RenderingContext', webGL2Descriptor);
    else delete (globalThis as Partial<typeof globalThis>).WebGL2RenderingContext;
    resetWebGLProbe();
  });

  it('does not reconcile the Canvas for committed drag updates without a new frame', () => {
    expect(fiberMock.canvasRenders).toBe(1);

    act(() => useDesignStore.getState().updateValue('R', 141));
    act(() => useDesignStore.getState().updateValue('R', 142));
    act(() => useDesignStore.getState().updateValue('R', 143));

    expect(fiberMock.canvasRenders).toBe(1);
  });
});
