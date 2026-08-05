import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AUTOSAVE_KEY, restoreAutosave, startAutosave, writeAutosave } from './autosave';
import { resetDesignStore, seedDesign, useDesignStore } from './design';
import { resetDocumentStore, useDocumentStore } from './document';

describe('design autosave', () => {
  beforeEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    resetDesignStore();
    resetDocumentStore();
  });

  it('round-trips the design, filename, revision, and explicit-save state', () => {
    useDesignStore.getState().updateField('R', 177);
    useDocumentStore.getState().setFilename('recovered.cfg');
    useDocumentStore.getState().markSaved(1);
    expect(writeAutosave()).toBe(true);

    resetDesignStore();
    resetDocumentStore();
    expect(restoreAutosave()).toBe(true);
    expect(useDesignStore.getState()).toMatchObject({ designRevision: 2, design: { R: 177 } });
    expect(useDocumentStore.getState()).toMatchObject({ filename: 'recovered.cfg', savedRevision: 1 });
    expect(useDesignStore.temporal.getState().pastStates).toEqual([]);
  });

  it('rejects and removes a corrupt draft without replacing the current design', () => {
    localStorage.setItem(AUTOSAVE_KEY, JSON.stringify({ version: 1, design: { formula: 'BROKEN' } }));
    expect(restoreAutosave()).toBe(false);
    expect(localStorage.getItem(AUTOSAVE_KEY)).toBeNull();
    expect(useDesignStore.getState().design).toEqual(seedDesign);
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
