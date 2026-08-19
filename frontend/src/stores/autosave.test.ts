import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AUTOSAVE_KEY, restoreAutosave, restoreAutosaveWithLateRetry, startAutosave, writeAutosave } from './autosave';
import { resetDesignStore, seedDesign, useDesignStore } from './design';
import { resetDocumentStore, useDocumentStore } from './document';

describe('design autosave', () => {
  beforeEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    resetDesignStore();
    resetDocumentStore();
  });

  it('round-trips the design, name, revision, and explicit-save state', () => {
    useDesignStore.getState().updateField('R', 177);
    useDocumentStore.getState().setDesignName('Recovered Horn');
    useDocumentStore.getState().markSaved(1);
    expect(writeAutosave()).toBe(true);

    resetDesignStore();
    resetDocumentStore();
    expect(restoreAutosave()).toBe(true);
    expect(useDesignStore.getState()).toMatchObject({ designRevision: 2, design: { R: 177 } });
    expect(useDocumentStore.getState()).toMatchObject({
      designName: 'Recovered Horn', filename: 'Recovered_Horn.cfg', savedRevision: 1,
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
});
