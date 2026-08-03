import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
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

  it('rejects prospective inverted frequency bounds before committing', () => {
    const entry = host.querySelector<HTMLElement>('[data-parameter-id="simulation.f1"]')!;
    const input = entry.querySelector<HTMLInputElement>('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '18000');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    act(() => input.blur());
    expect(useDesignStore.getState().design.simulation.f1).toBe(400);
  });

  it('enforces the legacy Source.Velocity 1/2 domain', () => {
    act(() => useDesignStore.getState().setSourceConvention('legacy'));
    const entry = host.querySelector<HTMLElement>('[data-parameter-id="source.velocity"]')!;
    const input = entry.querySelector<HTMLInputElement>('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '3');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    act(() => input.blur());
    expect(useDesignStore.getState().design.source.velocity).toBe(1);
  });

  it('renders the solve/directivity contracts and editable FREEFORM tables', () => {
    expect(host.querySelector('[data-section="Solve options"]')).not.toBeNull();
    expect(host.querySelector('[data-section="Directivity Map"]')).not.toBeNull();
    for (const id of ['solve-engine', 'mesh-validation-mode', 'frequency-spacing', 'solve-verbose', 'polar-angle-start', 'polar-angle-end', 'polar-angle-step', 'polar-distance', 'polar-norm-angle', 'polar-diagonal-angle', 'polar-observation-origin', 'polar-spherical-sampling']) {
      expect(host.querySelector(`#${id}`), id).not.toBeNull();
    }
    act(() => useDesignStore.getState().setFamily('FREEFORM'));
    expect(host.querySelectorAll('.editable-parameter-table')).toHaveLength(3);
    expect(host.querySelectorAll('.point-paste textarea')).toHaveLength(2);
  });
});
