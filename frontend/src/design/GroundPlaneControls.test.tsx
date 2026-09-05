import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { CAPABILITIES_QUERY_KEY } from '../jobs/useCapabilities';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { GroundPlaneControls } from './SolveOptionsSections';

/**
 * The ground plane is a different boundary condition from the infinite baffle,
 * and choosing the wrong one returns a plausible wrong answer rather than an
 * error. These pin that the UI says so, rather than offering two similar names
 * and leaving the difference to be inferred.
 */
const engine = (name: string, mountings: string[], groundPlaneAxes?: string[]) => ({
  name, available: true, reason: 'ok', version: null, fast_paths: [],
  formulations: ['full-3d'], mountings, geometry_sources: ['parametric'],
  ...(groundPlaneAxes ? { ground_plane_axes: groundPlaneAxes } : {}),
});

const capabilities = (engines: ReturnType<typeof engine>[]) => ({
  engines,
  engineSelection: {
    default: 'auto', resolvedDefault: 'bempp',
    full3dOrder: ['metal', 'beat', 'bempp', 'dryrun'], axisymmetricRunner: 'axisym',
  },
});

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

function render(caps: ReturnType<typeof capabilities>) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(CAPABILITIES_QUERY_KEY, caps);
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <GroundPlaneControls />
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  // Every sibling component test sets this. Without it React does not treat
  // `act` as a real act scope, so state updates escape the scope and can land
  // during a later file's run -- which is how this file made an unrelated test
  // fail intermittently in a full run and never on its own.
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  resetSolveOptionsStore();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client?.clear();
  // The store is persisted, so an enabled ground plane or a forced engine left
  // behind here is state the next file would inherit.
  resetSolveOptionsStore();
  localStorage.clear();
});

describe('ground plane controls', () => {
  it('is off by default and hides its settings until asked for', () => {
    render(capabilities([engine('bempp', ['free-standing', 'ground-plane'], ['x', 'y', 'z'])]));
    const toggle = container.querySelector<HTMLInputElement>('#solve-ground-plane');
    expect(toggle?.checked).toBe(false);
    expect(container.querySelector('#ground-plane-axis')).toBeNull();
    expect(container.querySelector('#ground-plane-height')).toBeNull();
  });

  it('names the difference from an infinite baffle in the visible copy', () => {
    render(capabilities([engine('bempp', ['free-standing', 'ground-plane'], ['x', 'y', 'z'])]));
    act(() => { useSolveOptionsStore.getState().updateGroundPlane({ enabled: true }); });
    const text = container.textContent ?? '';
    // A user who picks the wrong one gets a plausible wrong answer, so the
    // panel has to state the distinction rather than imply it.
    expect(text).toContain('infinite baffle');
    expect(text).toMatch(/different boundary/i);
    expect(text).toMatch(/keeps its edges|cabinet edges/i);
  });

  it('offers the plane by axis, never as an axis-pair token', () => {
    render(capabilities([engine('bempp', ['free-standing', 'ground-plane'], ['x', 'y', 'z'])]));
    act(() => { useSolveOptionsStore.getState().updateGroundPlane({ enabled: true }); });
    const select = container.querySelector<HTMLSelectElement>('#ground-plane-axis');
    const values = [...(select?.options ?? [])].map((option) => option.value);
    expect(values).toEqual(['y', 'x', 'z']);
    // `xy` means legacy bi-symmetry in WG and x-and-y mirrors in BEAT; it must
    // never be selectable here as a third meaning.
    expect(values).not.toContain('xy');
    expect(select?.value).toBe('y');
    const labels = [...(select?.options ?? [])].map((option) => option.textContent ?? '');
    expect(labels[0]).toMatch(/floor/i);
  });

  it('disables an axis the resolved backend cannot solve', () => {
    render(capabilities([engine('bempp', ['free-standing', 'ground-plane'], ['y'])]));
    act(() => { useSolveOptionsStore.getState().updateGroundPlane({ enabled: true }); });
    const options = [...(container.querySelector<HTMLSelectElement>('#ground-plane-axis')?.options ?? [])];
    const disabled = options.filter((option) => option.disabled).map((option) => option.value);
    expect(disabled.sort()).toEqual(['x', 'z']);
  });

  it('warns, with a remedy, when the backend has no ground plane at all', () => {
    useSolveOptionsStore.getState().setEngine('beat');
    render(capabilities([
      engine('beat', ['free-standing']),
      engine('bempp', ['free-standing', 'ground-plane'], ['x', 'y', 'z']),
    ]));
    act(() => { useSolveOptionsStore.getState().updateGroundPlane({ enabled: true }); });
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('BEAT does not support a rigid ground plane');
    // The remedy must not suggest the baffle as a substitute for the plane.
    expect(alert?.textContent).toMatch(/not a substitute/i);
  });

  it('only sends ground_plane once it is enabled', () => {
    render(capabilities([engine('bempp', ['free-standing', 'ground-plane'], ['x', 'y', 'z'])]));
    expect(useSolveOptionsStore.getState().options().ground_plane).toBeUndefined();
    act(() => {
      useSolveOptionsStore.getState().updateGroundPlane({ enabled: true, height_m: 1.25 });
    });
    expect(useSolveOptionsStore.getState().options().ground_plane)
      .toEqual({ enabled: true, axis: 'y', height_m: 1.25 });
  });
});
