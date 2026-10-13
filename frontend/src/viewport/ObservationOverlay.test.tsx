import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { Matrix4 } from 'three';
import { ObservationOverlay } from './ObservationOverlay';
import { useObservationStore } from './observationStore';
import { DemandRenderScheduler } from './demandRender';

vi.mock('@react-three/fiber', () => ({
  useThree: (select: (state: unknown) => unknown) => select({ gl: { domElement: document.createElement('canvas') }, camera: {} }),
}));

afterEach(() => { useObservationStore.setState({ visible: false, basis: null }); vi.restoreAllMocks(); });

it('fills every new instance buffer when the rig is shown again', () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // Model the renderer-created instance buffer on a fresh host element. This
  // exercises React's actual hide/remount lifecycle without a WebGL context.
  const matrices = new WeakMap<HTMLElement, Map<number, number[]>>();
  Object.defineProperties(HTMLElement.prototype, {
    setMatrixAt: { configurable: true, value(this: HTMLElement, index: number, matrix: Matrix4) {
      if (!matrices.has(this)) matrices.set(this, new Map());
      matrices.get(this)!.set(index, matrix.elements.slice());
    } },
    setColorAt: { configurable: true, value() {} },
    computeBoundingSphere: { configurable: true, value() {} },
    instanceMatrix: { configurable: true, get() { return {}; } },
  });
  const host = document.createElement('div');
  const root = createRoot(host);
  useObservationStore.setState({ visible: false, basis: { axis: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0], origin_m: [0, 0, 0] } });
  try {
    act(() => root.render(<ObservationOverlay unitsPerMetre={1000} scheduler={new DemandRenderScheduler(vi.fn())}/>));
    act(() => useObservationStore.getState().setVisible(true));
    const first = host.querySelector('instancedmesh') as HTMLElement;
    expect(matrices.get(first)?.size).toBeGreaterThan(0);
    const initial = [...matrices.get(first)!.values()];
    act(() => useObservationStore.getState().setVisible(false));
    act(() => useObservationStore.getState().setVisible(true));
    const second = host.querySelector('instancedmesh') as HTMLElement;
    expect(second).not.toBe(first);
    expect([...matrices.get(second)!.values()]).toEqual(initial);
  } finally {
    act(() => root.unmount());
    for (const name of ['setMatrixAt', 'setColorAt', 'computeBoundingSphere', 'instanceMatrix']) {
      delete (HTMLElement.prototype as unknown as Record<string, unknown>)[name];
    }
  }
});
