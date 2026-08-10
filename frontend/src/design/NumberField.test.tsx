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

  it('does not round a higher-precision value when focus leaves without an edit', () => {
    const commit = vi.fn();
    act(() => root.render(<NumberField label="Throat radius" value={18.091} precision={2} onCommit={commit}/>));
    const input = host.querySelector<HTMLInputElement>('input')!;
    expect(input.value).toBe('18.09');
    act(() => input.focus());
    act(() => input.blur());
    expect(commit).not.toHaveBeenCalled();
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

  it('coalesces rapid label moves to one commit per animation frame', () => {
    const commit = vi.fn();
    let animationCallback: FrameRequestCallback | null = null;
    const request = vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((callback) => {
      animationCallback = callback;
      return 17;
    });
    const cancel = vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => undefined);
    act(() => root.render(<NumberField label="Radius" value={10} step={1} onCommit={commit}/>));
    const label = host.querySelector('label')!;
    Object.defineProperty(label, 'setPointerCapture', { value: vi.fn() });
    act(() => {
      label.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerId: 1, clientX: 0 }));
      label.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 1, clientX: 1 }));
      label.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 1, clientX: 2 }));
      label.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 1, clientX: 3 }));
    });
    expect(commit).not.toHaveBeenCalled();
    act(() => animationCallback?.(performance.now()));
    expect(commit).toHaveBeenCalledOnce();
    expect(commit).toHaveBeenCalledWith(13);
    request.mockRestore();
    cancel.mockRestore();
  });

  it('flushes a pending drag value once when unmounted before its frame', () => {
    const commit = vi.fn();
    const end = vi.fn();
    let animationCallback: FrameRequestCallback | null = null;
    const request = vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((callback) => {
      animationCallback = callback;
      return 18;
    });
    const cancel = vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => undefined);
    act(() => root.render(<NumberField label="Radius" value={10} step={1} onCommit={commit} onEndDrag={end}/>));
    const label = host.querySelector('label')!;
    Object.defineProperty(label, 'setPointerCapture', { value: vi.fn() });
    act(() => {
      label.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerId: 1, clientX: 0 }));
      label.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 1, clientX: 4 }));
    });

    act(() => root.unmount());
    expect(cancel).toHaveBeenCalledWith(18);
    expect(commit).toHaveBeenCalledOnce();
    expect(commit).toHaveBeenCalledWith(14);
    expect(end).toHaveBeenCalledOnce();
    (animationCallback as unknown as FrameRequestCallback)(performance.now());
    expect(commit).toHaveBeenCalledOnce();
    root = createRoot(host);
    request.mockRestore();
    cancel.mockRestore();
  });

  it('commits a non-numeric ATH expression verbatim and shows a known evaluated value', () => {
    const commitExpression = vi.fn();
    act(() => root.render(<NumberField label="Radius" value={140} expression={{ raw: 'mouth(p) * 2', value: 280 }} allowExpression onCommit={vi.fn()} onCommitExpression={commitExpression} />));
    expect(host.querySelector<HTMLInputElement>('input')!.value).toBe('mouth(p) * 2');
    expect(host.textContent).toContain('= 280');
    const input = host.querySelector<HTMLInputElement>('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '140 + p');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.blur();
    });
    expect(commitExpression).toHaveBeenCalledWith({ raw: '140 + p', value: null });
  });

  it('preserves a cached expression value when focus leaves without an edit', () => {
    const commitExpression = vi.fn();
    const expression = { raw: '48.5 - 7*cos(2*p)^5', value: 33.7 };
    act(() => root.render(<NumberField label="Coverage" value={33.7} expression={expression} allowExpression onCommit={vi.fn()} onCommitExpression={commitExpression} />));
    const input = host.querySelector<HTMLInputElement>('input')!;
    expect(host.textContent).toContain('= 33.7');
    act(() => input.focus());
    act(() => input.blur());
    expect(commitExpression).not.toHaveBeenCalled();
  });

  it('gives an ATH formula its own row and refuses to scrub it away', () => {
    const commit = vi.fn();
    const commitExpression = vi.fn();
    const formula = '48.5 - 7*cos(2*p)^5 - 16*sin(p)^12';
    act(() => root.render(<NumberField label="Mouth coverage angle" value={33.7} unit="°" expression={{ raw: formula, value: 33.7 }} allowExpression onCommit={commit} onCommitExpression={commitExpression} />));

    const row = host.querySelector('.field-row')!;
    expect(row.classList.contains('expression-row')).toBe(true);
    // The whole formula is present, reading from its start rather than clipped.
    expect(host.querySelector<HTMLInputElement>('input')!.value).toBe(formula);
    // A range track would imply a scrubbable value; a formula has none.
    expect(host.querySelector('.number-track')).toBeNull();

    const label = host.querySelector<HTMLLabelElement>('.field-label')!;
    label.setPointerCapture = vi.fn();
    act(() => {
      label.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
      label.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 90 }));
      label.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
    });
    expect(commit).not.toHaveBeenCalled();
    expect(commitExpression).not.toHaveBeenCalled();
    expect(host.querySelector<HTMLInputElement>('input')!.value).toBe(formula);
  });

  it('keeps a plain numeric field scrubbable', () => {
    act(() => root.render(<NumberField label="Scale" value={1} onCommit={vi.fn()} />));
    expect(host.querySelector('.field-row')!.classList.contains('expression-row')).toBe(false);
    expect(host.querySelector('.number-track')).not.toBeNull();
  });

  it('does not repeat a plain numeric imported value as a green evaluation', () => {
    act(() => root.render(<NumberField label="Sweep start" value={100} expression={{ raw: '100', value: 100 }} allowExpression onCommit={vi.fn()} />));
    expect(host.querySelector<HTMLInputElement>('input')?.value).toBe('100');
    expect(host.querySelector('.expr-value')).toBeNull();
    expect(host.textContent).not.toContain('= 100');
  });
  const setValue = (input: HTMLInputElement, value: string) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };

  describe('out-of-range input states its reason', () => {
    it('names the allowed range visibly and to assistive tech', async () => {
      const commit = vi.fn();
      await act(async () => { root.render(<NumberField label="Mouth radius" symbol="R" unit="mm" value={140} min={10} max={400} precision={2} onCommit={commit}/>); });
      const input = host.querySelector('input')!;
      await act(async () => { setValue(input, '99999'); });

      const message = host.querySelector('.field-error');
      // The bounds ARE the message: a red border with no reason was the defect.
      expect(message?.textContent).toBe('Must be between 10 and 400 mm.');
      expect(input.getAttribute('aria-invalid')).toBe('true');
      expect(input.getAttribute('aria-describedby')).toBe(message?.id);
      expect(message?.getAttribute('role')).toBe('alert');
      // Visible, not sr-only — that was the other half of the defect.
      expect(message?.classList.contains('sr-only')).toBe(false);
      expect(commit).not.toHaveBeenCalled();
    });

    it('carries its range as spinbutton semantics', async () => {
      await act(async () => { root.render(<NumberField label="Mouth radius" unit="mm" value={140} min={10} max={400} precision={2} onCommit={vi.fn()}/>); });
      const input = host.querySelector('input')!;
      expect(input.getAttribute('role')).toBe('spinbutton');
      expect(input.getAttribute('aria-valuemin')).toBe('10');
      expect(input.getAttribute('aria-valuemax')).toBe('400');
      expect(input.getAttribute('aria-valuenow')).toBe('140');
      expect(input.getAttribute('aria-valuetext')).toBe('140.00 mm');
    });

    it('steps with the arrow keys and clamps at the bounds', async () => {
      // The field is controlled, so the test has to play the parent and feed
      // the committed value back. Without that, a step back to the original
      // value is correctly suppressed as a no-op and the assertion ends up
      // measuring the harness rather than the component.
      const commit = vi.fn();
      let current = 140;
      const render = async () => {
        await act(async () => {
          root.render(<NumberField label="Bending" value={current} min={10} max={400} step={2} precision={2} onCommit={(value) => { current = value; commit(value); }}/>);
        });
      };
      const press = async (key: string, init: KeyboardEventInit = {}) => {
        const input = host.querySelector('input')!;
        await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init })); });
        await render();
      };
      await render();

      await press('ArrowUp');
      expect(commit).toHaveBeenLastCalledWith(142);
      await press('ArrowDown');
      expect(commit).toHaveBeenLastCalledWith(140);
      // Shift and PageUp are the coarse gesture.
      await press('PageUp');
      expect(commit).toHaveBeenLastCalledWith(160);
      await press('ArrowUp', { shiftKey: true });
      expect(commit).toHaveBeenLastCalledWith(180);

      // Clamped, never past the bound.
      current = 399;
      await render();
      await press('PageUp');
      expect(commit).toHaveBeenLastCalledWith(400);
    });
  });

});
