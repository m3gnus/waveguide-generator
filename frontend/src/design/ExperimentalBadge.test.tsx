import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi } from '../api/sourceEditor';
import { EXPERIMENTAL_NOTE } from './ExperimentalBadge';
import { AssemblyEditor, SourceAssemblyLauncher } from './SourceAssemblyEditor';
import { Editor, SourceContourEditor } from './SourceContourEditor';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validateAssembly: vi.fn(), validate: vi.fn(), presets: vi.fn(), hornProfile: vi.fn(), ingestAssembly: vi.fn(), exportAssembly: vi.fn() } }));
vi.mock('./nativeAssemblyPreparation', () => ({ prepareNativeAssembly: vi.fn() }));

describe('experimental native assembly labels', () => {
  let host: HTMLDivElement; let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.mocked(sourceEditorApi.validateAssembly).mockReturnValue(new Promise(() => {}));
    vi.mocked(sourceEditorApi.validate).mockReturnValue(new Promise(() => {}));
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
  const badges = () => [...document.querySelectorAll<HTMLElement>('.experimental-badge')];

  it('labels both launchers, and keeps their buttons enabled', async () => {
    await act(async () => root.render(<><SourceContourEditor/><SourceAssemblyLauncher/></>));
    expect(badges()).toHaveLength(2);
    badges().forEach((badge) => { expect(badge.textContent).toBe('Experimental'); expect(badge.title).toBe(EXPERIMENTAL_NOTE); });
    expect([...host.querySelectorAll('button')].every((b) => !b.disabled)).toBe(true);
  });
  it('labels the contour editor and its baffle export section', async () => {
    await act(async () => root.render(<Editor onClose={() => {}}/>));
    expect(badges().length).toBeGreaterThanOrEqual(2);
    expect(host.querySelector('h2')?.textContent).toContain('Experimental');
  });
  it('labels the assembly dialog and the phase plug section', async () => {
    await act(async () => root.render(<AssemblyEditor onClose={() => {}}/>));
    const headings = [...host.querySelectorAll('h2, h3')].filter((h) => h.querySelector('.experimental-badge')).map((h) => h.textContent);
    expect(headings.some((t) => t?.includes('Horn + woofer assembly'))).toBe(true);
    expect(headings.some((t) => t?.includes('Phase plug'))).toBe(true);
  });
});
