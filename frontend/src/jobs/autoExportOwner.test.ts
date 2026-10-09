import { afterEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { ownedAutoExport } from './autoExportOwner';

const job = { id: 'one', auto_export_formats: { csv: { status: 'blocked', attempted_at: 'earlier', reason: 'Existing file' } } } as unknown as JobItem;
const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
afterEach(() => vi.unstubAllGlobals());

describe('backend-owned automatic exports', () => {
  it('claims before generating, uses fresh backend metadata, and counts blocked selected formats', async () => {
    const fetcher = vi.fn(async (path: string, _init?: RequestInit) => path.endsWith('/claim')
      ? response({ claimed: true, token: 'secret', formats: ['step'], job }) : response({ status: 'ok' }));
    vi.stubGlobal('fetch', fetcher);
    const generate = vi.fn(async (fresh: JobItem, _formats: unknown, fetch: typeof globalThis.fetch) => {
      expect(fetcher).toHaveBeenCalledTimes(1);
      expect(fresh.auto_export_formats.csv.status).toBe('blocked');
      await fetch('/api/export/step', { method: 'POST' });
      return { files: ['one.step'], failures: [] };
    });
    await ownedAutoExport({ ...job, auto_export_formats: {} }, ['step'], generate, ['csv', 'step']);
    const write = fetcher.mock.calls.find(([path]) => path === '/api/export/step')!;
    expect(new Headers(write[1]?.headers).get('X-WG-Auto-Export')).toBe('secret');
    const finish = fetcher.mock.calls.find(([path]) => path.endsWith('/finish'))!;
    expect(JSON.parse(finish[1]!.body as string).completed_at).toBeNull();
  });

  it('keeps uncertain publication blocked and distinguishes a pre-publication transient failure', async () => {
    for (const uncertain of [true, false]) {
      const fetcher = vi.fn(async (path: string, _init?: RequestInit) => path.endsWith('/claim')
        ? response({ claimed: true, token: 'secret', formats: ['step'], job }) : response({ status: 'ok' }));
      vi.stubGlobal('fetch', fetcher);
      await expect(ownedAutoExport(job, ['step'], async () => {
        throw Object.assign(new Error('Connection reset'), { publicationUncertain: uncertain });
      })).rejects.toThrow('Connection reset');
      const finish = fetcher.mock.calls.find(([path]) => path.endsWith('/finish'))!;
      expect(JSON.parse(finish[1]!.body as string).formats.step.status).toBe(uncertain ? 'blocked' : 'failed');
    }
  });

  it('does not generate when another window already completed or blocked the export', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ claimed: false, busy: false })));
    const generate = vi.fn();
    await ownedAutoExport(job, ['step'], generate);
    expect(generate).not.toHaveBeenCalled();
  });
});

it('records a destination conflict as blocked rather than completion', async () => {
  const fetcher = vi.fn(async (path: string, _init?: RequestInit) => path.endsWith('/claim')
    ? response({ claimed: true, token: 'secret', formats: ['step'], job }) : response({ status: 'ok' }));
  vi.stubGlobal('fetch', fetcher);
  await expect(ownedAutoExport(job, ['step'], async () => {
    throw Object.assign(new Error('Existing destination'), { destinationConflict: true });
  })).rejects.toThrow('Existing destination');
  const finish = fetcher.mock.calls.find(([path]) => path.endsWith('/finish'))!;
  const body = JSON.parse(finish[1]!.body as string);
  expect(body.formats.step.status).toBe('blocked');
  expect(body.completed_at).toBeNull();
  expect(body.files).toEqual([]);
});
