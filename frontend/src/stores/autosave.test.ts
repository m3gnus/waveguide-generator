import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AUTOSAVE_KEY, restoreAutosave, restoreAutosaveWithLateRetry, startAutosave, writeAutosave } from './autosave';
import { resetDesignStore, seedDesign, useDesignStore } from './design';
import { DESIGN_CONTENT_KEY_VERSION } from './designContentKey';
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
    // What Export a copy or a successful Send to CAD does to the document.
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

  it('rejects and removes a corrupt draft without replacing the current design', () => {
    localStorage.setItem(AUTOSAVE_KEY, JSON.stringify({ version: 1, design: { formula: 'BROKEN' } }));
    expect(restoreAutosave()).toBe(false);
    expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull();
    expect(useDesignStore.getState().design).toEqual(seedDesign);
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
