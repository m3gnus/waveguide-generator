import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { CAPABILITIES_QUERY_KEY } from '../jobs/useCapabilities';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { ParamPanel } from './ParamPanel';

/**
 * End-to-end cover for portable Windows/BEMPP capabilities.
 *
 * `backendSupport.test.ts` pins the decision helpers; this pins the wiring —
   * that the panel actually consults the live capability probe and offers the
   * coupled infinite-baffle workflow on a host without Metal.
 */
const engine = (
  name: string,
  available: boolean,
  mountings: string[],
  formulations = ['full-3d'],
) => ({
  name, available, reason: available ? 'ok' : `${name} unavailable`, version: null, fast_paths: [],
  formulations, mountings, geometry_sources: ['parametric'],
});

const capabilities = (metalAvailable: boolean) => ({
  engines: [
    engine('metal', metalAvailable, ['free-standing', 'infinite-baffle']),
    engine('bempp', true, ['free-standing', 'infinite-baffle']),
    engine('axisym', true, ['free-standing', 'infinite-baffle'], ['axisymmetric']),
  ],
  engineSelection: {
    default: 'auto', resolvedDefault: metalAvailable ? 'metal' : 'bempp',
    full3dOrder: ['metal', 'beat', 'bempp', 'dryrun'], axisymmetricRunner: 'axisym',
  },
});

describe('solver-backend parameter gating', () => {
  let host: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  /* Seeding the cache rather than stubbing fetch keeps the probe out of the
   * assertion: an unresolved query reports no backend, which by design hides
   * nothing, so a race here would read as a passing "everything is offered". */
  const mount = async (payload: ReturnType<typeof capabilities>) => {
    queryClient.setQueryData(CAPABILITIES_QUERY_KEY, payload);
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}><ParamPanel tab="simulation" /></QueryClientProvider>);
      await Promise.resolve();
    });
  };

  const optionsOf = (parameterId: string) => [
    ...host.querySelectorAll<HTMLOptionElement>(`[data-parameter-id="${parameterId}"] option`),
  ].map((option) => option.textContent);

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    resetDesignStore();
    resetCadReturnStore();
    resetSolveOptionsStore();
    workspaceModeStore.setMode('parametric');
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    queryClient.clear();
  });

  it('offers infinite baffle where Metal is available', async () => {
    await mount(capabilities(true));
    expect(optionsOf('simulation.sim_type')).toEqual(['Free-standing', 'Infinite baffle']);
    expect(host.querySelector('[data-parameter-id="simulation.solver_mode"]')).toBeNull();
    expect(host.querySelector('.field-warning')).toBeNull();
  });

  it('offers coupled infinite baffle on a host with BEMPP and no Metal', async () => {
    await mount(capabilities(false));
    expect(optionsOf('simulation.sim_type')).toEqual(['Free-standing', 'Infinite baffle']);
    expect(host.querySelector('[data-parameter-id="simulation.solver_mode"]')).toBeNull();
    expect(host.querySelector('.field-warning')).toBeNull();
  });

  it('keeps an imported infinite-baffle design supported by BEMPP', async () => {
    useDesignStore.getState().updateValue('simulation.sim_type', 'infinite-baffle');
    await mount(capabilities(false));
    expect(optionsOf('simulation.sim_type')).toEqual(['Free-standing', 'Infinite baffle']);
    expect(host.querySelector('[data-parameter-id="simulation.sim_type"] .field-warning')).toBeNull();
  });

  /* Aperture mesh scale sizes the infinite-baffle cap and nothing else, so it
   * follows the simulation type rather than the backend. */
  it('shows aperture mesh scale only alongside an infinite-baffle design', async () => {
    await mount(capabilities(true));
    expect(host.querySelector('[data-parameter-id="mesh.aperture_resolution_scale"]')).toBeNull();
    await act(async () => {
      useDesignStore.getState().updateValue('simulation.sim_type', 'infinite-baffle');
      await Promise.resolve();
    });
    expect(host.querySelector('[data-parameter-id="mesh.aperture_resolution_scale"]')).not.toBeNull();
  });

  it('lets AUTO skip BEAT for BEMPP coupled IB, but keeps explicit BEAT gated', async () => {
    const payload = {
      engines: [
        engine('metal', false, ['free-standing', 'infinite-baffle']),
        engine('beat', true, ['free-standing']),
        engine('bempp', true, ['free-standing', 'infinite-baffle']),
        engine('axisym', false, ['free-standing', 'infinite-baffle'], ['axisymmetric']),
      ],
      engineSelection: {
        default: 'auto', resolvedDefault: 'beat',
        full3dOrder: ['metal', 'beat', 'bempp', 'dryrun'], axisymmetricRunner: 'axisym',
      },
    };

    await mount(payload);
    expect(optionsOf('simulation.sim_type')).toEqual(['Free-standing', 'Infinite baffle']);

    await act(async () => {
      useSolveOptionsStore.setState({ engine: 'beat' });
      await Promise.resolve();
    });
    expect(optionsOf('simulation.sim_type')).toEqual(['Free-standing']);
  });
});
