import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { CAPABILITIES_QUERY_KEY } from './useCapabilities';
import { useImportedSolvePlan } from './useImportedSolvePlan';
import { capabilityFingerprint } from './capabilityFingerprint';
import type { EngineCapability } from './actions';
import { SOLVE_PLAN_DEBOUNCE_MS } from './planQueryPolicy';

const numba: EngineCapability = {
  name: 'bempp', available: true, reason: 'temporary timeout', version: 'test', fast_paths: [],
  assembly_backend: 'numba', assembly_device: null, qualification: 'done', geometry_sources: ['parametric'],
};
const opencl: EngineCapability = { ...numba, assembly_backend: 'opencl', geometry_sources: ['parametric', 'imported'],
  assembly_device: { type: 'cpu', name: 'CPU', vendor: 'CPU', platform: 'PoCL', fp64: true } };

it('hides the previous CAD plan on a select change followed immediately by Solve', async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  resetCadReturnStore();
  resetSolveOptionsStore();
  useCadReturnStore.setState({ ingestRecord: {
    ingest_id: 'wgi_select', manifest_sha256: 'manifest', artifact_sha256: 'artifact',
    findings: [], polar_grid_derivation: {}, role_findings: [], evidence: {},
  } as unknown as CadReturnIngestRecord });
  const client = new QueryClient();
  client.setQueryData(CAPABILITIES_QUERY_KEY, { engines: [opencl], cpuPreparationInFlight: false,
    engineSelection: { default: 'auto', resolvedDefault: 'bempp', full3dOrder: ['bempp'] } });
  const plan = { ingest_id: 'wgi_select', requested: 'auto', engine: 'bempp', reason: 'qualified', engines: [] };
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(plan)));
  vi.stubGlobal('fetch', fetcher);
  const onSolve = vi.fn();
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  function Consumer() {
    const { plan, isPending } = useImportedSolvePlan(true);
    const options = useSolveOptionsStore();
    return <>
      <select value={options.engine} onChange={(event) => options.setEngine(event.target.value)}>
        <option value="auto">AUTO</option><option value="bempp">BEMPP</option>
      </select>
      <button onClick={() => onSolve(plan)}>Solve</button>
      <div data-tag="status">{isPending ? 'pending' : plan?.engine}</div>
    </>;
  }
  const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(1); });
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><Consumer/></QueryClientProvider>); });
    await flush();
    const select = host.querySelector('select')!;
    act(() => {
      select.value = 'bempp';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    act(() => host.querySelector('button')!.click());
    expect(onSolve).toHaveBeenLastCalledWith(null);
    expect(host.querySelector('[data-tag="status"]')!.textContent).toBe('pending');
    expect(fetcher).toHaveBeenCalledTimes(1);
    let resolve!: (response: Response) => void;
    fetcher.mockReturnValueOnce(new Promise<Response>((done) => { resolve = done; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
    expect(host.querySelector('[data-tag="status"]')!.textContent).toBe('pending');
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string).options.engine).toBe('bempp');
    const freshPlan = { ...plan, requested: 'bempp' };
    await act(async () => { resolve(new Response(JSON.stringify(freshPlan))); });
    await flush();
    act(() => host.querySelector('button')!.click());
    expect(onSolve).toHaveBeenLastCalledWith(freshPlan);
  } finally {
    act(() => root.unmount()); host.remove(); client.clear();
    resetCadReturnStore(); resetSolveOptionsStore();
    vi.unstubAllGlobals(); vi.clearAllTimers(); vi.useRealTimers();
  }
});

it('refetches the unchanged CAD plan when numba recovers to OpenCL', async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  resetCadReturnStore();
  resetSolveOptionsStore();
  useCadReturnStore.setState({ ingestRecord: {
    ingest_id: 'wgi_recovery', manifest_sha256: 'manifest', artifact_sha256: 'artifact',
    findings: [], polar_grid_derivation: {}, role_findings: [], evidence: {},
  } as unknown as CadReturnIngestRecord });
  const client = new QueryClient();
  const capabilities = (engine: EngineCapability) => ({ engines: [engine], cpuPreparationInFlight: false,
    engineSelection: { default: 'auto', resolvedDefault: 'bempp', full3dOrder: ['bempp'] } });
  client.setQueryData(CAPABILITIES_QUERY_KEY, capabilities(numba));
  let engine = numba;
  const fetcher = vi.fn(async (url: string, _init?: RequestInit) => {
    expect(url).toBe('/api/solve/imported-plan');
    return new Response(JSON.stringify({ ingest_id: 'wgi_recovery', requested: 'auto',
      engine: engine.assembly_backend === 'opencl' ? 'bempp' : null,
      reason: engine.assembly_backend === 'opencl' ? 'qualified' : 'requires qualified OpenCL', engines: [],
    }));
  });
  vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  function Consumer() {
    const { plan, error } = useImportedSolvePlan(true);
    return <div>{plan?.engine ?? plan?.reason ?? error ?? 'pending'}</div>;
  }
  const flush = async () => act(async () => { await vi.advanceTimersByTimeAsync(1); });
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><Consumer/></QueryClientProvider>); });
    await flush();
    expect(host.textContent).toBe('requires qualified OpenCL');
    expect(fetcher).toHaveBeenCalledTimes(1);
    const body = fetcher.mock.calls[0][1]?.body;
    engine = opencl;
    await act(async () => { client.setQueryData(CAPABILITIES_QUERY_KEY, capabilities(opencl)); });
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(host.textContent).toBe('bempp');
    expect(fetcher.mock.calls[1][1]?.body).toEqual(body); // The capability key alone changed.
    await act(async () => { client.setQueryData(CAPABILITIES_QUERY_KEY, capabilities({ ...opencl, reason: 'new prose' })); });
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
  } finally {
    act(() => root.unmount()); host.remove(); client.clear();
    resetCadReturnStore(); resetSolveOptionsStore();
    vi.unstubAllGlobals(); vi.clearAllTimers(); vi.useRealTimers();
  }
});

it('fingerprints every eligibility field with stable ordering', () => {
  for (const change of [
    { available: false }, { assembly_backend: 'opencl' as const },
    { assembly_device: opencl.assembly_device }, { qualification: 'pending' as const },
    { geometry_sources: ['parametric', 'imported'] },
  ]) expect(capabilityFingerprint([{ ...numba, ...change }])).not.toBe(capabilityFingerprint([numba]));
  expect(capabilityFingerprint([opencl])).toBe(capabilityFingerprint([
    { ...opencl, geometry_sources: ['imported', 'parametric'] },
  ]));
  expect(capabilityFingerprint([opencl, { ...numba, name: 'metal' }])).toBe(capabilityFingerprint([
    { ...numba, name: 'metal' }, opencl,
  ]));
});
