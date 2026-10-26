import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { useMeshFreshnessStore } from './meshFreshness';

const job = (id: string): JobItem => ({ id, provenance: { mesher_version: '1', geometry_hash: 'sha256:mesh' } } as unknown as JobItem);

beforeEach(() => useMeshFreshnessStore.setState({ jobs: {} }));
afterEach(() => vi.unstubAllGlobals());

it('does not ask for a freshness verdict for old jobs without evidence', () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  useMeshFreshnessStore.setState({ jobs: { old: 'stale' } });
  useMeshFreshnessStore.getState().check({ id: 'old' } as JobItem);
  expect(fetcher).not.toHaveBeenCalled();
  expect(useMeshFreshnessStore.getState().jobs.old).toBeUndefined();
});

it('keeps a separate verdict for each job and treats failed checks as unknown', async () => {
  const fetcher = vi.fn(async (url: string) => url.includes('stale')
    ? new Response(JSON.stringify({ freshness: 'stale' }))
    : new Response('', { status: 503 }));
  vi.stubGlobal('fetch', fetcher);
  useMeshFreshnessStore.getState().check(job('stale'));
  useMeshFreshnessStore.getState().check(job('offline'));
  expect(useMeshFreshnessStore.getState().jobs.stale).toBe('pending');
  await vi.waitFor(() => expect(useMeshFreshnessStore.getState().jobs).toEqual({ stale: 'stale', offline: 'unknown' }));
});

it('ignores an older reply when a run is reopened again', async () => {
  let finishOld!: (response: Response) => void;
  const fetcher = vi.fn()
    .mockImplementationOnce(() => new Promise<Response>(resolve => { finishOld = resolve; }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ freshness: 'current' })));
  vi.stubGlobal('fetch', fetcher);
  useMeshFreshnessStore.getState().check(job('one'));
  useMeshFreshnessStore.getState().check(job('one'), true);
  await vi.waitFor(() => expect(useMeshFreshnessStore.getState().jobs.one).toBe('current'));
  finishOld(new Response(JSON.stringify({ freshness: 'stale' })));
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(useMeshFreshnessStore.getState().jobs.one).toBe('current');
});

it('rechecks after a new backend connection epoch', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ freshness: 'current' })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ freshness: 'stale' })));
  vi.stubGlobal('fetch', fetcher);
  useMeshFreshnessStore.getState().check(job('reconnect'), false, 1);
  await vi.waitFor(() => expect(useMeshFreshnessStore.getState().jobs.reconnect).toBe('current'));
  useMeshFreshnessStore.getState().check(job('reconnect'), false, 2);
  await vi.waitFor(() => expect(useMeshFreshnessStore.getState().jobs.reconnect).toBe('stale'));
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('rechecks when a restarted backend reuses the same epoch', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ freshness: 'current' })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ freshness: 'stale' })));
  vi.stubGlobal('fetch', fetcher);
  useMeshFreshnessStore.getState().check(job('restarted'), false, 1);
  await vi.waitFor(() => expect(useMeshFreshnessStore.getState().jobs.restarted).toBe('current'));
  useMeshFreshnessStore.getState().check(job('restarted'), false, null);
  useMeshFreshnessStore.getState().check(job('restarted'), false, 1);
  await vi.waitFor(() => expect(useMeshFreshnessStore.getState().jobs.restarted).toBe('stale'));
  expect(fetcher).toHaveBeenCalledTimes(2);
});
