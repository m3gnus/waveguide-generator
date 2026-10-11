import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fusionWorkflowView } from '../shell/cadWorkflowView';
import { createCadConnectionStore, type CadConnectionStatus } from './cadConnection';

function status(overrides: Partial<CadConnectionStatus> = {}): CadConnectionStatus {
  return {
    cadApplication: 'fusion360', cadFolderConfigured: true, cadFolderPath: '/workspace',
    state: 'current', processRunning: true, running: true, updatedAt: new Date().toISOString(),
    documentName: 'Speaker', documentId: 'document-1', currentFormula: 'OSSE', fusionFormula: 'OSSE',
    link: null, wgChangesAvailable: false, fusionChangesAvailable: false, documentChanged: false,
    documentChangeDetectable: true, staleDetectionExplanation: null, observationFreshness: 'current',
    addinInboxTransfer: true, statusTtlSeconds: 20,
    realizedDimensions: { state: 'no_link', instanceId: null, exportId: null, parameters: [] },
    ...overrides,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe('CAD connection observations without the coordinator', () => {
  let connection: ReturnType<typeof createCadConnectionStore>;
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime('2026-10-11T12:00:00Z'); connection = createCadConnectionStore(); });
  afterEach(() => { connection.invalidate(); vi.useRealTimers(); });

  it('ages a held status into last report at the TTL without traffic', async () => {
    const reader = vi.fn(async () => status());
    await connection.read(reader);
    expect(connection.getState().receivedAt).toBe(Date.now());
    expect(fusionWorkflowView(connection.getState().fusionStatus).state).toBe('current');
    await vi.advanceTimersByTimeAsync(19_999);
    expect(connection.getState().fusionStatus?.statusObserved).not.toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(connection.getState().fusionStatus?.statusObserved).toBe(false);
    expect(fusionWorkflowView(connection.getState().fusionStatus).headline).toContain('last reported');
    expect(connection.getState().fusionStatus?.documentChangeDetectable).toBe(false);
    expect(reader).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ages the held report while a newer read stalls', async () => {
    await connection.read(async () => status());
    void connection.read(() => new Promise(() => undefined));
    await vi.advanceTimersByTimeAsync(20_000);
    expect(connection.getState().fusionStatus?.statusObserved).toBe(false);
  });

  it('refuses a superseded read to its action caller as well as the display', async () => {
    const first = deferred<CadConnectionStatus>();
    const oldAction = connection.read(() => first.promise);
    const latest = status({ documentId: 'document-new' });
    await connection.read(async () => latest);
    first.resolve(status({ documentId: 'document-old' }));
    expect(await oldAction).toBeNull();
    expect(connection.getState().fusionStatus).toBe(latest);
  });

  it('refuses the older action even while the newer read is pending or fails', async () => {
    const first = deferred<CadConnectionStatus>();
    const second = deferred<CadConnectionStatus>();
    const oldAction = connection.read(() => first.promise);
    const newAction = connection.read(() => second.promise.then(() => { throw new Error('offline'); }));
    first.resolve(status());
    expect(await oldAction).toBeNull();
    second.resolve(status());
    expect(await newAction).toBeNull();
    expect(connection.getState().fusionStatus).toBeNull();
  });

  it('lets a command finish across background polls and resumes observation afterward', async () => {
    const pending = deferred<CadConnectionStatus>();
    const command = connection.readCommand(() => pending.promise);
    const poll = vi.fn(async () => status({ documentId: 'poll-document' }));
    expect(await connection.read(poll)).toBeNull();
    expect(poll).not.toHaveBeenCalled();
    const answer = status(); pending.resolve(answer);
    expect(await command).toBe(answer);
    expect(connection.getState().fusionStatus).toBe(answer);
    expect(await connection.read(poll)).toEqual(status({ documentId: 'poll-document' }));
    expect(poll).toHaveBeenCalledOnce();
  });

  it('refuses a background answer overtaken by a command and fences older commands', async () => {
    const background = deferred<CadConnectionStatus>();
    const old = deferred<CadConnectionStatus>();
    const backgroundRead = connection.read(() => background.promise);
    const oldCommand = connection.readCommand(() => old.promise);
    const next = status({ documentId: 'new-command' });
    expect(await connection.readCommand(async () => next)).toBe(next);
    background.resolve(status({ documentId: 'old-background' })); old.resolve(status());
    expect(await backgroundRead).toBeNull(); expect(await oldCommand).toBeNull();
    expect(connection.getState().fusionStatus).toBe(next);
  });

  it('cancels the old expiry when a new report is published', async () => {
    await connection.read(async () => status());
    await vi.advanceTimersByTimeAsync(10_000);
    await connection.read(async () => status({ documentId: 'new' }));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(connection.getState().fusionStatus?.statusObserved).not.toBe(false);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(connection.getState().fusionStatus?.statusObserved).toBe(false);
  });

  it('fences an in-flight read and removes the clock on context invalidation', async () => {
    await connection.read(async () => status());
    const pending = deferred<CadConnectionStatus>();
    const read = connection.read(() => pending.promise);
    connection.invalidate();
    pending.resolve(status());
    expect(await read).toBeNull();
    expect(connection.getState().fusionStatus).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('fences an in-flight action without losing the held report or its expiry clock', async () => {
    const link = { instanceId: 'instance-1', linkName: 'Upper horn' } as NonNullable<CadConnectionStatus['link']>;
    await connection.read(async () => status({ link }));
    const held = connection.getState();
    const pending = deferred<CadConnectionStatus>();
    const read = connection.read(() => pending.promise);
    await vi.advanceTimersByTimeAsync(5_000);
    connection.fence();
    pending.resolve(status({ documentId: 'superseded' }));
    expect(await read).toBeNull();
    expect(connection.getState()).toBe(held);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(connection.getState().fusionStatus?.statusObserved).toBe(false);
    expect(connection.getState().fusionStatus?.processRunning).toBe(true);
    expect(connection.getState().linkName).toBe('Upper horn');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps advisory read failures quiet, rejects unknown states, and ages already expired answers immediately', async () => {
    await connection.read(async () => status());
    expect(await connection.read(async () => { throw new Error('offline'); })).toBeNull();
    expect(await connection.read(async () => status({ state: 'unsupported' as CadConnectionStatus['state'] }))).toBeNull();
    expect(connection.getState().fusionStatus?.state).toBe('current');
    const answer = await connection.read(async () => status({ updatedAt: new Date(Date.now() - 21_000).toISOString() }));
    expect(answer?.statusObserved).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('exposes the optional linkName without replacing design or instance identity', async () => {
    const link = { instanceId: 'instance-1', designName: 'WG design', linkName: 'Upper horn' } as NonNullable<CadConnectionStatus['link']>;
    await connection.read(async () => status({ link, matchingLinks: [link] }));
    expect(connection.getState().linkName).toBe('Upper horn');
    expect(connection.getState().fusionStatus?.link?.designName).toBe('WG design');
    await connection.read(async () => status({ link: { ...link, linkName: null } }));
    expect(connection.getState().linkName).toBeNull();
  });
});

it('bounds a hanging command read and releases background observations even if abort is ignored', async () => {
  vi.useFakeTimers();
  const connection = createCadConnectionStore();
  const late = deferred<CadConnectionStatus>();
  let commandSignal: AbortSignal | undefined;
  try {
    const command = connection.readCommand((signal) => { commandSignal = signal; return late.promise; });
    expect(await connection.read(async () => status())).toBeNull();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(await command).toBeNull();
    expect(commandSignal?.aborted).toBe(true);
    const observed = await connection.read(async () => status({ documentId: 'new-document' }));
    late.resolve(status({ documentId: 'old-document' }));
    await Promise.resolve();
    expect(connection.getState().fusionStatus).toBe(observed);
  } finally { connection.invalidate(); vi.useRealTimers(); }
});

it('releases only the cancelled command slot and never the newer command slot', async () => {
  const connection = createCadConnectionStore();
  const cancellation = new AbortController();
  const held = deferred<CadConnectionStatus>();
  const newer = deferred<CadConnectionStatus>();
  try {
    const first = connection.readCommand(() => held.promise, cancellation.signal);
    const second = connection.readCommand(() => newer.promise);
    cancellation.abort();
    expect(await first).toBeNull();
    expect(await connection.read(async () => status())).toBeNull();
    newer.resolve(status()); await second;
    expect(await connection.read(async () => status())).not.toBeNull();
    held.resolve(status());
  } finally { connection.invalidate(); }
});
