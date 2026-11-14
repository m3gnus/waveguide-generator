import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AUTOSAVE_KEY, MAX_REJECTED_DRAFT_BYTES, MAX_REJECTED_DRAFTS, REJECTED_DRAFTS_NAMESPACE,
  restoreAutosave, restoreAutosaveWithLateRetry, startAutosave, writeAutosave,
} from './autosave';
import { resetDesignStore, seedDesign, useDesignStore } from './design';
import { DESIGN_CONTENT_KEY_VERSION } from './designContentKey';
import { DurableSettings, namespaceStorage, SETTINGS_NAMESPACES } from './durableSettings';
import { resetDocumentStore, useDocumentStore } from './document';
import { workspaceModeStore } from './workspaceMode';
import { useCadReturnStore } from './cadReturn';
import { observeCadProjectLoads } from '../shell/cadlink/restores';

describe('design autosave', () => {
  beforeEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    resetDesignStore();
    resetDocumentStore();
  });

  it('round-trips the design, name, and revision', () => {
    useDesignStore.getState().updateField('R', 177);
    useDocumentStore.getState().setDesignName('Recovered Horn');
    expect(writeAutosave()).toBe(true);
    // Keep the required v1 field for older readers without tracking a baseline.
    expect(JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!).savedRevision).toBeNull();

    resetDesignStore();
    resetDocumentStore();
    expect(restoreAutosave()).toBe(true);
    expect(useDesignStore.getState()).toMatchObject({ designRevision: 2, design: { R: 177 } });
    expect(useDocumentStore.getState()).toMatchObject({
      designName: 'Recovered Horn', filename: 'Recovered_Horn.cfg',
    });
    expect(useDesignStore.temporal.getState().pastStates).toEqual([]);
  });

  it('round-trips identity without advancing its persisted edit version', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 7,
    }, 'stale_copy');
    useDesignStore.getState().updateField('R', 181);
    expect(writeAutosave()).toBe(true);

    const record = JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!);
    expect(record.identity.baseEditVersion).toBe(7);
    resetDocumentStore();
    expect(restoreAutosave()).toBe(true);
    expect(useDocumentStore.getState()).toMatchObject({
      identity: { baseEditVersion: 7 },
      classification: 'stale_copy',
    });
  });

  it('records the remembered-copy key and its format beside the draft, not in it', () => {
    useDesignStore.getState().updateField('R', 177);
    useDocumentStore.getState().setOpenedContentKey(`${DESIGN_CONTENT_KEY_VERSION}:opened`);
    expect(writeAutosave()).toBe(true);

    const record = JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!);
    expect(record).toMatchObject({
      openedContentKey: `${DESIGN_CONTENT_KEY_VERSION}:opened`,
      contentKeyVersion: DESIGN_CONTENT_KEY_VERSION,
    });
    expect(record.design).not.toHaveProperty('openedContentKey');

    resetDesignStore();
    resetDocumentStore();
    expect(restoreAutosave()).toBe(true);
    expect(useDocumentStore.getState().openedContentKey).toBe(`${DESIGN_CONTENT_KEY_VERSION}:opened`);
  });

  it('restores a draft written before the remembered-copy key with none', () => {
    useDesignStore.getState().updateField('R', 177);
    useDocumentStore.getState().setOpenedContentKey(`${DESIGN_CONTENT_KEY_VERSION}:opened`);
    expect(writeAutosave()).toBe(true);
    const legacy = JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!);
    legacy.savedRevision = 1;
    legacy.savedDesignName = 'Opened Horn';
    legacy.savedSettings = '{"distance":2}';
    delete legacy.openedContentKey;
    delete legacy.contentKeyVersion;
    localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(legacy));

    resetDesignStore();
    expect(restoreAutosave()).toBe(true);
    expect(useDesignStore.getState().design.R).toBe(177);
    expect(useDocumentStore.getState().openedContentKey).toBeNull();
  });

  it('rewrites the draft when only the remembered copy changes', () => {
    vi.useFakeTimers();
    const controller = startAutosave(localStorage, 250, window, document);
    // What a design file export or a successful Send to CAD does to the document.
    useDocumentStore.getState().setOpenedContentKey(`${DESIGN_CONTENT_KEY_VERSION}:exported`);
    vi.advanceTimersByTime(250);

    expect(localStorage.getItem(AUTOSAVE_KEY)).not.toBeNull();
    expect(JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!).openedContentKey)
      .toBe(`${DESIGN_CONTENT_KEY_VERSION}:exported`);
    controller.dispose();
  });

  it('restores a version-1 draft written before identity was added as unlinked', () => {
    expect(writeAutosave()).toBe(true);
    const legacy = JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!);
    delete legacy.identity;
    delete legacy.classification;
    localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(legacy));
    expect(restoreAutosave()).toBe(true);
    expect(useDocumentStore.getState()).toMatchObject({ identity: null, classification: null });
  });

  /** A real DurableSettings over this test's browser storage and a scripted server. */
  function settingsWith(server: { accept?: boolean; envelope?: object; hold?: Promise<void> } = {}) {
    const calls: { url: string; method: string }[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/settings') {
        await server.hold;
        return new Response(JSON.stringify(server.envelope ?? { namespaces: {} }), { status: 200 });
      }
      calls.push({ url, method: init?.method ?? 'GET' });
      return new Response('{}', { status: server.accept === false ? 500 : 200 });
    });
    const settings = new DurableSettings({ storage: localStorage, fetcher: fetcher as unknown as typeof fetch, writeDelayMs: () => 0 });
    return { settings, calls, draft: namespaceStorage('designDraft', settings) };
  }

  const keptDrafts = () => JSON.parse(localStorage.getItem(SETTINGS_NAMESPACES.rejectedDesignDraft)!)
    .drafts.map((entry: { draft: string }) => entry.draft);

  it('sets a draft it cannot restore aside, and clears it only once the server has the copy', async () => {
    const { settings, calls, draft } = settingsWith();
    const corrupt = JSON.stringify({ version: 1, savedAt: '2026-11-13T21:04:00.000Z', design: { formula: 'BROKEN' } });
    settings.set('designDraft', corrupt);
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    expect(restoreAutosave(draft, settings)).toBe(false);
    expect(useDesignStore.getState().design).toEqual(seedDesign);
    await vi.waitFor(() => expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull());
    // Stored first, and only then taken out of the restore slot.
    const stored = calls.findIndex((call) => call.url === `/api/settings/${REJECTED_DRAFTS_NAMESPACE}` && call.method === 'PUT');
    const cleared = calls.findIndex((call) => call.url === '/api/settings/designDraft' && call.method === 'DELETE');
    expect(stored).toBeGreaterThan(0);
    expect(cleared).toBeGreaterThan(stored);
    expect(keptDrafts()).toEqual([corrupt]);
    const notice = useDesignStore.getState().loadNotice!;
    expect(notice).toContain('could not be restored');
    expect(notice).toContain(`“${REJECTED_DRAFTS_NAMESPACE}”`);
    expect(notice).toContain('ui_settings.json');

    // Nothing left to fail on: a second start is silent and keeps what it set aside.
    useDesignStore.setState({ loadNotice: null });
    expect(restoreAutosave(draft, settings)).toBe(false);
    await Promise.resolve();
    expect(useDesignStore.getState().loadNotice).toBeNull();
    expect(keptDrafts()).toEqual([corrupt]);
  });

  it('leaves the draft in place when the server does not take the set-aside copy', async () => {
    const { settings, calls, draft } = settingsWith({ accept: false });
    const corrupt = JSON.stringify({ version: 1, design: { formula: 'BROKEN' } });
    localStorage.setItem(AUTOSAVE_KEY, corrupt);
    expect(restoreAutosave(draft, settings)).toBe(false);
    await vi.waitFor(() => expect(calls).toContainEqual({ url: `/api/settings/${REJECTED_DRAFTS_NAMESPACE}`, method: 'PUT' }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(localStorage.getItem(AUTOSAVE_KEY)).toBe(corrupt);
    expect(calls).not.toContainEqual({ url: '/api/settings/designDraft', method: 'DELETE' });
    expect(useDesignStore.getState().loadNotice).toContain('could not be restored');
  });

  it('keeps the newest five set-aside drafts and says when older ones went', async () => {
    const { settings, draft } = settingsWith();
    const broken = (index: number) => JSON.stringify({ version: 1, design: { formula: `BROKEN-${index}` } });
    for (let index = 1; index <= MAX_REJECTED_DRAFTS + 1; index += 1) {
      localStorage.setItem(AUTOSAVE_KEY, broken(index));
      useDesignStore.setState({ loadNotice: null });
      expect(restoreAutosave(draft, settings)).toBe(false);
      await vi.waitFor(() => expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull());
    }
    expect(keptDrafts()).toEqual([2, 3, 4, 5, 6].map(broken));
    expect(useDesignStore.getState().loadNotice).toContain('the oldest draft set aside earlier was removed');
  });

  it('drops the oldest drafts to stay under the server size limit', async () => {
    const { settings, draft } = settingsWith();
    const big = (index: number) => JSON.stringify({ version: 1, design: { formula: `BROKEN-${index}`, pad: 'x'.repeat(70_000) } });
    for (let index = 1; index <= 4; index += 1) {
      localStorage.setItem(AUTOSAVE_KEY, big(index));
      expect(restoreAutosave(draft, settings)).toBe(false);
      await vi.waitFor(() => expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull());
    }
    const stored = localStorage.getItem(SETTINGS_NAMESPACES.rejectedDesignDraft)!;
    expect(JSON.stringify(stored).length).toBeLessThanOrEqual(MAX_REJECTED_DRAFT_BYTES);
    expect(keptDrafts().at(-1)).toBe(big(4));
    expect(keptDrafts().length).toBeLessThan(4);
  });

  it('adds to the drafts the server already holds, not to a stale browser copy', async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => { release = resolve; });
    const earlier = JSON.stringify({ version: 1, design: { formula: 'EARLIER' } });
    const { settings, draft } = settingsWith({
      hold,
      envelope: { namespaces: { [REJECTED_DRAFTS_NAMESPACE]: JSON.stringify({ version: 1, drafts: [{ rejectedAt: '', draft: earlier }] }) } },
    });
    const hydration = settings.hydrate();
    const corrupt = JSON.stringify({ version: 1, design: { formula: 'BROKEN' } });
    localStorage.setItem(AUTOSAVE_KEY, corrupt);
    expect(restoreAutosave(draft, settings)).toBe(false);
    await Promise.resolve();
    expect(localStorage.getItem(SETTINGS_NAMESPACES.rejectedDesignDraft)).toBeNull();
    release();
    await hydration;
    await vi.waitFor(() => expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull());
    expect(keptDrafts()).toEqual([earlier, corrupt]);
  });

  it('sets aside a draft whose restore throws part-way', async () => {
    const { settings, draft } = settingsWith();
    useDesignStore.getState().updateField('R', 177);
    expect(writeAutosave(draft)).toBe(true);
    const raw = localStorage.getItem(AUTOSAVE_KEY)!;
    resetDesignStore();
    const replaceDocument = vi.spyOn(useDesignStore.getState(), 'replaceDocument')
      .mockImplementation(() => { throw new Error('replace failed'); });
    try {
      expect(restoreAutosave(draft, settings)).toBe(false);
    } finally {
      replaceDocument.mockRestore();
    }
    await vi.waitFor(() => expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull());
    expect(keptDrafts()).toEqual([raw]);
  });

  it('retries restoration when a durable draft arrives after the cache was checked', () => {
    useDesignStore.getState().updateField('R', 177);
    expect(writeAutosave()).toBe(true);
    const raw = localStorage.getItem(AUTOSAVE_KEY)!;
    resetDesignStore();

    const values = new Map<string, string>();
    const storage = {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
      removeItem: vi.fn((key: string) => { values.delete(key); }),
    };
    let notify: ((raw: string | null) => void) | undefined;
    const settings = {
      subscribe: vi.fn((_namespace: 'designDraft', listener: (raw: string | null) => void) => {
        notify = listener;
        return vi.fn();
      }),
    };

    const unsubscribe = restoreAutosaveWithLateRetry(settings, storage);
    expect(useDesignStore.getState().design).toEqual(seedDesign);

    values.set(AUTOSAVE_KEY, raw);
    notify?.(raw);
    expect(useDesignStore.getState().design.R).toBe(177);

    useDesignStore.getState().updateField('R', 199);
    notify?.(raw);
    expect(useDesignStore.getState().design.R).toBe(199);
    unsubscribe();
  });

  it('replaces an untouched cached draft when a newer durable draft arrives late', () => {
    useDesignStore.getState().updateField('R', 150);
    expect(writeAutosave()).toBe(true);
    const cached = localStorage.getItem(AUTOSAVE_KEY)!;
    useDesignStore.getState().updateField('R', 200);
    expect(writeAutosave()).toBe(true);
    const durable = localStorage.getItem(AUTOSAVE_KEY)!;
    resetDesignStore();
    resetDocumentStore();

    const values = new Map([[AUTOSAVE_KEY, cached]]);
    const storage = {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
      removeItem: vi.fn((key: string) => { values.delete(key); }),
    };
    let notify: ((raw: string | null) => void) | undefined;
    const settings = {
      subscribe: vi.fn((_namespace: 'designDraft', listener: (raw: string | null) => void) => {
        notify = listener;
        return vi.fn();
      }),
    };

    const unsubscribe = restoreAutosaveWithLateRetry(settings, storage);
    expect(useDesignStore.getState().design.R).toBe(150);

    values.set(AUTOSAVE_KEY, durable);
    notify?.(durable);
    expect(useDesignStore.getState().design.R).toBe(200);
    unsubscribe();
  });

  it('does not replace intervening edits when a durable draft arrives late', () => {
    useDesignStore.getState().updateField('R', 150);
    expect(writeAutosave()).toBe(true);
    const cached = localStorage.getItem(AUTOSAVE_KEY)!;
    useDesignStore.getState().updateField('R', 200);
    expect(writeAutosave()).toBe(true);
    const durable = localStorage.getItem(AUTOSAVE_KEY)!;
    resetDesignStore();
    resetDocumentStore();

    const values = new Map([[AUTOSAVE_KEY, cached]]);
    const storage = {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
      removeItem: vi.fn((key: string) => { values.delete(key); }),
    };
    let notify: ((raw: string | null) => void) | undefined;
    const settings = {
      subscribe: vi.fn((_namespace: 'designDraft', listener: (raw: string | null) => void) => {
        notify = listener;
        return vi.fn();
      }),
    };

    const unsubscribe = restoreAutosaveWithLateRetry(settings, storage);
    expect(useDesignStore.getState().design.R).toBe(150);
    useDesignStore.getState().updateField('R', 175);

    values.set(AUTOSAVE_KEY, durable);
    notify?.(durable);
    expect(useDesignStore.getState().design.R).toBe(175);
    unsubscribe();
  });

  it('debounces edits and flushes the latest draft before unload', () => {
    vi.useFakeTimers();
    const controller = startAutosave(localStorage, 250, window, document);
    useDesignStore.getState().updateField('R', 150);
    useDesignStore.getState().updateField('R', 160);
    expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull();
    vi.advanceTimersByTime(249);
    expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull();
    vi.advanceTimersByTime(1);
    expect(JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!).design.R).toBe(160);

    useDesignStore.getState().updateField('R', 170);
    window.dispatchEvent(new Event('beforeunload'));
    expect(JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!).design.R).toBe(170);

    useDesignStore.getState().updateField('R', 180);
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
    expect(JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!).design.R).toBe(180);
    visibility.mockRestore();
    controller.dispose();
  });

  it('leaves CAD mode and the CAD ingest alone when a draft is restored late', () => {
    useDesignStore.getState().updateField('R', 177);
    expect(writeAutosave()).toBe(true);
    const draft = localStorage.getItem(AUTOSAVE_KEY)!;
    resetDesignStore();
    resetDocumentStore();
    workspaceModeStore.setMode('cad');
    const markStale = vi.spyOn(useCadReturnStore.getState(), 'markIngestStale');
    const ref = <T,>(current: T) => ({ current });
    const stop = observeCadProjectLoads({
      enterCadWorkspace: vi.fn(), manualSelectionAt: ref(null), projectOpenPending: ref(false),
      refreshRef: ref(vi.fn()), refusedForeignReturn: ref(false), returnListRequest: ref(0),
      seenReturnRevisions: ref(null), setError: vi.fn(), setStatus: vi.fn(),
    } as unknown as Parameters<typeof observeCadProjectLoads>[0]);

    let notify: (() => void) | undefined;
    const storage = { getItem: vi.fn(() => null as string | null), setItem: vi.fn(), removeItem: vi.fn() };
    const unsubscribe = restoreAutosaveWithLateRetry({
      subscribe: (_namespace, listener) => { notify = () => listener(draft); return () => undefined; },
    }, storage);
    storage.getItem.mockReturnValue(draft);
    notify?.();

    expect(useDesignStore.getState().design.R).toBe(177);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(markStale).not.toHaveBeenCalled();
    unsubscribe();
    stop();
    markStale.mockRestore();
    workspaceModeStore.setMode('parametric');
  });
});

it('retains a permanent id in autosave and mints one for older drafts', () => {
  resetDocumentStore();
  const uid = useDocumentStore.getState().designUid;
  writeAutosave();
  resetDocumentStore();
  expect(restoreAutosave()).toBe(true);
  expect(useDocumentStore.getState().designUid).toBe(uid);
  const old = JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!);
  delete old.designUid;
  localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(old));
  expect(restoreAutosave()).toBe(true);
  expect(useDocumentStore.getState().designUid).toMatch(/^wgu_/);
  expect(useDocumentStore.getState().designUid).not.toBe(uid);
});

it('restores the design assembly with its identities', () => {
  resetDesignStore();
  const woofer = '00000000-0000-4000-8000-0000000000aa';
  const assembly = {
    hf_source: { radiator_id: 'horn-throat', kind: 'classic' as const },
    woofers: [{ id: woofer, label: 'Woofer 1', enabled: false, radius_mm: 80, shape: { kind: 'piston' as const },
      placement: { anchor: 'below' as const, gap_mm: 10 }, drive: 'prescribed' as const }],
    signal_groups: [
      { id: '00000000-0000-4000-8000-0000000000b1', label: 'HF', members: [{ radiator_id: 'horn-throat' }] },
      { id: '00000000-0000-4000-8000-0000000000b2', label: 'Woofer 1', members: [{ radiator_id: woofer }] },
    ],
  };
  useDesignStore.getState().commitEmbedded({ assembly }, 'step');
  writeAutosave();
  resetDesignStore();
  expect(useDesignStore.getState().design.assembly).toBeUndefined();
  expect(restoreAutosave()).toBe(true);
  expect(useDesignStore.getState().design.assembly).toEqual(assembly);
});
