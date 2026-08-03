import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NumberField } from './NumberField';

describe('NumberField', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  it('commits a valid edit on blur and rejects an invalid value', () => {
    const commit = vi.fn();
    act(() => root.render(<NumberField label="Coverage" value={42} min={10} max={90} precision={1} onCommit={commit}/>));
    const input = host.querySelector('input')!;
    const setInputValue = (value: string) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
    act(() => {
      input.focus();
      setInputValue('45.5');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => input.blur());
    expect(commit).toHaveBeenCalledWith(45.5);

    act(() => {
      input.focus();
      setInputValue('120');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    act(() => input.blur());
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it('rejects an empty draft instead of coercing it to zero', () => {
    const commit = vi.fn();
    act(() => root.render(<NumberField label="Margin" value={12} min={0} onCommit={commit}/>));
    const input = host.querySelector('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    act(() => input.blur());
    expect(commit).not.toHaveBeenCalled();
    expect(input.value).toBe('12.0');
  });

  it('cancels with Escape without letting blur commit the stale draft', () => {
    const commit = vi.fn();
    act(() => root.render(<NumberField label="Depth" value={42} onCommit={commit}/>));
    const input = host.querySelector('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '50');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(commit).not.toHaveBeenCalled();
    expect(input.value).toBe('42.0');
  });

  it('ends an active label drag on lost capture and unmount', () => {
    const begin = vi.fn();
    const end = vi.fn();
    act(() => root.render(<NumberField label="Radius" value={10} onCommit={vi.fn()} onBeginDrag={begin} onEndDrag={end}/>));
    const label = host.querySelector('label')!;
    Object.defineProperty(label, 'setPointerCapture', { value: vi.fn() });
    act(() => label.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerId: 1 })));
    act(() => label.dispatchEvent(new PointerEvent('lostpointercapture', { bubbles: true, pointerId: 1 })));
    expect(begin).toHaveBeenCalledOnce();
    expect(end).toHaveBeenCalledOnce();

    act(() => label.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerId: 2 })));
    act(() => root.unmount());
    expect(end).toHaveBeenCalledTimes(2);
    root = createRoot(host);
  });
});
