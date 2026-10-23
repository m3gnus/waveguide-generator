import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RootFolderControls } from './RootFolderControls';
import { getFolderRoot, openFolderRoot, selectFolderRoot, useRootFolder, type FolderRoot } from '../api/folders';

vi.mock('../api/folders', () => ({ getFolderRoot: vi.fn(), openFolderRoot: vi.fn(), selectFolderRoot: vi.fn(), useRootFolder: vi.fn() }));
vi.mock('../stores/workspaceFolder', () => ({ resetWorkspaceFolderStore: vi.fn(), useWorkspaceFolderStore: { getState: () => ({ load: vi.fn() }) } }));
const folder: FolderRoot = { path: '/current/root', selected: true, runs: '/earlier/runs', cadlink: '/current/root/cadlink', overrides: { runs: '/earlier/runs' } };

describe('WG folder', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.resetAllMocks();
    vi.mocked(getFolderRoot).mockResolvedValue(folder);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); });
  const button = (text: string) => [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === text)!;

  it('shows earlier overrides and keeps them after choosing a root', async () => {
    vi.mocked(selectFolderRoot).mockResolvedValue({ ...folder, path: '/new/root', cadlink: '/new/root/cadlink' });
    await act(async () => root.render(<RootFolderControls/>));
    expect(host.textContent).toContain('Runs folderSeparate folder from an earlier setup: /earlier/runs');
    expect(host.querySelectorAll('dd button')).toHaveLength(1);
    await act(async () => button('Choose WG folder…').click());
    // No proposed/default path is sent. The server starts at its current root.
    expect(selectFolderRoot).toHaveBeenCalledWith(undefined);
    expect(host.textContent).toContain('/new/root/cadlink');
    expect(host.textContent).toContain('/earlier/runs');
  });

  it('opens the root and displays a picker refusal', async () => {
    vi.mocked(openFolderRoot).mockResolvedValue(folder);
    vi.mocked(selectFolderRoot).mockRejectedValue(new Error('Choose a shorter path for Windows run files.'));
    await act(async () => root.render(<RootFolderControls/>));
    await act(async () => button('Open WG folder').click());
    expect(openFolderRoot).toHaveBeenCalledOnce();
    await act(async () => button('Choose WG folder…').click());
    expect(host.textContent).toContain('Choose a shorter path');
    expect(host.textContent).toContain('/current/root');
  });

  it('refuses editing an unsupported future schema', async () => {
    vi.mocked(getFolderRoot).mockResolvedValue({ ...folder, readOnly: true });
    await act(async () => root.render(<RootFolderControls/>));
    expect(button('Choose WG folder…').disabled).toBe(true);
    expect(button('Use this path').disabled).toBe(true);
  });

  it.each(['folders.json', 'cadlink_settings.json', 'workspace_settings.json'])('shows the %s recovery note and enables folder actions', async file => {
    const notice = `${file} is corrupt. Choosing a WG folder will replace it.`;
    vi.mocked(getFolderRoot).mockResolvedValue({ ...folder, readOnly: false, notice });
    await act(async () => root.render(<RootFolderControls/>));
    expect(host.textContent).toContain(notice);
    expect(button('Choose WG folder…').disabled).toBe(false);
    expect(button('Use the WG folder instead').disabled).toBe(false);
    const input = host.querySelector('input')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '/replacement');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(button('Use this path').disabled).toBe(false);
    vi.mocked(selectFolderRoot).mockResolvedValue({ ...folder, notice: undefined });
    await act(async () => button('Use this path').click());
    expect(selectFolderRoot).toHaveBeenCalledWith('/replacement');
    expect(host.textContent).not.toContain(notice);
  });

  it('allows creating a missing WGLink child when settings are corrupt', async () => {
    vi.mocked(getFolderRoot).mockResolvedValue({ ...folder, cadlinkExists: false, readOnly: false,
      notice: 'cadlink_settings.json is corrupt. Choosing a WG folder will replace it.' });
    await act(async () => root.render(<RootFolderControls cadlinkOnly/>));
    expect(button('Create it').disabled).toBe(false);
  });

  it('clears only after confirmation naming the new destination', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    vi.mocked(useRootFolder).mockResolvedValue({ ...folder, overrides: {}, runs: '/current/root/runs' });
    await act(async () => root.render(<RootFolderControls/>));
    await act(async () => button('Use the WG folder instead').click());
    expect(useRootFolder).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledWith('Use the WG folder instead? No files will move. New Runs files will go to /current/root/runs.');
    confirm.mockReturnValue(true);
    await act(async () => button('Use the WG folder instead').click());
    expect(useRootFolder).toHaveBeenCalledWith('runs', '/current/root/runs', '/earlier/runs');
    expect(host.textContent).toContain('/current/root/runs');
    expect(host.textContent).not.toContain('/earlier/runs');
    confirm.mockRestore();
  });

  it('creates a missing root child without a picker', async () => {
    vi.mocked(getFolderRoot).mockResolvedValue({ ...folder, cadlinkExists: false });
    vi.mocked(useRootFolder).mockResolvedValue({ ...folder, cadlinkExists: true });
    await act(async () => root.render(<RootFolderControls cadlinkOnly/>));
    expect(host.textContent).toContain('WGLink folder not found: /current/root/cadlink');
    expect(button('Open WGLink folder').disabled).toBe(true);
    await act(async () => button('Create it').click());
    expect(useRootFolder).toHaveBeenCalledWith('cadlink', '/current/root/cadlink', '/current/root/cadlink');
    expect(selectFolderRoot).not.toHaveBeenCalled();
    expect(button('Open WGLink folder').disabled).toBe(false);
  });

  it('offers explicit override removal for a missing earlier folder', async () => {
    vi.mocked(getFolderRoot).mockResolvedValue({ ...folder, cadlink: '/old/exchange', cadlinkExists: false, overrides: { cadlink: '/old/exchange' } });
    await act(async () => root.render(<RootFolderControls cadlinkOnly/>));
    expect(host.textContent).toContain('WGLink folder not found: /old/exchange');
    expect(button('Create it')).toBeUndefined();
    expect(button('Use the WG folder instead')).toBeDefined();
    expect(button('Choose WG folder…')).toBeDefined();
  });

  it('shows the child-folder warning beside the saved WG folder', async () => {
    vi.mocked(getFolderRoot).mockResolvedValue({ ...folder, rootNote: "This folder is a WGLink folder. Choose the folder that should contain WG's folders, for example its parent." });
    await act(async () => root.render(<RootFolderControls/>));
    expect(host.textContent).toContain('This folder is a WGLink folder.');
    expect(button('Choose WG folder…').disabled).toBe(false);
  });

});
