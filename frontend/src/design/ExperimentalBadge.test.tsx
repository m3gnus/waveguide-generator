import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi } from '../api/sourceEditor';
import { EXPERIMENTAL_NOTE } from './ExperimentalBadge';
import { Editor, SourceContourEditor } from './SourceContourEditor';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validate: vi.fn(), presets: vi.fn() } }));

describe('experimental source contour labels', () => {
  let host: HTMLDivElement; let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.mocked(sourceEditorApi.validate).mockReturnValue(new Promise(() => {}));
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
  const badges = () => [...document.querySelectorAll<HTMLElement>('.experimental-badge')];

  it('labels the source contour launcher and keeps its button enabled', async () => {
    await act(async () => root.render(<SourceContourEditor/>));
    expect(badges()).toHaveLength(1);
    badges().forEach((badge) => {
      expect(badge.textContent).toBe('Experimental');
      expect(badge.title).toBe(EXPERIMENTAL_NOTE);
      expect(badge.getAttribute('role')).toBe('note');
      expect(badge.getAttribute('aria-label')).toBe(EXPERIMENTAL_NOTE);
    });
    expect([...host.querySelectorAll('button')].every((b) => !b.disabled)).toBe(true);
  });
  it('labels the contour editor dialog', async () => {
    await act(async () => root.render(<Editor onClose={() => {}}/>));
    expect(badges()).toHaveLength(1);
    expect(host.querySelector('h2')?.textContent).toBe('Source contour');
    expect(host.querySelector('header')?.textContent).toContain(EXPERIMENTAL_NOTE);
  });
});
