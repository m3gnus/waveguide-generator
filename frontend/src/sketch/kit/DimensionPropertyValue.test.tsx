import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DimensionPropertyValue } from './DimensionPropertyValue';
import { sourceScaleLegacy } from '../adapters/fixtures/sourceScaleLegacy';
import type { Constraint } from '../model';

describe('dimension property expression entry', () => {
  let host: HTMLDivElement, root: Root;
  const commit = vi.fn().mockResolvedValue(undefined);
  const input = () => host.querySelector('input')!;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    commit.mockReset().mockResolvedValue(undefined);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); });
  async function mount(dimension: Constraint & { value: number }, disabled = false) {
    const sketch = structuredClone(sourceScaleLegacy.sketch);
    sketch.constraints = [...sketch.constraints.filter(c => c.id !== dimension.id), dimension];
    await act(async () => root.render(<DimensionPropertyValue sketch={sketch} dimension={dimension} disabled={disabled} onCommit={commit}/>));
  }
  async function type(value: string) {
    await act(async () => input().focus());
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input(), value);
      input().dispatchEvent(new Event('input', { bubbles: true }));
    });
  }
  const depth = () => ({ kind: 'distanceY', id: 'depth', a: 'elbow', b: 'rim', value: 4, driving: true } as const);

  it('prefills an existing expression, shows its marker and suggestions, and submits the shared command on blur', async () => {
    await mount({ ...depth(), expression: '[Cap width] + 1' });
    expect(input().value).toBe('[Cap width] + 1');
    expect(host.querySelector('.sketch-expression-marker')!.textContent).toBe('ƒ');
    await type('Cap');
    expect(host.querySelector('[role="option"]')!.textContent).toBe('Cap width');
    await act(async () => input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })));
    expect(input().value).toBe('[Cap width]');
    expect(commit).not.toHaveBeenCalled();
    await act(async () => input().blur());
    expect(commit).toHaveBeenCalledExactlyOnceWith({ kind: 'setDimensionExpression', id: 'depth', expression: '[Cap width]' });
  });

  it('autocomplete falls back to the ID for a label containing a closing bracket', async () => {
    const sketch = structuredClone(sourceScaleLegacy.sketch), dimension = depth();
    sketch.constraints = [{ ...dimension }, { ...dimension, id: 'width', label: 'Width]', value: 6 }];
    await act(async () => root.render(<DimensionPropertyValue sketch={sketch} dimension={dimension} disabled={false} onCommit={commit}/>));
    await type('Wid');
    expect(host.querySelector('[role="option"]')!.textContent).toBe('Width]');
    await act(async () => input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })));
    expect(input().value).toBe('width');
    await act(async () => input().blur());
    expect(commit).toHaveBeenCalledExactlyOnceWith({ kind: 'setDimensionExpression', id: 'depth', expression: 'width' });
  });

  it.each(['12', ''])("repairs an imported unresolved expression with '%s'", async text => {
    await mount({ ...depth(), expression: 'Missing*2' }); await type(text);
    await act(async () => input().blur());
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith(text ? { kind: 'setDimension', id: 'depth', value: 12 }
      : { kind: 'setDimensionExpression', id: 'depth', expression: '' });
  });

  it.each([
    [{ kind: 'distanceX', id: 'cap-width', a: 'pole', b: 'elbow', value: 3, driving: true, diameter: true } as const, '6.00', '8', 4],
    [{ kind: 'angle', id: 'angle', a: 'cap', b: 'cone', value: Math.PI / 2, driving: true } as const, '90.00', '45', Math.PI / 4],
  ])('uses display values for %s and converts plain numbers on Enter', async (dimension, shown, text, internal) => {
    await mount(dimension);
    expect(input().value).toBe(shown);
    await type(text);
    await act(async () => input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })));
    expect(commit).toHaveBeenCalledExactlyOnceWith({ kind: 'setDimension', id: dimension.id, value: internal });
    await act(async () => input().blur());
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it('keeps actionable validation errors next to the field and does not submit invalid formulas', async () => {
    await mount(depth()); await type('unknown + 1');
    await act(async () => input().blur());
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('Use a unique dimension name or ID');
    expect(input().getAttribute('aria-invalid')).toBe('true');
    expect(input().value).toBe('unknown + 1');
    expect(commit).not.toHaveBeenCalled();
  });

  it('cancels with Escape, preserves untouched precision, and disables reference values', async () => {
    await mount({ ...depth(), value: 4.123456789 });
    await act(async () => input().focus());
    expect(input().value).toBe('4.123456789');
    await act(async () => input().blur());
    expect(commit).not.toHaveBeenCalled();
    await type('9');
    await act(async () => input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
    await act(async () => input().blur());
    expect(input().value).toBe('4.12'); expect(commit).not.toHaveBeenCalled();
    await mount({ ...depth(), driving: false }, true);
    expect(input().disabled).toBe(true);
  });

  it('reports a rejected solve locally and retains the entry for correction', async () => {
    commit.mockRejectedValue(new Error('Remove the conflicting depth constraint.'));
    await mount(depth()); await type('6');
    await act(async () => input().blur());
    expect(host.querySelector('[role="alert"]')!.textContent).toBe('Remove the conflicting depth constraint.');
    expect(input().value).toBe('6');
  });

  it('can submit twice without leaving the input, including returning to the original value', async () => {
    await mount(depth()); await type('5');
    await act(async () => input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })));
    await mount({ ...depth(), value: 5 });
    await type('4');
    await act(async () => input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })));
    expect(commit).toHaveBeenCalledTimes(2);
    expect(commit).toHaveBeenLastCalledWith({ kind: 'setDimension', id: 'depth', value: 4 });
  });
});
