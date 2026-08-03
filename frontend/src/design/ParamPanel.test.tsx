import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resetDesignStore } from '../stores/design';
import { ParamPanel } from './ParamPanel';

describe('ParamPanel inventory UX', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    resetDesignStore();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<ParamPanel />));
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  it('filters across labels and ATH/v1 keys, including a mode-hidden field', () => {
    const input = host.querySelector<HTMLInputElement>('#parameter-filter')!;
    const setInputValue = (value: string) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
    act(() => {
      setInputValue('zMapPoints');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const entries = host.querySelectorAll('[data-parameter-id]');
    expect(entries).toHaveLength(1);
    expect(entries[0].getAttribute('data-parameter-id')).toBe('mesh.z_map_points');
    expect(host.textContent).toContain('normally hidden by the active mode');
  });

  it('persists section collapse state', () => {
    const source = host.querySelector<HTMLElement>('[data-section="Source"]')!;
    act(() => source.querySelector<HTMLButtonElement>('.section-head')!.click());
    expect(localStorage.getItem('wg-param-section-open:Source')).toBe('false');
    expect(source.classList.contains('closed')).toBe(true);
  });
});
