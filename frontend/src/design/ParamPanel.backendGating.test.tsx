import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import guidance from '../../../shared/opencl-driver-guidance.v1.json';
import type { OpenClGuidanceReason } from '../shell/OpenClGuidance';
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
) => ({
  name, available, reason: available ? 'ok' : `${name} unavailable`, version: null, fast_paths: [],
  assembly_backend: null as 'opencl' | 'numba' | null,
  opencl_unavailable_reason: null as import('../jobs/actions').OpenclUnavailableReason | null,
  formulations: ['full-3d'], mountings, geometry_sources: ['parametric'],
});

const capabilities = (metalAvailable: boolean) => ({
  hostPlatform: 'windows',
  engines: [
    engine('metal', metalAvailable, ['free-standing', 'infinite-baffle']),
    engine('bempp', true, ['free-standing', 'infinite-baffle']),
  ],
  engineSelection: {
    default: 'auto', resolvedDefault: metalAvailable ? 'metal' : 'bempp',
    full3dOrder: ['metal', 'beat', 'bempp', 'dryrun'],
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

  /* Every warning inside one field's entry, and only that field's. */
  const warningsFor = (parameterId: string) => [
    ...host.querySelectorAll(`[data-parameter-id="${parameterId}"] .field-warning`),
  ].map((node) => node.textContent ?? '');

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
      hostPlatform: 'windows',
      engines: [
        engine('metal', false, ['free-standing', 'infinite-baffle']),
        engine('beat', true, ['free-standing']),
        engine('bempp', true, ['free-standing', 'infinite-baffle']),
      ],
      engineSelection: {
        default: 'auto', resolvedDefault: 'beat',
        full3dOrder: ['metal', 'beat', 'bempp', 'dryrun'],
      },
    };

    await mount(payload);
    expect(optionsOf('simulation.sim_type')).toEqual(['Free-standing', 'Infinite baffle']);

    await act(async () => {
      useSolveOptionsStore.setState({ engine: 'beat' });
      await Promise.resolve();
    });
    // Listed but disabled, naming the selection and the engines to switch to.
    expect(optionsOf('simulation.sim_type')).toEqual(['Free-standing', 'Infinite baffle']);
    const option = [...host.querySelectorAll<HTMLOptionElement>('[data-parameter-id="simulation.sim_type"] option')]
      .find((item) => item.textContent === 'Infinite baffle');
    expect(option?.disabled).toBe(true);
    const reason = warningsFor('simulation.sim_type').join(' ');
    expect(reason).toContain('BEAT cannot solve coupled infinite-baffle simulation');
    expect(reason).toMatch(/Switch the engine to AUTO, BEMPP/);
    expect(reason).not.toMatch(/No engine on this host/);
  });

  it('shows infinite baffle disabled, with a reason, when no engine on the host can run it', async () => {
    await mount({
      hostPlatform: 'windows',
      engines: [
        engine('metal', false, ['free-standing', 'infinite-baffle']),
        engine('beat-cpu', true, ['free-standing']),
        engine('bempp', true, ['free-standing']),
      ],
      engineSelection: { default: 'auto', resolvedDefault: 'bempp', full3dOrder: ['metal', 'beat-cpu', 'bempp', 'dryrun'] },
    });
    const option = [...host.querySelectorAll<HTMLOptionElement>('[data-parameter-id="simulation.sim_type"] option')]
      .find((item) => item.textContent === 'Infinite baffle');
    expect(option).toBeDefined();
    expect(option?.disabled).toBe(true);
    const reason = warningsFor('simulation.sim_type').join(' ');
    expect(reason).toContain('No engine on this host supports coupled infinite-baffle simulation');
    expect(reason).toContain('Metal, or BEMPP with coupled infinite-baffle support');
    expect(reason).not.toMatch(/beat|axisym/i);
  });

  it('says an unavailable explicit engine is unavailable, not incapable, and offers the capable ones', async () => {
    await mount(capabilities(false));
    await act(async () => {
      useSolveOptionsStore.setState({ engine: 'metal' });
      await Promise.resolve();
    });
    const option = [...host.querySelectorAll<HTMLOptionElement>('[data-parameter-id="simulation.sim_type"] option')]
      .find((item) => item.textContent === 'Infinite baffle');
    expect(option?.disabled).toBe(true);
    const reason = warningsFor('simulation.sim_type').join(' ');
    expect(reason).toContain('Metal is not available on this host');
    expect(reason).toMatch(/Switch the engine to AUTO, BEMPP/);
    expect(reason).not.toContain('cannot solve');
  });

  it('adds no reason when the selected engine can run it', async () => {
    await mount(capabilities(true));
    await act(async () => {
      useSolveOptionsStore.setState({ engine: 'metal' });
      await Promise.resolve();
    });
    expect(warningsFor('simulation.sim_type')).toEqual([]);
  });

  it.each(['freestanding', 'infinite-baffle'].flatMap((simType) =>
    (Object.keys(guidance.reasons) as OpenClGuidanceReason[]).map((reason) => ({ simType, reason }))))(
    'shows one shared guidance block in the IB numba notice ($simType, $reason)', async ({ simType, reason }) => {
    useDesignStore.getState().updateValue('simulation.sim_type', simType);
    const payload = capabilities(false);
    payload.engines[1].assembly_backend = 'numba';
    payload.engines[1].opencl_unavailable_reason = reason;
    await mount(payload);
    expect(host.querySelectorAll('.opencl-guidance')).toHaveLength(1);
    const block = host.querySelector('[data-parameter-id="simulation.sim_type"] .opencl-guidance')!;
    expect(block.textContent).toContain(guidance.platforms.windows.summary);
    expect(block.querySelector('p')?.textContent).toBe(guidance.notices.infinite_baffle_numba);
    expect(block.textContent).toContain(guidance.reasons[reason]);
    expect(block.textContent).toContain(guidance.notices.infinite_baffle_numba);
  });

  it.each(['opencl', null] as const)('omits the notice for a BEMPP backend of %s', async (backend) => {
    const payload = capabilities(false);
    payload.engines[1].assembly_backend = backend;
    await mount(payload);
    expect(warningsFor('simulation.sim_type')).toEqual([]);
    expect(host.querySelector('.opencl-guidance')).toBeNull();
  });

  it('keeps the short IB numba notice without driver guidance on macOS', async () => {
    const payload = capabilities(false);
    payload.hostPlatform = 'darwin';
    payload.engines[1].assembly_backend = 'numba';
    payload.engines[1].opencl_unavailable_reason = 'no_device';
    await mount(payload);
    expect(host.textContent).toContain(guidance.notices.infinite_baffle_numba);
    expect(host.querySelector('.opencl-guidance')?.textContent).toBe(guidance.notices.infinite_baffle_numba);
    expect(host.querySelector('.opencl-guidance a')).toBeNull();
  });

  it('omits the notice when AUTO routes IB to Metal ahead of numba', async () => {
    const payload = capabilities(true);
    payload.engines[1].assembly_backend = 'numba';
    await mount(payload);
    expect(warningsFor('simulation.sim_type')).toEqual([]);
  });

  it('follows the IB candidate when AUTO skips its free-standing default', async () => {
    const payload = capabilities(false);
    payload.engines.unshift(engine('beat-cpu', true, ['free-standing']));
    payload.engines[2].assembly_backend = 'numba';
    payload.engineSelection.resolvedDefault = 'beat-cpu';
    payload.engineSelection.full3dOrder = ['beat-cpu', 'bempp'];
    await mount(payload);
    expect(host.textContent).toContain(guidance.notices.infinite_baffle_numba);
    await act(async () => { useSolveOptionsStore.setState({ engine: 'beat-cpu' }); });
    expect(host.textContent).not.toContain(guidance.notices.infinite_baffle_numba);
  });

  it('omits the notice when BEMPP does not offer IB', async () => {
    const payload = capabilities(false);
    payload.engines[1].assembly_backend = 'numba';
    payload.engines[1].mountings = ['free-standing'];
    await mount(payload);
    expect(host.textContent).not.toContain(guidance.notices.infinite_baffle_numba);
  });

});
