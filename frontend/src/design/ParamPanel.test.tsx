import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { ParamPanel, resolveOuterBodyMode } from './ParamPanel';

describe('outer-body precedence', () => {
  it('matches all four server resolution branches', () => {
    const design = designForFamily('OSSE');
    // A fresh design carries ATH's 5 mm default wall (config_parser.py:296,
    // verified against ath.exe), so it starts freestanding, not bare.
    expect(design.mesh.wall_thickness).toBe(5);
    expect(resolveOuterBodyMode(design)).toBe('freestanding');
    design.mesh.wall_thickness = 0;
    expect(resolveOuterBodyMode(design)).toBe('bare');
    design.mesh.wall_thickness = 5;
    expect(resolveOuterBodyMode(design)).toBe('freestanding');
    design.enclosure.depth = 280;
    expect(resolveOuterBodyMode(design)).toBe('enclosure');
    design.simulation.sim_type = 'infinite-baffle';
    expect(resolveOuterBodyMode(design)).toBe('infinite-baffle');
  });
});

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

  it('shows cross-tab search results with both tab labels', () => {
    const input = host.querySelector<HTMLInputElement>('#parameter-filter')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, 'mesh');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(host.textContent).toContain('Geometry matches');
    expect(host.textContent).toContain('Simulation matches');
    expect(host.querySelector('[data-parameter-id="mesh.angular_segments"]')).not.toBeNull();
    expect(host.querySelector('[data-parameter-id="mesh.throat_resolution"]')).not.toBeNull();
  });

  it('uses accessible keyboard tabs and persists the active tab', () => {
    const geometry = host.querySelector<HTMLButtonElement>('#parameter-tab-geometry')!;
    const simulation = host.querySelector<HTMLButtonElement>('#parameter-tab-simulation')!;
    expect(geometry.getAttribute('role')).toBe('tab');
    expect(geometry.getAttribute('aria-selected')).toBe('true');
    act(() => geometry.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })));
    expect(simulation.getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(simulation);
    expect(localStorage.getItem('wg-param-active-tab')).toBe('simulation');
    expect(host.querySelector('#parameter-panel-simulation')?.hasAttribute('hidden')).toBe(false);
    act(() => simulation.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })));
    expect(geometry.getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(geometry);
  });

  it('persists section collapse state', () => {
    const source = host.querySelector<HTMLElement>('[data-section="Source Definition"]')!;
    act(() => source.querySelector<HTMLButtonElement>('.section-head')!.click());
    expect(localStorage.getItem('wg-param-section-open:Source Definition')).toBe('false');
    expect(source.classList.contains('closed')).toBe(true);
  });

  it('changes outer-body modes in one mutation and restores the last wall thickness', () => {
    const select = host.querySelector<HTMLSelectElement>('#outer-body-mode')!;
    const choose = (value: string) => act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(select, value);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const before = useDesignStore.getState().designRevision;
    choose('freestanding');
    expect(useDesignStore.getState().designRevision).toBe(before + 1);
    expect(useDesignStore.getState().design.mesh.wall_thickness).toBe(5);
    expect(useDesignStore.getState().design.enclosure.depth).toBe(0);

    act(() => useDesignStore.getState().updateValue('mesh.wall_thickness', 8));
    choose('enclosure');
    expect(useDesignStore.getState().design.mesh.wall_thickness).toBe(0);
    expect(useDesignStore.getState().design.enclosure.depth).toBe(280);
    choose('freestanding');
    expect(useDesignStore.getState().design.mesh.wall_thickness).toBe(8);
    expect(useDesignStore.getState().design.enclosure.depth).toBe(0);
  });

  it('plainly reports the infinite-baffle override', () => {
    act(() => useDesignStore.getState().updateValue('simulation.sim_type', 'infinite-baffle'));
    expect(host.textContent).toContain('Infinite baffle simulation overrides the outer body.');
    expect(host.textContent).toContain('Resolved mode');
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
    act(() => host.querySelector<HTMLButtonElement>('#parameter-tab-simulation')!.click());
    expect(host.querySelector('[data-section="Solve options"]')).not.toBeNull();
    expect(host.querySelector('[data-section="Directivity Map"]')).not.toBeNull();
    for (const id of ['solve-engine', 'mesh-validation-mode', 'frequency-spacing', 'solve-verbose', 'polar-angle-start', 'polar-angle-end', 'polar-angle-step', 'polar-distance', 'polar-norm-angle', 'polar-diagonal-angle', 'polar-observation-origin', 'polar-spherical-sampling']) {
      expect(host.querySelector(`#${id}`), id).not.toBeNull();
    }
    act(() => {
      useDesignStore.getState().setFamily('FREEFORM');
      host.querySelector<HTMLButtonElement>('#parameter-tab-geometry')!.click();
    });
    expect(host.querySelectorAll('.editable-parameter-table')).toHaveLength(3);
    expect(host.querySelectorAll('.point-paste textarea')).toHaveLength(2);
    expect(host.textContent).not.toContain('tangent scale');
    expect(host.textContent).not.toContain('Spline overshoot');
    expect(host.querySelector('input[aria-label$=" strength"]')).toBeNull();
    expect(host.querySelector<HTMLSelectElement>('select[aria-label="Station 1 shape"]')?.value).toBe('ellipse');
  });
});
