import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { decodeFemFieldPayload } from './femField';
import { femFieldFixture } from './test-fixtures/femField';
import { femSlicePlane, FemFieldScene } from './FemFieldScene';
import { useFieldPlaneStore } from './fieldPlaneStore';

const mounted = vi.hoisted(() => ({ view: vi.fn() }));
vi.mock('./FemFieldView', () => ({ FemFieldView: (props: unknown) => { mounted.view(props); return null; } }));

const plane = { origin_m: [0, 0, 0.005] as [number, number, number],
  axis_u: [1, 0, 0] as [number, number, number], axis_v: [0, 1, 0] as [number, number, number],
  width_m: 0.1, height_m: 0.1, nx: 96, ny: 96 };

describe('FEM view mount', () => {
  let root: Root;
  let host: HTMLDivElement;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    mounted.view.mockClear();
    useFieldPlaneStore.getState().enableFem('one', plane);
    host = document.createElement('div'); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); useFieldPlaneStore.getState().disable(); });

  it('mounts the F5 view with the shared plane, modes and phase animation', () => {
    const payload = femFieldFixture();
    const field = { ...decodeFemFieldPayload(payload), payload };
    act(() => root.render(<FemFieldScene field={field} rendering="element" sequential={['#000000', '#ffffff']} unitsPerMetre={1000}/>));
    expect(mounted.view.mock.lastCall?.[0]).toMatchObject({ domain: { regionId: 'chamber' },
      plane: { origin: [0, 0, 5], normal: [0, 0, 1] }, rendering: 'element', animating: false });
    act(() => { useFieldPlaneStore.getState().setAnimating(true); useFieldPlaneStore.getState().setAnimationSpeed(2); });
    expect(mounted.view.mock.lastCall?.[0]).toMatchObject({ mode: 'instantaneous', animating: true, animationSpeed: 2 });
    act(() => useFieldPlaneStore.getState().setPlane({ ...plane, origin_m: [0, 0, 0.01] }));
    expect(mounted.view.mock.lastCall?.[0]).toMatchObject({ plane: { origin: [0, 0, 10] } });
  });

  it('uses the shared gizmo axes for the FEM normal', () => {
    expect(femSlicePlane({ ...plane, axis_v: [0, 0, 1] }).normal).toEqual([0, -1, 0]);
  });
});
