import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RootFolderControls } from './RootFolderControls';
import { getFolderRoot, openFolderRoot, selectFolderRoot, type FolderRoot } from '../api/folders';

vi.mock('../api/folders', () => ({ getFolderRoot: vi.fn(), openFolderRoot: vi.fn(), selectFolderRoot: vi.fn() }));
vi.mock('../stores/workspaceFolder', () => ({ resetWorkspaceFolderStore: vi.fn(), useWorkspaceFolderStore: { getState: () => ({ load: vi.fn() }) } }));
const folder: FolderRoot = { path: '/current/root', selected: true, runs: '/earlier/runs', cadlink: '/current/root/cadlink', overrides: { runs: '/earlier/runs' } };

describe('WG root folder', () => {
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

  it('shows preserved overrides read-only and keeps them after choosing a root', async () => {
    vi.mocked(selectFolderRoot).mockResolvedValue({ ...folder, path: '/new/root', cadlink: '/new/root/cadlink' });
    await act(async () => root.render(<RootFolderControls/>));
    expect(host.textContent).toContain('Runs folder/earlier/runs — kept from an earlier setup');
    expect(host.querySelectorAll('dd input, dd button')).toHaveLength(0);
    await act(async () => button('Choose WG root folder…').click());
    // No proposed/default path is sent. The server starts at its current root.
    expect(selectFolderRoot).toHaveBeenCalledWith(undefined);
    expect(host.textContent).toContain('/new/root/cadlink');
    expect(host.textContent).toContain('/earlier/runs');
  });

  it('opens the root and displays a picker refusal', async () => {
    vi.mocked(openFolderRoot).mockResolvedValue(folder);
    vi.mocked(selectFolderRoot).mockRejectedValue(new Error('Choose a shorter path for Windows run files.'));
    await act(async () => root.render(<RootFolderControls/>));
    await act(async () => button('Open WG root folder').click());
    expect(openFolderRoot).toHaveBeenCalledOnce();
    await act(async () => button('Choose WG root folder…').click());
    expect(host.textContent).toContain('Choose a shorter path');
    expect(host.textContent).toContain('/current/root');
  });

  it('refuses editing an unsupported future schema', async () => {
    vi.mocked(getFolderRoot).mockResolvedValue({ ...folder, readOnly: true });
    await act(async () => root.render(<RootFolderControls/>));
    expect(button('Choose WG root folder…').disabled).toBe(true);
    expect(button('Use this path').disabled).toBe(true);
  });
});
