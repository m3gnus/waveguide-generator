import { act } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi } from '../api/sourceEditor';
import { realSessionReady } from '../sketch/testSupport';
import { namespaceStorage } from '../stores/durableSettings';
import { Editor } from './SourceContourEditor';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validate: vi.fn(), presets: vi.fn() } }));
beforeAll(realSessionReady);

// jsdom has no layout engine or media-query evaluation. Apply the actual CSS rules
// for the mocked viewport, then model only the row heights and scroll offsets.
function viewportStyles(width: number, height: number) {
  vi.stubGlobal('innerWidth', width); vi.stubGlobal('innerHeight', height);
  const style = document.createElement('style');
  style.textContent = new TextDecoder().decode(readFileSync('src/design/sourceContourEditor.css'));
  document.head.append(style);
  const activeRules = (rules: CSSRuleList): string => [...rules].map(rule => {
    if (rule instanceof CSSMediaRule) {
      const matches = [...rule.conditionText.matchAll(/\(max-(width|height): (\d+)px\)/g)];
      if (!matches.length) throw new Error(`Unmodelled media query: ${rule.conditionText}`);
      return matches.every(([, axis, limit]) => (axis === 'width' ? width : height) <= Number(limit)) ? activeRules(rule.cssRules) : '';
    }
    return rule.cssText;
  }).join('\n');
  style.textContent = activeRules(style.sheet!.cssRules);
  return style;
}

describe('source dialog layout at constrained viewport sizes', () => {
  let host: HTMLDivElement, root: Root, style: HTMLStyleElement;
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    namespaceStorage('sourceContourDraft').removeItem('');
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
    vi.mocked(sourceEditorApi.validate).mockImplementation(async document => ({ document, geometry_sha256: 'g', excitation_sha256: 'e', meridian: {} }));
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount()); host.remove(); style?.remove();
    vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  });
  const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(200); }); };

  it('gives the properties panel a content-sized row and scrolls the entire body at 800 × 600', async () => {
    style = viewportStyles(800, 600);
    await act(async () => root.render(<Editor onClose={() => {}}/>)); await settle();
    const body = host.querySelector<HTMLElement>('fieldset')!, columns = body.querySelector<HTMLElement>('.source-editor-columns')!;
    const workspace = body.querySelector<HTMLElement>('.source-workspace')!, panel = body.querySelector<HTMLElement>('.source-properties')!;
    expect(getComputedStyle(body).overflow).toBe('auto');
    expect(getComputedStyle(columns).height).toBe('auto');
    expect(getComputedStyle(columns).gridTemplateColumns).toBe('1fr');
    expect(getComputedStyle(columns).gridTemplateRows).toBe('minmax(220px, 40vh) auto');
    expect(getComputedStyle(workspace).minHeight).toBe('220px');
    expect(getComputedStyle(panel).overflow).toBe('visible');
    // At 600px the header/footer leave about 220px for the body. An auto
    // second row retains the panel's intrinsic height instead of sharing zero
    // remaining space after the old 360px canvas minimum.
    const bodyHeight = 220, intrinsicPanelHeight = 480;
    const rows = getComputedStyle(columns).gridTemplateRows;
    const canvasHeight = Math.max(parseFloat(getComputedStyle(workspace).minHeight), innerHeight * .4);
    const panelHeight = rows.endsWith(' auto') ? intrinsicPanelHeight : Math.max(0, (bodyHeight - canvasHeight) / 2);
    vi.spyOn(panel, 'getBoundingClientRect').mockImplementation(() => ({ top: canvasHeight - body.scrollTop, height: panelHeight, bottom: canvasHeight + panelHeight - body.scrollTop } as DOMRect));
    Object.defineProperties(body, { clientHeight: { value: bodyHeight }, scrollHeight: { value: canvasHeight + panelHeight } });
    expect(panel.getBoundingClientRect().height).toBeGreaterThan(0);
    expect(body.scrollHeight).toBeGreaterThan(body.clientHeight);
    body.scrollTop = body.scrollHeight - body.clientHeight;
    expect(panel.getBoundingClientRect().bottom).toBe(body.clientHeight);
  });
});
