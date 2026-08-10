import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionMenu, type ActionMenuItem } from './ActionMenu';

const menu = () => document.querySelector<HTMLElement>('[role="menu"]');
const chevron = (host: HTMLElement) => host.querySelector<HTMLButtonElement>('[aria-label="More options"]')!;
const main = (host: HTMLElement) => host.querySelector<HTMLButtonElement>('.action-menu-primary')!;
const itemButtons = () => Array.from(document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'));
const pointer = (element: Element, type: 'pointerover' | 'pointerout' | 'pointerdown', pointerType = 'mouse') => {
  element.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerType }));
};
const key = (element: Element, value: string, altKey = false) => {
  element.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: value, altKey }));
};

function makeItems(actions = [vi.fn(), vi.fn(), vi.fn()]): ActionMenuItem[] {
  return [
    { id: 'first', label: 'First', group: 'Results', onSelect: actions[0] },
    { id: 'disabled', label: 'Unavailable', group: 'Results', disabled: true, disabledReason: 'No saved design', onSelect: actions[1] },
    { id: 'last', label: 'Last', group: 'Geometry', trailing: '.step', onSelect: actions[2] },
  ];
}

describe('ActionMenu', () => {
  let overflowAncestor: HTMLDivElement;
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    overflowAncestor = document.createElement('div');
    overflowAncestor.style.overflow = 'hidden';
    host = document.createElement('div');
    overflowAncestor.append(host);
    document.body.append(overflowAncestor);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    overflowAncestor.remove();
    document.querySelectorAll('.action-menu-popover').forEach((element) => element.remove());
    vi.useRealTimers();
  });

  const render = (props: Partial<React.ComponentProps<typeof ActionMenu>> = {}) => act(() => root.render(
    <ActionMenu items={makeItems()} menuLabel="Export run" triggerLabel="Export" {...props}/>,
  ));

  it('opens only after hover intent and closes only after the leave delay', () => {
    render();
    const trigger = host.querySelector('.action-menu')!;
    act(() => pointer(trigger, 'pointerover'));
    act(() => vi.advanceTimersByTime(179));
    expect(menu()).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(menu()).not.toBeNull();

    act(() => pointer(trigger, 'pointerout'));
    act(() => vi.advanceTimersByTime(299));
    expect(menu()).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(menu()).toBeNull();
  });

  it('ignores emulated touch hover', () => {
    render();
    act(() => { pointer(host.querySelector('.action-menu')!, 'pointerover', 'touch'); vi.advanceTimersByTime(500); });
    expect(menu()).toBeNull();
  });

  it('pins immediately on chevron click and closes on the second click', () => {
    render();
    act(() => chevron(host).click());
    expect(menu()).not.toBeNull();
    expect(chevron(host).getAttribute('aria-expanded')).toBe('true');
    act(() => chevron(host).click());
    expect(menu()).toBeNull();
  });

  it('runs the primary action, while a plain menu button opens instead', () => {
    const primary = vi.fn();
    render({ onPrimary: primary });
    act(() => main(host).click());
    expect(primary).toHaveBeenCalledOnce();
    expect(menu()).toBeNull();

    render({ onPrimary: undefined });
    act(() => main(host).click());
    expect(menu()).not.toBeNull();
    expect(document.activeElement).toBe(itemButtons()[0]);
  });

  it('Escape closes and restores focus to the chevron', () => {
    render();
    act(() => { chevron(host).focus(); key(chevron(host), 'ArrowDown'); });
    expect(document.activeElement).toBe(itemButtons()[0]);
    act(() => key(itemButtons()[0], 'Escape'));
    act(() => vi.advanceTimersByTime(20));
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(chevron(host));
  });

  it('wraps arrow navigation, skips disabled items, and supports Home and End', () => {
    render();
    act(() => key(chevron(host), 'ArrowDown', true));
    const buttons = itemButtons();
    expect(document.activeElement).toBe(buttons[0]);
    act(() => key(buttons[0], 'ArrowUp'));
    expect(document.activeElement).toBe(buttons[2]);
    act(() => key(buttons[2], 'ArrowDown'));
    expect(document.activeElement).toBe(buttons[0]);
    act(() => key(buttons[0], 'End'));
    expect(document.activeElement).toBe(buttons[2]);
    act(() => key(buttons[2], 'Home'));
    expect(document.activeElement).toBe(buttons[0]);
  });

  it('closes on outside pointerdown', () => {
    render();
    act(() => chevron(host).click());
    expect(menu()).not.toBeNull();
    act(() => pointer(document.body, 'pointerdown'));
    expect(menu()).toBeNull();
  });

  it('keeps a disabled item focusable with its reason but suppresses its action', () => {
    const actions = [vi.fn(), vi.fn(), vi.fn()];
    render({ items: makeItems(actions) });
    act(() => chevron(host).click());
    const unavailable = itemButtons()[1];
    act(() => unavailable.focus());
    expect(document.activeElement).toBe(unavailable);
    expect(unavailable.getAttribute('aria-disabled')).toBe('true');
    const reason = document.getElementById(unavailable.getAttribute('aria-describedby')!);
    expect(reason?.textContent).toBe('No saved design');
    act(() => unavailable.click());
    expect(actions[1]).not.toHaveBeenCalled();
    expect(menu()).not.toBeNull();
  });

  it('exposes item busy state', () => {
    const items = makeItems();
    items[0] = { ...items[0], busy: true, busyLabel: 'Preparing…' };
    render({ items });
    act(() => chevron(host).click());
    expect(itemButtons()[0].getAttribute('aria-busy')).toBe('true');
    expect(itemButtons()[0].textContent).toContain('Preparing…');
  });

  it('portals the menu outside an overflow-clipping ancestor', () => {
    render();
    act(() => chevron(host).click());
    expect(menu()?.parentElement).toBe(document.body);
    expect(overflowAncestor.contains(menu())).toBe(false);
  });
});
