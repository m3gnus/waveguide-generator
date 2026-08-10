import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HelpTipRow } from './HelpTip';
import { NumberField } from './NumberField';

/**
 * jsdom reports every rect as zero, so anything that depends on real geometry
 * stubs the two rects the placement reads: the anchor's and the bubble's.
 */
function stubRects(anchorTop: number, anchorHeight: number, tipHeight: number) {
  const original = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function stub(this: Element) {
    const isTip = this.classList.contains('help-tip');
    const top = isTip ? 0 : anchorTop;
    const height = isTip ? tipHeight : anchorHeight;
    return { x: 20, y: top, top, left: 20, right: 280, bottom: top + height, width: 260, height, toJSON: () => ({}) } as DOMRect;
  };
  return () => { Element.prototype.getBoundingClientRect = original; };
}

const tip = () => document.querySelector('.help-tip');
const hover = (element: Element) => element.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType: 'mouse' }));

describe('parameter hover help', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.useRealTimers();
  });

  it('opens after the hover delay and closes when the pointer leaves', () => {
    act(() => root.render(<HelpTipRow className="select-row" text="Shape the mouth morphs toward.">rows</HelpTipRow>));
    const row = host.querySelector('.select-row')!;

    act(() => { hover(row); });
    expect(tip(), 'no bubble before the delay elapses').toBeNull();

    act(() => { vi.advanceTimersByTime(400); });
    expect(tip()?.textContent).toContain('Shape the mouth morphs toward.');

    act(() => { row.dispatchEvent(new PointerEvent('pointerout', { bubbles: true })); });
    expect(tip()).toBeNull();
  });

  it('never opens for a parameter with no description', () => {
    act(() => root.render(<HelpTipRow className="select-row">rows</HelpTipRow>));
    act(() => { hover(host.querySelector('.select-row')!); vi.advanceTimersByTime(2_000); });
    expect(tip()).toBeNull();
  });

  // The parameter rail is a narrow overflow:auto column, so a bubble nested in
  // the row would be clipped by it. It has to land on <body> instead.
  it('renders into document.body rather than inside the scrolling rail', () => {
    act(() => root.render(<HelpTipRow className="select-row" text="Body-level.">rows</HelpTipRow>));
    act(() => { hover(host.querySelector('.select-row')!); vi.advanceTimersByTime(400); });
    expect(tip()!.parentElement).toBe(document.body);
    expect(host.contains(tip())).toBe(false);
  });

  it('places the bubble below a row near the top of the window', () => {
    const restore = stubRects(100, 16, 120);
    try {
      act(() => root.render(<HelpTipRow className="select-row" text="Below.">rows</HelpTipRow>));
      act(() => { hover(host.querySelector('.select-row')!); vi.advanceTimersByTime(400); });
      // anchor bottom 116 + 6px gap
      expect((tip() as HTMLElement).style.top).toBe('122px');
    } finally { restore(); }
  });

  it('flips the bubble above a row too close to the bottom of the window', () => {
    // 700 + 16 + 6 + 120 overflows jsdom's 768px-tall window, so it must flip.
    const restore = stubRects(700, 16, 120);
    try {
      act(() => root.render(<HelpTipRow className="select-row" text="Above.">rows</HelpTipRow>));
      act(() => { hover(host.querySelector('.select-row')!); vi.advanceTimersByTime(400); });
      // anchor top 700 - 6px gap - 120px tall
      expect((tip() as HTMLElement).style.top).toBe('574px');
    } finally { restore(); }
  });

  it('heads a number field bubble with the full name and ATH symbol, and keeps the drag hint', () => {
    act(() => root.render(<NumberField label="Throat coverage angle" symbol="a0" description="Wall angle where the profile leaves the throat." value={15.5} onCommit={vi.fn()} />));
    act(() => { hover(host.querySelector('.field-label')!); vi.advanceTimersByTime(400); });
    expect(tip()!.querySelector('b')!.textContent).toBe('Throat coverage angle (a0)');
    expect(tip()!.querySelector('p')!.textContent).toBe('Wall angle where the profile leaves the throat.');
    expect(tip()!.querySelector('small')!.textContent).toContain('Drag the label');
  });

  // The label doubles as NumberField's drag-to-scrub handle. Leaving the bubble
  // up over the value the user is dragging would hide the thing they came for.
  it('dismisses the bubble when a label drag starts, without swallowing the drag', () => {
    const commit = vi.fn();
    act(() => root.render(<NumberField label="Bending" symbol="b" description="Bends the profile." value={0.2} onCommit={commit} />));
    const label = host.querySelector('.field-label')! as HTMLElement;
    Object.defineProperty(label, 'setPointerCapture', { value: vi.fn() });
    act(() => { hover(label); vi.advanceTimersByTime(400); });
    expect(tip()).not.toBeNull();

    act(() => { label.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 10 })); });
    expect(tip()).toBeNull();
    act(() => { label.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 60 })); });
    act(() => { label.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })); });
    expect(commit, 'the drag still reached NumberField').toHaveBeenCalled();
  });

  it('falls back to a native title only when there is no description to show', () => {
    act(() => root.render(<NumberField label="Bending" symbol="b" value={0.2} onCommit={vi.fn()} />));
    expect(host.querySelector('.field-label')!.getAttribute('title')).toBe('Bending (b) — drag horizontally to adjust');

    act(() => root.render(<NumberField label="Bending" symbol="b" description="Bends the profile." value={0.2} onCommit={vi.fn()} />));
    expect(host.querySelector('.field-label')!.getAttribute('title')).toBeNull();
  });
});
