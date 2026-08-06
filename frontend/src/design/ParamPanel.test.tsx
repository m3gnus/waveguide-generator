import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { ParamPanel, domainName, parameterRevealRequest, requestParameterReveal, resolveOuterBodyMode, symmetrySummary } from './ParamPanel';

/**
 * The panel reads capabilities and symmetry through React Query, exactly as
 * `ReactPanelRenderer` mounts it behind `AppQueryProvider`. A per-test client
 * keeps one test's cached answers out of the next one's, and no retries keep a
 * failed jsdom fetch from rescheduling after the test has torn the root down.
 */
let queryClient: QueryClient;

function withQueryClient(children: ReactNode) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

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
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(withQueryClient(<ParamPanel tab="geometry" />)));
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    queryClient.clear();
  });

  it('filters across labels and ATH/v1 keys, including a mode-hidden field', () => {
    const input = host.querySelector<HTMLInputElement>('#parameter-filter-geometry')!;
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

  it('keeps each dock panel filter scoped to its own parameter category', () => {
    const input = host.querySelector<HTMLInputElement>('#parameter-filter-geometry')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, 'mesh');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(host.querySelector('[data-parameter-id="mesh.angular_segments"]')).not.toBeNull();
    expect(host.querySelector('[data-parameter-id="mesh.throat_resolution"]')).toBeNull();
  });

  it('uses the dock tab as its only category switcher', () => {
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    expect(localStorage.getItem('wg-param-active-tab')).toBeNull();
    expect(host.querySelector('[data-param-tab="geometry"]')).not.toBeNull();
    expect(host.textContent).not.toContain('complete design inventory');
  });

  it('reveals and focuses a parameter routed from the command palette', async () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    await act(async () => {
      requestParameterReveal({ id: 'simulation.f1', tab: 'simulation', query: 'Sweep start' });
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
    const entry = host.querySelector<HTMLElement>('[data-parameter-id="simulation.f1"]')!;
    expect(entry).not.toBeNull();
    expect(document.activeElement).toBe(entry.querySelector('input'));
    expect(host.querySelector<HTMLInputElement>('#parameter-filter-simulation')?.value).toBe('Sweep start');
  });

  it('persists section collapse state', () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
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
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
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
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
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
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    expect(host.querySelector('[data-section="Solve options"]')).not.toBeNull();
    expect(host.querySelector('[data-section="Directivity Map"]')).not.toBeNull();
    for (const id of ['solve-engine', 'mesh-validation-mode', 'frequency-spacing', 'solve-verbose', 'polar-angle-start', 'polar-angle-end', 'polar-angle-step', 'polar-distance', 'polar-norm-angle', 'polar-diagonal-angle', 'polar-observation-origin', 'polar-spherical-sampling']) {
      expect(host.querySelector(`#${id}`), id).not.toBeNull();
    }
    act(() => {
      useDesignStore.getState().setFamily('FREEFORM');
      root.render(withQueryClient(<ParamPanel tab="geometry" />));
    });
    expect(host.querySelectorAll('.editable-parameter-table')).toHaveLength(3);
    expect(host.querySelectorAll('.point-paste textarea')).toHaveLength(2);
    expect(host.textContent).not.toContain('tangent scale');
    expect(host.textContent).not.toContain('Spline overshoot');
    expect(host.querySelector('input[aria-label$=" strength"]')).toBeNull();
    expect(host.querySelector<HTMLSelectElement>('select[aria-label="Station 1 shape"]')?.value).toBe('ellipse');
  });

  it('changes FREEFORM length without moving or dropping normalized anchors', () => {
    act(() => {
      useDesignStore.getState().setFamily('FREEFORM');
      useDesignStore.getState().updateValue('profile_h.points', [
        { t: 0, r: 12.7 }, { t: 70 / 120, r: 60 }, { t: 1, r: 140 },
      ]);
    });
    const before = structuredClone(useDesignStore.getState().design.profile_h!.points);
    const input = host.querySelector<HTMLInputElement>('[data-parameter-id="freeform.length"] input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '60');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => input.blur());
    expect(useDesignStore.getState().design.length).toBe(60);
    expect(useDesignStore.getState().design.profile_h!.points).toEqual(before);
  });
});

describe('automatic solve domain', () => {
  it('names each ATH quadrant mask the way the solver reduces it', () => {
    expect(domainName(1)).toBe('Quarter domain');
    expect(domainName(12)).toBe('Half domain (XZ)');
    expect(domainName(14)).toBe('Half domain (YZ)');
    expect(domainName(1234)).toBe('Full domain');
  });

  it('reports only the planes that were actually rejected, and says so when none were', () => {
    expect(symmetrySummary({
      quadrants: 1, xz: true, yz: true, reasons: { xz: [], yz: [] },
      tolerance_mm: 0.01, relative_tolerance: 2e-4,
    })).toBe('Both mirror planes hold.');
    expect(symmetrySummary({
      quadrants: 12, xz: true, yz: false,
      reasons: { xz: ['unused'], yz: ['guiding curve is rotated'] },
      tolerance_mm: 0.01, relative_tolerance: 2e-4,
    })).toBe('guiding curve is rotated');
  });
});

describe('parameter reveal requests', () => {
  it('is claimed by its own tab and left alone by the other', () => {
    requestParameterReveal({ id: 'rosse.R', tab: 'geometry', query: 'Mouth radius' });
    expect(parameterRevealRequest.claim('simulation')).toBeNull();
    expect(parameterRevealRequest.claim('geometry')).toMatchObject({ id: 'rosse.R' });
    // Claiming consumes it, so a later mount does not re-apply a stale filter.
    expect(parameterRevealRequest.claim('geometry')).toBeNull();
  });

  it('is readable by a panel that only mounts after the request was made', () => {
    // The palette activates a tab and then routes, so the panel that must act
    // on the request frequently does not exist when it is made.
    requestParameterReveal({ id: 'simulation.f1', tab: 'simulation', query: 'Sweep start' });
    expect(parameterRevealRequest.getSnapshot()).toMatchObject({ tab: 'simulation' });
    let notified = 0;
    const unsubscribe = parameterRevealRequest.subscribe(() => { notified += 1; });
    requestParameterReveal({ id: 'rosse.R', tab: 'geometry', query: 'Mouth radius' });
    expect(notified).toBe(1);
    unsubscribe();
    parameterRevealRequest.claim('geometry');
  });
});
