import { beforeEach, describe, expect, it, vi } from 'vitest';
import { workspaceModeStore } from './workspaceMode';

describe('workspace mode store', () => {
  beforeEach(() => workspaceModeStore.setMode('parametric'));

  it('defaults to parametric', () => {
    expect(workspaceModeStore.getSnapshot()).toEqual({ mode: 'parametric' });
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

  it('does not persist mode across a simulated module reload', async () => {
    workspaceModeStore.setMode('cad');
    vi.resetModules();
    const reloaded = await import('./workspaceMode');
    expect(reloaded.workspaceModeStore.getSnapshot()).toEqual({ mode: 'parametric' });
  });
});
