import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NativeWindowStore } from './nativeWindow';
import { NO_DRAG_SELECTOR, WindowControls } from './WindowControls';
import { readFileSync } from 'node:fs';

/**
 * A store wired to a fake pywebview host.
 *
 * The component is given the store rather than reaching for the module
 * singleton, so a test can state exactly which host it is describing instead of
 * inheriting whatever machine runs it.
 */
function storeFor(options: {
  backend?: string;
  customFrame?: boolean;
  maximized?: boolean;
  api?: Record<string, (...args: never[]) => Promise<unknown>>;
}) {
  const host = {
    pywebview: {
      platform: options.backend ?? 'edgechromium',
      api: {
        window_state: async () => ({
          customFrame: options.customFrame ?? true,
          maximized: options.maximized ?? false,
        }),
        ...options.api,
      },
    },
    navigator: { userAgent: '' },
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  } as unknown as Window & typeof globalThis;
  return new NativeWindowStore({ host });
}

let container: HTMLElement;
let root: Root;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete document.documentElement.dataset.nativeFrame;
  delete document.documentElement.dataset.nativeMaximized;
  delete document.documentElement.dataset.nativeBlurred;
});

async function render(store: NativeWindowStore, side: 'leading' | 'trailing') {
  await act(async () => {
    root.render(<WindowControls side={side} store={store}/>);
  });
  await act(async () => { await store.refresh(); });
}

describe('WindowControls', () => {
  // The dev browser still has real browser chrome, so drawing window buttons
  // there would be a second, non-functional set beside the tab's own.
  it('renders nothing in a browser', async () => {
    const host = {
      navigator: { userAgent: '' },
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    } as unknown as Window & typeof globalThis;
    await render(new NativeWindowStore({ host }), 'trailing');
    expect(container.querySelector('.window-controls')).toBeNull();
    expect(document.documentElement.dataset.nativeFrame).toBeUndefined();
  });

  // A launcher that kept the OS caption already has working buttons.
  it('renders nothing when the launcher kept the OS title bar', async () => {
    await render(storeFor({ customFrame: false }), 'trailing');
    expect(container.querySelector('.window-controls')).toBeNull();
  });

  it('puts the controls at the trailing edge on Windows', async () => {
    const store = storeFor({ backend: 'edgechromium' });
    await render(store, 'trailing');
    const group = container.querySelector('.window-controls');
    expect(group).not.toBeNull();
    expect(group?.classList.contains('window-controls-windows')).toBe(true);
    expect([...container.querySelectorAll('button')].map((b) => b.getAttribute('aria-label')))
      .toEqual(['Minimize', 'Maximize', 'Close']);
    expect(document.documentElement.dataset.nativeFrame).toBe('windows');
  });

  it('leaves the leading edge empty on Windows', async () => {
    await render(storeFor({ backend: 'edgechromium' }), 'leading');
    expect(container.querySelector('.window-controls')).toBeNull();
  });

  // Traffic lights sit at the leading edge and close comes first -- both are
  // muscle memory on macOS, and the wrong order is a mis-click, not a nit.
  it('puts the controls at the leading edge on macOS, close first', async () => {
    await render(storeFor({ backend: 'cocoa' }), 'leading');
    expect(container.querySelector('.window-controls-macos')).not.toBeNull();
    expect([...container.querySelectorAll('button')].map((b) => b.getAttribute('aria-label')))
      .toEqual(['Close', 'Minimize', 'Enter full screen']);
    expect(document.documentElement.dataset.nativeFrame).toBe('macos');
  });

  // Linux gets the status window rather than a native frame; if a build ever
  // reported a custom frame there, guessing a side would be worse than nothing.
  it('renders nothing on a platform with no convention to follow', async () => {
    await render(storeFor({ backend: 'gtkwebkit2' }), 'leading');
    expect(container.querySelector('.window-controls')).toBeNull();
    await render(storeFor({ backend: 'gtkwebkit2' }), 'trailing');
    expect(container.querySelector('.window-controls')).toBeNull();
  });

  it('names the maximize button for what it will do', async () => {
    await render(storeFor({ maximized: true }), 'trailing');
    const maximize = container.querySelector('.window-controls-maximize');
    expect(maximize?.getAttribute('aria-label')).toBe('Restore down');
    expect(document.documentElement.dataset.nativeMaximized).toBe('true');
  });

  it('drives each button through the bridge', async () => {
    const window_minimize = vi.fn(async () => undefined);
    const window_toggle_maximize = vi.fn(async () => ({ maximized: true }));
    const window_close = vi.fn(async () => undefined);
    const store = storeFor({ api: { window_minimize, window_toggle_maximize, window_close } });
    await render(store, 'trailing');

    for (const label of ['Minimize', 'Maximize', 'Close']) {
      const button = container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
      await act(async () => { button?.click(); });
    }
    expect(window_minimize).toHaveBeenCalledOnce();
    expect(window_toggle_maximize).toHaveBeenCalledOnce();
    expect(window_close).toHaveBeenCalledOnce();
  });

  // The attribute drives the drag region, so a stale one left on the document
  // would make the whole top bar unclickable in a browser.
  it('clears the document attributes when it unmounts', async () => {
    await render(storeFor({}), 'trailing');
    expect(document.documentElement.dataset.nativeFrame).toBe('windows');
    await act(async () => root.render(<></>));
    expect(document.documentElement.dataset.nativeFrame).toBeUndefined();
  });
});

/**
 * A top bar to press on, with one control in it and one piece of plain text.
 *
 * The component listens on the document rather than on an element it renders,
 * because the bar it makes draggable is its parent. Tests therefore have to
 * build the bar themselves.
 */
function topbarWith(): { bar: HTMLElement; label: HTMLElement; button: HTMLElement } {
  const bar = document.createElement('header');
  bar.className = 'topbar';
  const label = document.createElement('span');
  label.textContent = 'WAVEGUIDE GENERATOR';
  const button = document.createElement('button');
  bar.append(label, button);
  document.body.appendChild(bar);
  return { bar, label, button };
}

function press(target: Element, detail = 1) {
  target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, detail }));
}

describe('WindowControls on macOS', () => {
  let bar: HTMLElement;
  let label: HTMLElement;
  let button: HTMLElement;

  beforeEach(() => { ({ bar, label, button } = topbarWith()); });
  afterEach(() => bar.remove());

  // macOS puts them at the leading edge, and the close button first -- the
  // opposite of Windows on both counts.
  it('draws its controls at the leading edge, close first', async () => {
    await render(storeFor({ backend: 'cocoa' }), 'leading');
    const labels = [...container.querySelectorAll('button')].map(b => b.getAttribute('aria-label'));
    expect(labels).toEqual(['Close', 'Minimize', 'Enter full screen']);
    expect(document.documentElement.dataset.nativeFrame).toBe('macos');
  });

  // Reusing the Windows glyphs made the dots read as a Windows title bar in
  // macOS colours -- a square outline for maximize above all, which is not a
  // shape the platform draws anywhere. macOS zooms with two filled triangles.
  it('draws the platform\'s own glyphs, not the Windows ones', async () => {
    await render(storeFor({ backend: 'cocoa' }), 'leading');
    const green = container.querySelector('button[aria-label="Enter full screen"]');
    expect(green?.querySelectorAll('path[fill="currentColor"]').length).toBe(2);
    expect(green?.querySelector('rect')).toBeNull();
    const close = container.querySelector('button[aria-label="Close"] path');
    expect(close?.getAttribute('stroke-linecap')).toBe('round');
    // 12 px coordinates, because the glyphs are traced at the button's own size.
    expect(close?.closest('svg')?.getAttribute('viewBox')).toBe('0 0 12 12');
  });

  it('turns the chevrons inward once the window is full screen', async () => {
    await render(storeFor({ backend: 'cocoa', maximized: true }), 'leading');
    const green = container.querySelector('button[aria-label="Exit full screen"]');
    expect(green?.querySelectorAll('path[fill="currentColor"]').length).toBe(2);
  });

  // A plain click is full screen and Option-click is zoom, exactly as the real
  // green button behaves. Sending the same call for both would make the
  // modifier silently do nothing.
  it('passes Option through so the green button zooms instead', async () => {
    const window_toggle_maximize = vi.fn(async () => ({ maximized: false }));
    await render(storeFor({ backend: 'cocoa', api: { window_toggle_maximize } }), 'leading');
    const green = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Enter full screen"]');
    await act(async () => {
      green?.dispatchEvent(new MouseEvent('click', { bubbles: true, altKey: true }));
    });
    expect(window_toggle_maximize).toHaveBeenCalledWith(true);
    await act(async () => {
      green?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(window_toggle_maximize).toHaveBeenLastCalledWith(false);
  });

  it('draws nothing at the trailing edge', async () => {
    await render(storeFor({ backend: 'cocoa' }), 'trailing');
    expect(container.querySelector('.window-controls')).toBeNull();
  });

  // WebKit has no `-webkit-app-region`, so this call is the only way the window
  // can be moved at all.
  it('starts a native drag from a press on the bar', async () => {
    const window_begin_drag = vi.fn(async () => undefined);
    await render(storeFor({ backend: 'cocoa', api: { window_begin_drag } }), 'leading');
    press(label);
    expect(window_begin_drag).toHaveBeenCalledOnce();
  });

  it('leaves presses on a control alone', async () => {
    const window_begin_drag = vi.fn(async () => undefined);
    await render(storeFor({ backend: 'cocoa', api: { window_begin_drag } }), 'leading');
    press(button);
    expect(window_begin_drag).not.toHaveBeenCalled();
  });

  it('leaves presses outside the bar alone', async () => {
    const window_begin_drag = vi.fn(async () => undefined);
    await render(storeFor({ backend: 'cocoa', api: { window_begin_drag } }), 'leading');
    press(document.body);
    expect(window_begin_drag).not.toHaveBeenCalled();
  });

  // The second click of a double-click must not begin a second zero-distance
  // drag, or the window would never zoom.
  it('answers the second click as a double-click', async () => {
    const window_begin_drag = vi.fn(async () => undefined);
    const window_double_click = vi.fn(async () => ({ action: 'zoom' }));
    await render(
      storeFor({ backend: 'cocoa', api: { window_begin_drag, window_double_click } }), 'leading');
    press(label, 1);
    press(label, 2);
    expect(window_begin_drag).toHaveBeenCalledOnce();
    expect(window_double_click).toHaveBeenCalledOnce();
  });

  // Windows is given its drags by the runtime; asking for them there as well
  // would move the window twice for one gesture.
  it('asks for no drag on Windows', async () => {
    const window_begin_drag = vi.fn(async () => undefined);
    await render(storeFor({ api: { window_begin_drag } }), 'trailing');
    press(label);
    expect(window_begin_drag).not.toHaveBeenCalled();
  });

  it('stops listening when it unmounts', async () => {
    const window_begin_drag = vi.fn(async () => undefined);
    await render(storeFor({ backend: 'cocoa', api: { window_begin_drag } }), 'leading');
    await act(async () => root.render(<></>));
    press(label);
    expect(window_begin_drag).not.toHaveBeenCalled();
  });
});

/**
 * The JavaScript and the stylesheet must exempt the same controls.
 *
 * They cannot share a value -- one is CSS -- and they fail in opposite,
 * invisible ways: a control missing from the stylesheet is unclickable on
 * Windows, and one missing from the selector is undraggable on macOS.
 */
describe('the drag exemptions', () => {
  it('match the stylesheet', () => {
    const css = new TextDecoder().decode(readFileSync('src/styles/windowControls.css'));
    const block = /\.topbar :is\(([^)]+)\)/.exec(css);
    expect(block).not.toBeNull();
    const inCss = (block?.[1] ?? '').split(',').map(s => s.trim()).filter(Boolean).sort();
    const inTs = NO_DRAG_SELECTOR.split(',').map(s => s.trim().replace(/'/g, "'")).sort();
    expect(inCss).toEqual(inTs);
  });
});

/**
 * The controls have to survive a top bar too crowded to fit in the window.
 *
 * `.topbar` is a flex row and the trailing controls are its last item, so
 * every pixel the row overflowed by pushed them further past the window's
 * right edge -- and they are the only pointer route to minimize, maximize or
 * close a window whose caption has been removed. Measured against the shipped
 * 0.3.0 build before the wrapper existed: at the launcher's own 1100 px
 * minimum window width the bar wanted 1119 px and 19 px of the close button
 * hung outside; at 733 px -- that same minimum window on a 150% display --
 * the bar wanted 971 px and all three buttons sat wholly outside the window.
 *
 * macOS never showed it because its controls are the *first* item, which is
 * why this reads as a Windows-only bug rather than as a layout bug.
 */
describe('an overflowing top bar gives before the window controls do', () => {
  it('mounts both control groups outside the shrinkable wrapper', () => {
    const tsx = new TextDecoder().decode(readFileSync('src/shell/TopBar.tsx'));
    const open = tsx.indexOf('<div className="topbar-main">');
    expect(open).toBeGreaterThan(-1);
    // The wrapper's own closing tag is the one at the header's indentation;
    // everything nested inside it closes further in.
    const close = tsx.indexOf('\n    </div>', open);
    expect(close).toBeGreaterThan(open);
    // Inside the wrapper they would be carried out of the window by the very
    // content the wrapper exists to absorb.
    expect(tsx.slice(open, close)).not.toContain('<WindowControls');
    expect(tsx).toContain('<WindowControls side="leading"/>');
    expect(tsx).toContain('<WindowControls side="trailing"/>');
  });

  it('lets the wrapper, and only the wrapper, take the shrink', () => {
    const css = new TextDecoder().decode(readFileSync('src/styles/app.css'));
    const rule = /\.topbar-main \{([^}]*)\}/.exec(css);
    expect(rule).not.toBeNull();
    const declarations = rule?.[1] ?? '';
    // `min-width: 0` is the whole of the fix. Without it the wrapper's
    // min-content width is its children's, the row overflows exactly as
    // before, and the controls are again what leaves the window.
    expect(declarations).toMatch(/min-width:\s*0/);
    expect(declarations).toMatch(/flex:\s*1\s+1\s+auto/);
  });
});

/**
 * Reserving the space is not the same as owning the pixels.
 *
 * The `.topbar-main` wrapper above keeps the controls inside the window by
 * letting the wrapper shrink past its own content -- and the wrapper's
 * children, which do not shrink, then overflow it and run straight across the
 * controls. Measured in Chromium at 1101 px with the Windows custom frame
 * emulated, before the rules below existed: 112 px of overflow, the minimize
 * dash drawn between the report and settings icons, the maximize outline drawn
 * inside the reset-layout icon. `elementFromPoint` returned the *window
 * control* at every one of those centres, so the overlap never cost a click --
 * it redirected one. A pointer aimed at the visible reset-layout icon pressed
 * Close.
 *
 * Two rules answer that, and neither is enough alone: the strip is on its own
 * layer so nothing can be drawn into it, and the bar drops controls before it
 * reaches the strip so nothing invites a press that lands somewhere else.
 */
describe('the window controls own their strip', () => {
  const read = (path: string) => new TextDecoder().decode(readFileSync(path));

  /** The declarations of the rule for exactly this class selector. */
  function ruleFor(source: string, selector: string): string {
    // `<selector> {` rather than a pattern, so `.window-controls` cannot match
    // the start of `.window-controls-windows`.
    const marker = `${selector} {`;
    const start = source.indexOf(marker);
    expect(start, `no rule for ${selector}`).toBeGreaterThan(-1);
    const end = source.indexOf('}', start);
    expect(end).toBeGreaterThan(start);
    return source.slice(start + marker.length, end);
  }

  it('puts the controls on their own layer', () => {
    const declarations = ruleFor(read('src/styles/windowControls.css'), '.window-controls');
    // Without a layer the strip is only as safe as document order, and an
    // overflowing child carrying a z-index of its own would take it back.
    expect(declarations).toMatch(/position:\s*relative/);
    expect(declarations).toMatch(/z-index:\s*1\b/);
  });

  it("paints the strip in the top bar's own colour", () => {
    // Being on top is not enough while the buttons themselves are
    // transparent: an overflowing icon still showed through the glyph painted
    // over it. The two backgrounds have to be the same token or the fix reads
    // as a patch of the wrong colour in the corner -- and they live in
    // different stylesheets, so nothing but this holds them together.
    const controls = ruleFor(read('src/styles/windowControls.css'), '.window-controls-windows');
    expect(controls).toMatch(/background:\s*var\(--surface-panel\)/);
    expect(read('src/styles/app.css')).toMatch(
      /\.topbar \{[^}]*background:\s*var\(--surface-panel\)/,
    );
  });

  it('reaches the window corner in every band', () => {
    // A caption button the pointer can be thrown at is a Fitts's-law target;
    // one that stops four pixels short of the corner is not. The bar's inline
    // padding changes with the band, so the margin that cancels it has to
    // change with it -- which is why both read one variable rather than each
    // repeating 8px and drifting apart, as they had.
    const controls = ruleFor(read('src/styles/windowControls.css'), '.window-controls-windows');
    expect(controls).toMatch(/margin:\s*0\s+calc\(-1\s*\*\s*var\(--topbar-pad-inline[^)]*\)\)\s+0\s+4px/);
    expect(read('src/styles/app.css')).toMatch(/padding-inline:\s*var\(--topbar-pad-inline\)/);
  });
});

/**
 * What the bar gives up, at the widths where it has to give something up.
 *
 * The launcher's own minimum window is 1100 px, which is 733 CSS px on a 150%
 * display, and the bar's content does not fit there however it is compacted.
 * Something has to go; the rule is that it may only be something the command
 * palette can still reach, so the cost is a keystroke rather than the feature.
 */
describe('an overcrowded top bar drops controls, not the window buttons', () => {
  const read = (path: string) => new TextDecoder().decode(readFileSync(path));

  /**
   * Every `<tag …>` opening tag in a stretch of JSX, attributes included.
   *
   * Scanned rather than matched: an `onClick={() => …}` attribute contains a
   * `>` of its own, so the first `>` after `<button` is not the end of the
   * tag, and a pattern that assumes it is silently reads half the attributes.
   */
  function openTags(source: string, tag: string): string[] {
    const found: string[] = [];
    for (let at = source.indexOf(`<${tag}`); at > -1; at = source.indexOf(`<${tag}`, at + 1)) {
      let depth = 0;
      for (let i = at; i < source.length; i += 1) {
        if (source[i] === '{') depth += 1;
        else if (source[i] === '}') depth -= 1;
        else if (source[i] === '>' && depth === 0) {
          found.push(source.slice(at, i + 1));
          break;
        }
      }
    }
    return found;
  }

  /** The body of one `@media (max-width: <width>px)` block. */
  function band(source: string, width: number): string {
    const start = source.indexOf(`@media (max-width: ${width}px) {`);
    expect(start, `no max-width: ${width}px band`).toBeGreaterThan(-1);
    let depth = 0;
    for (let i = source.indexOf('{', start); i < source.length; i += 1) {
      if (source[i] === '{') depth += 1;
      else if (source[i] === '}') {
        depth -= 1;
        if (depth === 0) return source.slice(start, i);
      }
    }
    throw new Error(`unterminated max-width: ${width}px band`);
  }

  it('drops the duplicated utilities before the bar can overflow', () => {
    // Measured: no overflow at 1280 px, 14 px of it at 1200 px, 112 px by
    // 1101 px -- all of it above the 1100 px compact breakpoint that was
    // supposed to rescue the bar. That gap is what let the RC ship a top bar
    // overlapping its own window controls at an ordinary window size.
    expect(band(read('src/styles/app.css'), 1280)).toMatch(
      /\.topbar-utility \{[^}]*display:\s*none/,
    );
  });

  it('drops the history pair only at the narrowest window the launcher allows', () => {
    const css = read('src/styles/app.css');
    expect(band(css, 800)).toMatch(/\.topbar-history \{[^}]*display:\s*none/);
    // Still there in the band above: undo and redo are the last thing to go,
    // not part of the first cut.
    expect(band(css, 1280)).not.toContain('.topbar-history');
  });

  it('leaves every dropped control reachable from the command palette', () => {
    const source = read('src/shell/TopBar.tsx');
    const paletteLabels = new Set(
      [...source.matchAll(/\blabel: '([^']*)'/g)].map((match) => match[1]),
    );
    expect(paletteLabels.size).toBeGreaterThan(4);

    // Every button inside something the narrow bar hides has to name a command
    // the palette also offers. An icon added to these groups without a palette
    // entry would simply vanish below 1280 px with no way back to it.
    const dropped = [...source.matchAll(
      /<(\w+)[^>]*className="[^"]*\btopbar-(?:utility|history)\b[^"]*"/g,
    )];
    expect(dropped.length).toBeGreaterThan(3);
    for (const match of dropped) {
      const tag = match[1];
      const open = match.index ?? 0;
      const close = source.indexOf(`</${tag}>`, open);
      expect(close).toBeGreaterThan(open);
      const extent = source.slice(open, close);
      // The first closing tag is this element's own only while nothing of the
      // same kind is nested inside it. Fail loudly if that stops being true,
      // rather than quietly checking a prefix of the group.
      expect(extent.indexOf(`<${tag}`, 1), `nested <${tag}> in a droppable group`).toBe(-1);
      const buttons = openTags(extent, 'button');
      expect(buttons.length).toBeGreaterThan(0);
      for (const button of buttons) {
        const name = /aria-label="([^"]+)"/.exec(button)?.[1]
          ?? /title="([^"]+)"/.exec(button)?.[1];
        expect(name, `a droppable control with no name: ${button}`).toBeTruthy();
        expect([...paletteLabels], `${name} is dropped below 1280px with no palette route`)
          .toContain(name as string);
      }
    }
  });
});
