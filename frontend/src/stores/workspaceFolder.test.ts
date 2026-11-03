import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getWorkspaceFolder, openWorkspaceFolder, selectWorkspaceFolder } from '../api/workspace';
import { resetWorkspaceFolderStore, useWorkspaceFolderStore } from './workspaceFolder';

vi.mock('../api/workspace', () => ({
  getWorkspaceFolder: vi.fn(), openWorkspaceFolder: vi.fn(), selectWorkspaceFolder: vi.fn(),
}));

describe('workspace folder request ownership', () => {
  beforeEach(() => { vi.resetAllMocks(); resetWorkspaceFolderStore(); });
  afterEach(() => resetWorkspaceFolderStore());

  it.each(['open', 'select'] as const)('clears %s busy state when a consumer loads during the action', async (action) => {
    let finish!: (folder: { path: string; selected: boolean }) => void;
    vi.mocked(action === 'open' ? openWorkspaceFolder : selectWorkspaceFolder)
      .mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    vi.mocked(getWorkspaceFolder).mockResolvedValue({ path: '/old/runs', selected: false });
    const pending = useWorkspaceFolderStore.getState()[action]();
    const loading = useWorkspaceFolderStore.getState().load();
    expect(useWorkspaceFolderStore.getState().busy).toBe(action);
    finish({ path: '/new/runs', selected: true });
    await Promise.all([pending, loading]);
    expect(useWorkspaceFolderStore.getState()).toMatchObject({ path: '/new/runs', busy: null, loaded: true });
  });

  it('rejects a slow read started before a selection', async () => {
    let finish!: (folder: { path: string; selected: boolean }) => void;
    vi.mocked(getWorkspaceFolder).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    vi.mocked(selectWorkspaceFolder).mockResolvedValue({ path: '/new/runs', selected: true });
    const loading = useWorkspaceFolderStore.getState().load();
    await useWorkspaceFolderStore.getState().select();
    finish({ path: '/old/runs', selected: false });
    await loading;
    expect(useWorkspaceFolderStore.getState()).toMatchObject({ path: '/new/runs', selected: true, busy: null });
  });

  it.each(['open', 'select'] as const)('clears %s busy state and keeps its error after a coalesced load', async (action) => {
    let fail!: (reason: Error) => void;
    vi.mocked(action === 'open' ? openWorkspaceFolder : selectWorkspaceFolder)
      .mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject; }));
    const pending = useWorkspaceFolderStore.getState()[action]();
    const loading = useWorkspaceFolderStore.getState().load();
    fail(new Error('The folder is unavailable. Choose another folder in Settings.'));
    await Promise.all([pending, loading]);
    expect(useWorkspaceFolderStore.getState()).toMatchObject({
      busy: null, error: 'The folder is unavailable. Choose another folder in Settings.',
    });
    expect(getWorkspaceFolder).not.toHaveBeenCalled();
  });

  it('keeps the newer mutation busy when an older one finishes', async () => {
    let finishOpen!: (folder: { path: string; selected: boolean }) => void;
    let finishSelect!: (folder: { path: string; selected: boolean }) => void;
    vi.mocked(openWorkspaceFolder).mockReturnValueOnce(new Promise(resolve => { finishOpen = resolve; }));
    vi.mocked(selectWorkspaceFolder).mockReturnValueOnce(new Promise(resolve => { finishSelect = resolve; }));
    const opened = useWorkspaceFolderStore.getState().open();
    const selected = useWorkspaceFolderStore.getState().select();
    const loading = useWorkspaceFolderStore.getState().load();
    finishOpen({ path: '/old/runs', selected: false });
    await opened;
    expect(useWorkspaceFolderStore.getState().busy).toBe('select');
    finishSelect({ path: '/new/runs', selected: true });
    await Promise.all([selected, loading]);
    expect(useWorkspaceFolderStore.getState()).toMatchObject({ path: '/new/runs', selected: true, busy: null });
  });
});
