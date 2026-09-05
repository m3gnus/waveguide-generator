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
/**
 * Wire-faithful: the server always emits `ground_plane_axes`, as `[]` for an
 * engine with no ground plane -- `asdict(EngineInfo)` has no omit-empty. An
 * earlier version of this helper dropped the key when no axes were given, so a
 * fixture engine had `undefined` where the real payload has `[]`. That hid a
 * live bug, because `[]` is truthy in JS and `undefined` is not.
 */
const engine = (name: string, mountings: string[], groundPlaneAxes: string[] = []) => ({
  name, available: true, reason: 'ok', version: null, fast_paths: [],
  formulations: ['full-3d'], mountings, geometry_sources: ['parametric'],
  ground_plane_axes: groundPlaneAxes,
});

const capabilities = (engines: ReturnType<typeof engine>[], resolvedDefault = 'bempp') => ({
  engines,
  engineSelection: {
    default: 'auto', resolvedDefault,
    // The server's real order since BEAT was split into per-backend engines.
    // The pre-split ['metal','beat','bempp','dryrun'] fixture named an engine
    // the registry no longer advertises, which mattered the moment this
    // component started judging AUTO against the planned order rather than
    // against one active backend.
    full3dOrder: ['metal', 'beat-metal', 'beat-cpu', 'bempp', 'dryrun'],
    axisymmetricRunner: 'axisym',
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

  it('does not warn on AUTO when a later planned engine can ground', () => {
    // The regression: AUTO's "active" backend is only the first candidate the
    // server would walk. On a Mac that is Metal, which has no ground plane, so
    // judging the warning on it alone put a red alert on a solve the server
    // routes to BEMPP and runs. A warning that fires on a solve which succeeds
    // teaches a user to ignore warnings.
    useSolveOptionsStore.getState().setEngine('auto');
    // resolvedDefault 'metal' is the Mac case: AUTO's active backend is Metal,
    // which has no ground plane, while BEMPP later in the same plan does.
    render(capabilities([
      engine('metal', ['free-standing', 'infinite-baffle']),
      engine('bempp', ['free-standing', 'ground-plane'], ['x', 'y', 'z']),
    ], 'metal'));
    act(() => { useSolveOptionsStore.getState().updateGroundPlane({ enabled: true }); });
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('still warns on AUTO when no planned engine can ground', () => {
    useSolveOptionsStore.getState().setEngine('auto');
    render(capabilities([
      engine('metal', ['free-standing', 'infinite-baffle']),
      engine('beat-cpu', ['free-standing']),
    ], 'metal'));
    act(() => { useSolveOptionsStore.getState().updateGroundPlane({ enabled: true }); });
    expect(container.querySelector('[role="alert"]')?.textContent)
      .toContain('does not support a rigid ground plane');
  });

  it('offers every axis the plan can reach, not just the first candidate\'s', () => {
    // The regression this pairs with: the warning went plan-based while the
    // axis list stayed active-backend-based. On an AUTO Mac the active backend
    // is Metal, whose wire payload is ground_plane_axes: [] -- truthy -- so
    // every axis was disabled while no warning explained it. A user could not
    // pick a side or rear wall and was told nothing.
    useSolveOptionsStore.getState().setEngine('auto');
    render(capabilities([
      engine('metal', ['free-standing', 'infinite-baffle']),
      engine('bempp', ['free-standing', 'ground-plane'], ['x', 'y', 'z']),
    ], 'metal'));
    act(() => { useSolveOptionsStore.getState().updateGroundPlane({ enabled: true }); });
    const options = Array.from(
      container.querySelectorAll<HTMLOptionElement>('#ground-plane-axis option'),
    );
    expect(options.length).toBeGreaterThan(0);
    expect(options.filter((option) => option.disabled)).toHaveLength(0);
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
