import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecodedFrame } from '../api/frame';
import type { PreviewSnapshot } from '../api/previewSocket';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from './importedMeshStore';
import { frameToScene } from './frameScene';
import frameFixture from './solverFrame.v2.fixture.json';

const canvasMock = vi.hoisted(() => ({ cameraRequests: [] as Array<{ nonce: number; preset?: string; direction?: number[]; up?: number[] }> }));

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
  errorFields: null,
  errorRevision: null,
};

vi.mock('../api/previewSocket', () => ({
  PREVIEW_FINE_IDLE_MS: 140,
  previewSocket: {
    subscribe: () => () => undefined,
    getSnapshot: () => previewSnapshot,
    refresh: vi.fn(),
    setCurvatureWanted: vi.fn(),
  },
}));

vi.mock('./ViewportCanvas', () => ({
  canRenderWebGL: () => true,
  ViewportCanvas: ({ cameraRequest }: { cameraRequest: { nonce: number } }) => {
    canvasMock.cameraRequests.push(cameraRequest);
    return null;
  },
}));

import { Viewport } from './Viewport';

describe('Viewport camera revision policy', () => {
  let host: HTMLDivElement;
  let root: Root;

  const latestRequest = () => canvasMock.cameraRequests.at(-1)!;
  const latestNonce = () => latestRequest().nonce;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
    canvasMock.cameraRequests.length = 0;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<Viewport />));
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    workspaceModeStore.setMode('parametric');
    resetCadReturnStore();
    importedMeshStore.clear();
  });

  it('keeps the requested view across ordinary edits, undo, redo, and document changes', () => {
    expect(latestNonce()).toBe(0);

    act(() => useDesignStore.getState().updateValue('R', 150));
    expect(latestNonce()).toBe(0);

    act(() => useDesignStore.getState().undo());
    expect(latestNonce()).toBe(0);

    act(() => useDesignStore.getState().redo());
    expect(latestNonce()).toBe(0);

    act(() => useDesignStore.getState().setFamily('OSSE'));
    expect(latestNonce()).toBe(0);

    act(() => useDesignStore.getState().loadDesign(designForFamily('R-OSSE')));
    expect(latestNonce()).toBe(0);
  });

  it('restores the parametric front after a CAD aim and re-aims on return to CAD', () => {
    const ingestId = 'camera-transition';
    const matrix = frameFixture.axes['-y'];
    act(() => {
      useCadSolverFrameStore.setState({ frames: {
        [ingestId]: {
          ingestId, status: 'ready', linked: false, axis: '-y', picked: true,
          changedFrom: null, error: null,
          frame: { axes: [{ axis: '-y', previewFromRecord: matrix, allowed: true }] },
        },
      } as never });
      useCadReturnStore.setState({ ingestRecord: { ingest_id: ingestId } as never });
      importedMeshStore.setCad({
        source: 'cad', ingestId, name: 'CAD', artifactToken: ingestId,
        scene: frameToScene(frame), triangleCount: 1, solvedTriangleCount: 1, physicalGroupCount: 1,
      } as never);
      workspaceModeStore.setMode('cad');
    });
    expect(latestRequest()).toMatchObject({ direction: [0, -1, 0], up: [0, 0, 1] });
    const cadNonce = latestNonce();

    act(() => workspaceModeStore.setMode('parametric'));
    expect(latestRequest()).toMatchObject({ preset: 'front' });
    expect(latestRequest().direction).toBeUndefined();
    expect(latestNonce()).toBeGreaterThan(cadNonce);
    const parametricNonce = latestNonce();

    act(() => root.render(<Viewport />));
    expect(latestNonce()).toBe(parametricNonce);

    act(() => workspaceModeStore.setMode('cad'));
    expect(latestRequest()).toMatchObject({ direction: [0, -1, 0], up: [0, 0, 1] });
    expect(latestNonce()).toBeGreaterThan(parametricNonce);
  });
});
