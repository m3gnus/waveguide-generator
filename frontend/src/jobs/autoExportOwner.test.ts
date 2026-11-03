import { afterEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { ownedAutoExport } from './autoExportOwner';
import { runAutomaticWorkspaceExport } from '../results/exporters';
import { preferencesStore } from '../prefs/preferences';
import { designForFamily } from '../stores/design';
import { activityStore } from '../shell/activity';
import { deferred } from '../shell/cadlink/selection.fixtures';

const job = { id: 'one', auto_export_formats: { csv: { status: 'blocked', attempted_at: 'earlier', reason: 'Existing file' } } } as unknown as JobItem;
const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
const timeoutDescriptor = Object.getOwnPropertyDescriptor(AbortSignal, 'timeout')!;
afterEach(() => {
  Object.defineProperty(AbortSignal, 'timeout', timeoutDescriptor);
  vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); activityStore.reset();
});

const generateGeometry = (format: 'step' | 'stl') => (_job: JobItem, _formats: unknown, fetcher: typeof fetch) =>
  runAutomaticWorkspaceExport({ jobStem: 'horn', design: designForFamily('OSSE'),
    preferences: preferencesStore.getSnapshot(), fetcher }, [format]);

it.each(['step', 'stl'].flatMap(format => ['request', 'body'].map(phase => ({ format: format as 'step' | 'stl', phase }))))(
  'automatic $format honours Stop during the $phase without publishing', async ({ format, phase }) => {
    const reading = deferred<void>();
    let requestSignal: AbortSignal | undefined;
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      if (path.endsWith('/claim')) return response({ claimed: true, token: 'secret', formats: [format], job });
      if (path === `/api/export/${format}`) {
        requestSignal = init!.signal!;
        const wait = () => new Promise<never>((_resolve, reject) => {
          reading.resolve();
          requestSignal!.addEventListener('abort', () => reject(requestSignal!.reason), { once: true });
        });
        return phase === 'request' ? wait() : { ok: true, headers: new Headers(), blob: wait } as unknown as Response;
      }
      return response({ status: 'ok' });
    });
    vi.stubGlobal('fetch', fetcher);
    const pending = ownedAutoExport(job, [format], generateGeometry(format));
    const outcome = pending.catch(error => error);
    await reading.promise;
    activityStore.getSnapshot()[0].cancel!();
    expect(requestSignal?.aborted).toBe(true);
    expect(await outcome).toMatchObject({ message: 'Stopped' });
    expect(fetcher.mock.calls.some(([path]) => String(path) === '/api/workspace/write-export')).toBe(false);
    expect(activityStore.getSnapshot()).toEqual([]);
  },
);

it('automatic geometry progress reads retain their deadline', async () => {
  vi.useFakeTimers();
  // Drive the older WebView deadline path with fake time too.
  Object.defineProperty(AbortSignal, 'timeout', { value: undefined, configurable: true });
  const geometry = deferred<Response>();
  const started = deferred<void>();
  const signals: AbortSignal[] = [];
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const path = String(input);
    if (path.endsWith('/claim')) return response({ claimed: true, token: 'secret', formats: ['step'], job });
    if (path === '/api/export/step') { started.resolve(); return geometry.promise; }
    if (path.startsWith('/api/operation-progress/')) return new Promise((_resolve, reject) => {
      const signal = init!.signal!;
      signals.push(signal);
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    });
    if (path === '/api/workspace/write-export') return response({ directory: 'out', files: ['horn.step'] });
    return response({ status: 'ok' });
  });
  vi.stubGlobal('fetch', fetcher);
  const pending = ownedAutoExport(job, ['step'], generateGeometry('step'));
  await started.promise;
  await vi.advanceTimersByTimeAsync(200);
  expect(signals).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1000);
  expect(signals[0].aborted).toBe(true);
  expect(signals[0].reason).toMatchObject({ name: 'TimeoutError' });
  geometry.resolve(new Response('geometry'));
  await pending;
  expect(activityStore.getSnapshot()).toEqual([]);
});

it('lease loss still aborts a request with a caller signal and releases the claim', async () => {
  vi.useFakeTimers();
  const started = deferred<void>();
  let requestSignal: AbortSignal | undefined;
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const path = String(input);
    if (path.endsWith('/claim')) return response({ claimed: true, token: 'secret', formats: ['step'], job });
    if (path.endsWith('/heartbeat')) throw new Error('Lease lost');
    if (path === '/api/export/step') return new Promise((_resolve, reject) => {
      requestSignal = init!.signal!;
      started.resolve();
      requestSignal.addEventListener('abort', () => reject(requestSignal!.reason), { once: true });
    });
    return response({ status: 'ok' });
  });
  vi.stubGlobal('fetch', fetcher);
  const pending = ownedAutoExport(job, ['step'], generateGeometry('step'));
  const rejected = expect(pending).rejects.toThrow('Lease lost');
  await started.promise;
  await vi.advanceTimersByTimeAsync(10_000);
  await rejected;
  expect(requestSignal?.aborted).toBe(true);
  expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/release'))).toBe(true);
  expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/finish'))).toBe(false);
  expect(activityStore.getSnapshot()).toEqual([]);
});

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

it('records each format by its own outcome, and a refusal over a channel failure', async () => {
  const fetcher = vi.fn(async (path: string, _init?: RequestInit) => path.endsWith('/claim')
    ? response({ claimed: true, token: 'secret', formats: ['step', 'json', 'csv'], job }) : response({ status: 'ok' }));
  vi.stubGlobal('fetch', fetcher);
  await expect(ownedAutoExport(job, ['step', 'json', 'csv'], async () => ({
    files: ['one.step', 'one.csv'],
    failures: [
      { format: 'csv' as const, reason: 'One channel had no data' },
      { format: 'json' as const, reason: 'Export file already exists with different content', blocked: true },
      { format: 'csv' as const, reason: 'Export file already exists with different content', blocked: true },
    ],
  }))).rejects.toThrow('already exists');
  const body = JSON.parse(fetcher.mock.calls.find(([path]) => path.endsWith('/finish'))![1]!.body as string);
  expect(body.formats.step.status).toBe('complete');
  expect(body.formats.json).toMatchObject({ status: 'blocked', reason: 'Export file already exists with different content' });
  expect(body.formats.csv.status).toBe('blocked');
  expect(body.files).toEqual(['one.step', 'one.csv']);
});
