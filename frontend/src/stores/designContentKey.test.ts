import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { applyOpenedDesign } from '../design/openCadProject';
import {
  keptContentKeyNow,
  rememberSentCopy,
  replacingWouldLose,
  replacingWouldLoseNow,
} from '../design/replacementCheck';
import { toSolveDesign } from '../jobs/actions';
import { AUTOSAVE_KEY, restoreAutosave, writeAutosave } from './autosave';
import { resetDesignStore, seedDesign, serializeDesign, useDesignStore, type DesignDocument } from './design';
import { designContentKey, DESIGN_CONTENT_KEY_VERSION, type DesignFileSettings } from './designContentKey';
import { wgSolveSettingsFromStore } from './designWire';
import { resetDocumentStore, useDocumentStore } from './document';
import { resetSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { unsavedChangesNow } from './unsavedChanges';

function onScreenSettings(): DesignFileSettings {
  const state = useSolveOptionsStore.getState();
  return { polar: { ui: state.polar }, solve: wgSolveSettingsFromStore(state) };
}

function seed(): DesignDocument {
  return structuredClone(seedDesign);
}

function key(design: DesignDocument, name = 'horn'): string | null {
  return designContentKey(design, name, onScreenSettings());
}

/** Two designs are the same file when their serialized wires are equal, key order aside. */
function sameFile(left: DesignDocument, right: DesignDocument): boolean {
  const sorted = (value: unknown): unknown => (
    Array.isArray(value)
      ? value.map(sorted)
      : value !== null && typeof value === 'object'
        ? Object.fromEntries(Object.keys(value).sort().map((name) => [name, sorted((value as Record<string, unknown>)[name])]))
        : value
  );
  return JSON.stringify(sorted(serializeDesign(left))) === JSON.stringify(sorted(serializeDesign(right)));
}

function openedReport(r: number) {
  return {
    dialect: 'ath', migrationsApplied: [],
    passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
    design: { ...seed(), R: r },
    cadlink: { identity: null, classification: 'missing', adoptionCandidate: null },
  } as unknown as Parameters<typeof applyOpenedDesign>[0];
}

/** A run as the jobs list sends it: its snapshot, and its options with the server's defaults. */
function storedRun(design: DesignDocument, overrides: Partial<JobItem> = {}): JobItem {
  const options = JSON.parse(JSON.stringify(useSolveOptionsStore.getState().options())) as Record<string, unknown>;
  return {
    id: 'job-kept', run_number: 3, label: 'kept', status: 'complete', config_summary: {},
    script_snapshot: { version: 1, design: toSolveDesign(design) },
    solve_options: {
      frequency_range: null, num_frequencies: null, frequencies_hz: null, stage_delay_ms: 30,
      ...options,
      polar_config: {
        spherical_theta_count: 37, spherical_phi_count: 72,
        ...(options.polar_config as Record<string, unknown>),
      },
    },
    design_availability: { reopenable: true, source: 'v2-snapshot', reason_code: 'ok', reason: null, note: null },
    ...overrides,
  } as unknown as JobItem;
}

function jobsList(jobs: JobItem[], loaded = true) {
  vi.spyOn(jobsSocket, 'getSnapshot').mockReturnValue({
    connection: loaded ? 'connected' : 'idle', epoch: loaded ? 1 : null, cursor: loaded ? 1 : null, error: null, jobs,
  } as JobsSnapshot);
}

beforeEach(() => {
  resetDesignStore();
  resetDocumentStore();
  resetSolveOptionsStore();
  jobsList([]);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('designContentKey', () => {
  it('is versioned, and ignores the order a document was built in', () => {
    const design = seed();
    const reordered = Object.fromEntries(Object.entries(design).reverse()) as unknown as DesignDocument;

    expect(key(design)).toMatch(new RegExp(`^${DESIGN_CONTENT_KEY_VERSION}:`));
    expect(DESIGN_CONTENT_KEY_VERSION).toBe('v1');
    expect(key(reordered)).toBe(key(design));
  });

  it('keys the file: the name and the solve settings are in it, the settings once', () => {
    const design = seed();
    const withName = key(design, 'horn')!;

    expect(key(design, 'other horn')).not.toBe(withName);
    // One projection: the WG.Solve block is what carries the settings, and it
    // appears once, not again as a separate settings signature.
    expect(withName.split('"WG.Solve"')).toHaveLength(2);
    const symmetry = useSolveOptionsStore.getState().symmetry === 'quarter' ? 'full' : 'quarter';
    useSolveOptionsStore.setState({ symmetry });
    expect(key(design, 'horn')).not.toBe(withName);
  });

  it('keeps expression text exactly as the file does', () => {
    const typed = { ...seed(), R: 150, _expressions: { R: { value: 150, raw: '100 + 50' } } };
    const plain = { ...seed(), R: 150, _expressions: { R: { value: 150, raw: '150' } } };

    expect(key(plain)).not.toBe(key(typed));
    expect(key(structuredClone(typed))).toBe(key(typed));
  });

  it('follows the file contract for omitted and explicit values', () => {
    const explicit = seed();
    const omitted = { ...seed(), _absent: ['simulation.f1'] };
    // Only in the editor: serializeDesign never writes these, so no file differs.
    const uiOnly = {
      ...seed(),
      enclosure: { ...explicit.enclosure, baffle_margin: Number(explicit.enclosure.baffle_margin) + 5 },
    };

    expect(sameFile(omitted, explicit)).toBe(false);
    expect(key(omitted)).not.toBe(key(explicit));
    expect(sameFile(uiOnly, explicit)).toBe(true);
    expect(key(uiOnly)).toBe(key(explicit));
  });

  it('is null, and does not throw, while a directivity grid is half-typed', () => {
    const polar = { ...useSolveOptionsStore.getState().polar, angleStep: 0 };

    expect(() => designContentKey(seed(), 'horn', { polar: { ui: polar }, solve: null })).not.toThrow();
    expect(designContentKey(seed(), 'horn', { polar: { ui: polar }, solve: null })).toBeNull();
  });
});

describe('would replacing the design on screen lose it', () => {
  it('keeps the design as it was opened, and not an edit to it', async () => {
    applyOpenedDesign(openedReport(150), 'horn.cfg');
    expect(useDocumentStore.getState().openedContentKey).toBe(keptContentKeyNow());
    expect(replacingWouldLoseNow()).toBe(false);

    useDesignStore.getState().updateField('R', 321);
    expect(replacingWouldLoseNow()).toBe(true);
    await expect(replacingWouldLose()).resolves.toBe(true);

    useDesignStore.getState().undo();
    expect(replacingWouldLoseNow()).toBe(false);
  });

  it('counts exact expression text restored as kept', () => {
    useDesignStore.getState().replaceDesign({ ...seed(), R: 150, _expressions: { R: { value: 150, raw: '100 + 50' } } });
    useDocumentStore.getState().setOpenedContentKey(keptContentKeyNow());

    useDesignStore.getState().updateExpression('R', { value: 150, raw: '150' });
    expect(replacingWouldLoseNow()).toBe(true);
    useDesignStore.getState().updateExpression('R', { value: 150, raw: '100 + 50' });
    expect(replacingWouldLoseNow()).toBe(false);
  });

  it('never counts a rename alone as a loss', () => {
    applyOpenedDesign(openedReport(150), 'horn.cfg');
    useDocumentStore.getState().setDesignName('Renamed horn');

    expect(replacingWouldLoseNow()).toBe(false);
  });

  it('keeps the built-in design, whatever settings are on screen', () => {
    expect(replacingWouldLoseNow()).toBe(false);
    const symmetry = useSolveOptionsStore.getState().symmetry === 'quarter' ? 'full' : 'quarter';
    useSolveOptionsStore.setState({ symmetry });
    expect(replacingWouldLoseNow()).toBe(false);

    useDesignStore.getState().updateField('R', 321);
    expect(replacingWouldLoseNow()).toBe(true);
  });

  it('keeps a design a stored run holds, with the settings the run recorded', () => {
    useDesignStore.getState().updateField('R', 321);
    jobsList([storedRun(useDesignStore.getState().design)]);
    expect(replacingWouldLoseNow()).toBe(false);

    // The same geometry under settings the run did not record exists nowhere.
    const symmetry = useSolveOptionsStore.getState().symmetry === 'quarter' ? 'full' : 'quarter';
    useSolveOptionsStore.setState({ symmetry });
    expect(replacingWouldLoseNow()).toBe(true);
  });

  it('never counts an imported run, or one that cannot be reopened, as a copy', () => {
    useDesignStore.getState().updateField('R', 321);
    const design = useDesignStore.getState().design;
    jobsList([
      storedRun(design, { id: 'imported', config_summary: { geometry_type: 'imported' } }),
      storedRun(design, {
        id: 'unreadable',
        design_availability: {
          reopenable: false, source: 'none', reason_code: 'unreadable_design', reason: 'unreadable', note: null,
        },
      }),
    ]);

    expect(replacingWouldLoseNow()).toBe(true);
  });

  it('reads the run list at a transition when the jobs socket has not delivered it', async () => {
    useDesignStore.getState().updateField('R', 321);
    const run = storedRun(useDesignStore.getState().design);
    jobsList([], false);
    const fetcher = vi.fn(async (input: RequestInfo | URL) => (
      String(input).startsWith('/api/jobs?')
        ? new Response(JSON.stringify({ items: [run], total: 1 }), { status: 200, headers: { 'Content-Type': 'application/json' } })
        : new Response('not found', { status: 404 })
    ));

    expect(replacingWouldLoseNow()).toBe(true);
    await expect(replacingWouldLose(fetcher as unknown as typeof fetch)).resolves.toBe(false);
    expect(fetcher).toHaveBeenCalledWith('/api/jobs?limit=200&offset=0');
    // What that read found is what a synchronous guard then asks, unfetched.
    expect(replacingWouldLoseNow()).toBe(false);
  });

  it('reads the run list afresh for a replacement, and at most every few seconds for a wait', async () => {
    vi.useFakeTimers();
    try {
      useDesignStore.getState().updateField('R', 321);
      jobsList([], false);
      const fetcher = vi.fn(async () => new Response(
        JSON.stringify({ items: [], total: 0 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )) as unknown as typeof fetch;

      await replacingWouldLose(fetcher);
      await replacingWouldLose(fetcher);
      expect(fetcher).toHaveBeenCalledTimes(2);

      await replacingWouldLose(fetcher, { reuseRunsReadWithinMs: 5_000 });
      expect(fetcher).toHaveBeenCalledTimes(2);
      vi.advanceTimersByTime(5_000);
      await replacingWouldLose(fetcher, { reuseRunsReadWithinMs: 5_000 });
      expect(fetcher).toHaveBeenCalledTimes(3);

      // A clock that stepped back is not a recent read: both kinds read again.
      vi.setSystemTime(Date.now() - 60_000);
      await replacingWouldLose(fetcher, { reuseRunsReadWithinMs: 5_000 });
      expect(fetcher).toHaveBeenCalledTimes(4);
      vi.setSystemTime(Date.now() - 60_000);
      await replacingWouldLose(fetcher);
      expect(fetcher).toHaveBeenCalledTimes(5);
    } finally {
      vi.useRealTimers();
    }
  });

  it('counts a design as unkept when the run list cannot be read', async () => {
    useDesignStore.getState().updateField('R', 321);
    jobsList([], false);
    const fetcher = vi.fn(async () => new Response('{"detail":"busy"}', { status: 500 }));

    await expect(replacingWouldLose(fetcher as unknown as typeof fetch)).resolves.toBe(true);
    expect(replacingWouldLoseNow()).toBe(true);
  });

  it('never reads the run list when the design is kept already', async () => {
    applyOpenedDesign(openedReport(150), 'horn.cfg');
    jobsList([], false);
    const fetcher = vi.fn(async () => new Response('{}', { status: 200 }));

    await expect(replacingWouldLose(fetcher as unknown as typeof fetch)).resolves.toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('answers a half-typed directivity setting as unkept, without throwing', async () => {
    applyOpenedDesign(openedReport(150), 'horn.cfg');
    useSolveOptionsStore.setState((state) => ({ polar: { ...state.polar, angleStep: 0 } }));

    expect(() => replacingWouldLoseNow()).not.toThrow();
    expect(replacingWouldLoseNow()).toBe(true);
    await expect(replacingWouldLose()).resolves.toBe(true);
  });

  it('keeps a restored legacy draft recoverable, and counts it as kept nowhere', () => {
    // Written before drafts carried a name or settings, and "clean" by the
    // old saved-revision baseline: its revision equals its saved revision.
    const draft = {
      version: 1,
      savedAt: '2026-08-01T00:00:00Z',
      filename: 'legacy.cfg',
      designRevision: 5,
      savedRevision: 5,
      design: { ...seed(), R: 321 },
    };
    const storage = {
      getItem: (name: string) => (name === AUTOSAVE_KEY ? JSON.stringify(draft) : null),
      setItem: () => undefined,
      removeItem: () => undefined,
    };
    // Before the restore, the document on screen was opened with exactly the
    // draft's content: a key left over from it would call the draft kept.
    applyOpenedDesign(openedReport(321), 'legacy.cfg');
    expect(replacingWouldLoseNow()).toBe(false);

    expect(restoreAutosave(storage)).toBe(true);

    expect(useDesignStore.getState().design.R).toBe(321);
    expect(useDocumentStore.getState()).toMatchObject({ designName: 'legacy', openedContentKey: null });
    // The old baseline would call this clean; the draft is the only copy.
    expect(unsavedChangesNow()).toBe(false);
    expect(replacingWouldLoseNow()).toBe(true);
  });

  it('remembers what a send committed, and forgets a registry copy the send overwrote', () => {
    const report = openedReport(150) as unknown as Record<string, unknown>;
    applyOpenedDesign({
      ...report,
      cadlink: { identity: { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 1 }, classification: 'current' },
    } as unknown as Parameters<typeof applyOpenedDesign>[0], 'horn.cfg');
    const opened = useDocumentStore.getState().openedContentKey;
    expect(opened).not.toBeNull();

    // The document on screen was the one sent: the registry now holds that.
    rememberSentCopy('v1:sent', 'wgd_a', true);
    expect(useDocumentStore.getState().openedContentKey).toBe('v1:sent');

    // Another document, opened from the same registry design: its copy is gone.
    useDocumentStore.getState().setOpenedContentKey(opened);
    rememberSentCopy('v1:sent', 'wgd_a', false);
    expect(useDocumentStore.getState().openedContentKey).toBeNull();

    // Another document, linked elsewhere: its own copy still stands.
    useDocumentStore.getState().setOpenedContentKey(opened);
    rememberSentCopy('v1:sent', 'wgd_b', false);
    expect(useDocumentStore.getState().openedContentKey).toBe(opened);
  });

  it('changes no store while it decides', async () => {
    applyOpenedDesign(openedReport(150), 'horn.cfg');
    useDesignStore.getState().updateField('R', 321);
    jobsList([storedRun({ ...seed(), R: 200 })], false);
    const fetcher = vi.fn(async () => new Response(
      JSON.stringify({ items: [storedRun({ ...seed(), R: 321 })], total: 1 }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));
    const stores = () => ({
      design: useDesignStore.getState(),
      history: useDesignStore.temporal.getState(),
      document: useDocumentStore.getState(),
      solveOptions: useSolveOptionsStore.getState(),
      jobs: jobsSocket.getSnapshot(),
    });
    const before = stores();
    const serialized = JSON.stringify(before);

    replacingWouldLoseNow();
    keptContentKeyNow();
    await replacingWouldLose(fetcher as unknown as typeof fetch);
    replacingWouldLoseNow();

    const after = stores();
    expect(after.design).toBe(before.design);
    expect(after.history).toBe(before.history);
    expect(after.document).toBe(before.document);
    expect(after.solveOptions).toBe(before.solveOptions);
    expect(JSON.stringify(after)).toBe(serialized);
  });
});

describe('a restored autosave draft', () => {
  function memoryStorage() {
    const values = new Map<string, string>();
    return {
      values,
      getItem: (name: string) => values.get(name) ?? null,
      setItem: (name: string, value: string) => { values.set(name, value); },
      removeItem: (name: string) => { values.delete(name); },
    };
  }

  /** A restart: every store back to a fresh window's, then the draft restored. */
  function restart(storage: ReturnType<typeof memoryStorage>): boolean {
    resetDesignStore();
    resetDocumentStore();
    return restoreAutosave(storage);
  }

  it('keeps the kept verdict of a draft still equal to what was opened', () => {
    const storage = memoryStorage();
    applyOpenedDesign(openedReport(150), 'horn.cfg');
    const opened = useDocumentStore.getState().openedContentKey;
    expect(writeAutosave(storage)).toBe(true);

    expect(restart(storage)).toBe(true);

    expect(useDesignStore.getState().design.R).toBe(150);
    expect(useDocumentStore.getState().openedContentKey).toBe(opened);
    expect(replacingWouldLoseNow()).toBe(false);
  });

  it('restores the key recorded beside an edited draft, not one derived from the draft', () => {
    const storage = memoryStorage();
    applyOpenedDesign(openedReport(150), 'horn.cfg');
    const opened = useDocumentStore.getState().openedContentKey;
    useDesignStore.getState().updateField('R', 321);
    expect(writeAutosave(storage)).toBe(true);

    expect(restart(storage)).toBe(true);

    expect(useDesignStore.getState().design.R).toBe(321);
    expect(useDocumentStore.getState().openedContentKey).toBe(opened);
    expect(useDocumentStore.getState().openedContentKey).not.toBe(keptContentKeyNow());
    expect(replacingWouldLoseNow()).toBe(true);
    // The edit taken back after the restart is the opened design again.
    useDesignStore.getState().updateField('R', 150);
    expect(replacingWouldLoseNow()).toBe(false);
  });

  it.each([
    ['another key format', { contentKeyVersion: 'v0' }],
    ['no key format', { contentKeyVersion: undefined }],
    ['a key that is not text', { openedContentKey: 42 }],
    ['a key under another prefix than its format', { openedContentKey: 'v0:{}' }],
  ])('restores a draft recorded with %s, and counts it as kept nowhere', (_case, change) => {
    const storage = memoryStorage();
    applyOpenedDesign(openedReport(150), 'horn.cfg');
    expect(writeAutosave(storage)).toBe(true);
    const record = { ...JSON.parse(storage.values.get(AUTOSAVE_KEY)!), ...change };
    storage.values.set(AUTOSAVE_KEY, JSON.stringify(record));

    expect(restart(storage)).toBe(true);

    expect(useDesignStore.getState().design.R).toBe(150);
    expect(useDocumentStore.getState()).toMatchObject({ designName: 'horn', openedContentKey: null });
    expect(replacingWouldLoseNow()).toBe(true);
  });
});
