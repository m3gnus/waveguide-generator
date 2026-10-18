import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { workspaceModeStore } from './workspaceMode';

describe('late workspace hydration', () => {
  beforeEach(() => { vi.resetModules(); localStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('adopts the durable workspace and input after startup timed out', async () => {
    let answer!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; })));
    const { durableSettings } = await import('./durableSettings');
    const hydration = durableSettings.hydrate({ timeoutMs: 1_500 });
    await vi.advanceTimersByTimeAsync(1_500);
    await hydration;
    const { workspaceModeStore: store } = await import('./workspaceMode');
    const listener = vi.fn(); store.subscribe(listener);
    const inputs: string[] = [];
    store.subscribe(() => inputs.push(store.getSnapshot().importedInput));
    answer(new Response(JSON.stringify({ namespaces: { workspaceMode: 'cad', importedInput: 'file' } })));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getSnapshot()).toEqual({ mode: 'cad', importedInput: 'file', inputRevision: 1 });
    expect(listener).toHaveBeenCalled();
    expect(inputs).toEqual(['file']);
    const snapshot = store.getSnapshot();
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ namespaces: { workspaceMode: 'cad', importedInput: 'file' } }))));
    await durableSettings.hydrate();
    expect(store.getSnapshot()).toBe(snapshot);
  });

  it('adopts deleted mode and input without writing their defaults back', async () => {
    localStorage.setItem('wg2.workspace.mode.v1', 'cad');
    localStorage.setItem('wg2.imported.input.v1', 'file');
    const { durableSettings } = await import('./durableSettings');
    const { workspaceModeStore: store } = await import('./workspaceMode');
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ deleted: ['workspaceMode', 'importedInput'] }))));
    await durableSettings.hydrate();
    expect(store.getSnapshot()).toEqual({ mode: 'parametric', importedInput: 'cad', inputRevision: 1 });
    expect(localStorage.getItem('wg2.workspace.mode.v1')).toBeNull();
    expect(localStorage.getItem('wg2.imported.input.v1')).toBeNull();
  });

  it('preserves input and mode chosen while hydration is pending', async () => {
    let answer!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; })));
    const { durableSettings } = await import('./durableSettings');
    const { workspaceModeStore: store } = await import('./workspaceMode');
    const hydration = durableSettings.hydrate();
    store.setMode('cad'); store.setImportedInput('file');
    const snapshot = store.getSnapshot();
    answer(new Response(JSON.stringify({ namespaces: { workspaceMode: 'parametric', importedInput: 'cad' } })));
    await hydration;
    expect(store.getSnapshot()).toBe(snapshot);
  });

  it('preserves an explicit choice of the already visible mode during hydration', async () => {
    let answer!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; })));
    const { durableSettings } = await import('./durableSettings');
    const { workspaceModeStore: store } = await import('./workspaceMode');
    const hydration = durableSettings.hydrate();
    const snapshot = store.getSnapshot();
    store.setMode('parametric');
    answer(new Response(JSON.stringify({ namespaces: { workspaceMode: 'cad' } })));
    await hydration;
    expect(store.getSnapshot()).toBe(snapshot);
  });
});

describe('workspace mode store', () => {
  beforeEach(() => workspaceModeStore.setMode('parametric'));

  it('defaults to parametric', () => {
    expect(workspaceModeStore.getSnapshot()).toMatchObject({ mode: 'parametric' });
  });

  it('changes mode through its single writer', () => {
    workspaceModeStore.setMode('cad');
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  });

  it('keeps snapshots reference-stable until the mode changes', () => {
    const initial = workspaceModeStore.getSnapshot();
    expect(workspaceModeStore.getSnapshot()).toBe(initial);
    workspaceModeStore.setMode('parametric');
    expect(workspaceModeStore.getSnapshot()).toBe(initial);
    workspaceModeStore.setMode('cad');
    expect(workspaceModeStore.getSnapshot()).not.toBe(initial);
  });

  it('persists mode across a simulated module reload', async () => {
    // The old behaviour was the opposite: a reload always fell back to
    // Parametric because a CAD return could not be restored. It can now, so
    // the workspace comes back the way it was left.
    workspaceModeStore.setMode('cad');
    vi.resetModules();
    const reloaded = await import('./workspaceMode');
    expect(reloaded.workspaceModeStore.getSnapshot()).toMatchObject({ mode: 'cad' });
  });

  it('remembers the mode for the next load', () => {
    workspaceModeStore.setMode('cad');
    expect(localStorage.getItem('wg2.workspace.mode.v1')).toBe('cad');
    workspaceModeStore.setMode('parametric');
    expect(localStorage.getItem('wg2.workspace.mode.v1')).toBe('parametric');
  });
});

it('preserves the selected imported input across a mode round-trip and module reload', async () => {
  workspaceModeStore.setImportedInput('file');
  workspaceModeStore.setMode('cad'); workspaceModeStore.setMode('parametric'); workspaceModeStore.setMode('cad');
  expect(workspaceModeStore.getSnapshot().importedInput).toBe('file');
  vi.resetModules();
  const reloaded = await import('./workspaceMode');
  expect(reloaded.workspaceModeStore.getSnapshot()).toMatchObject({ mode: 'cad', importedInput: 'file' });
  workspaceModeStore.setImportedInput('cad');
});

it.each(['parametric', 'cad'] as const)('keeps the persisted %s mode however a last model is remembered', async (mode) => {
  // Start-up never forces a mode: the saved model only matters once Imported mode is already showing.
  workspaceModeStore.setMode(mode);
  localStorage.setItem('wg2.last.model.v1', JSON.stringify({ model_id: 'mesh-file:sha256:a', input: 'file' }));
  localStorage.setItem('wg-saved-mesh-setup', JSON.stringify({ model_id: 'mesh-file:sha256:a', prepared_id: 'wgi_a' }));
  localStorage.setItem('wg2.cad.project.v1', 'wgl_project');
  vi.resetModules();
  const reloaded = await import('./workspaceMode');
  expect(reloaded.workspaceModeStore.getSnapshot().mode).toBe(mode);
  workspaceModeStore.setMode('parametric');
});
