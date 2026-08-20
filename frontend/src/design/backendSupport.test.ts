import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import { activeBackendName, backendLimitation, backendSupports } from './backendSupport';
import {
  PARAMETER_REGISTRY,
  fieldIsVisible,
  fieldOptionsForBackend,
  fieldUnsupportedFeature,
} from './parameterRegistry';

const engine = (name: string, available: boolean) => ({
  name, available, reason: null, version: null, fast_paths: [],
});

const field = (id: string) => {
  const found = PARAMETER_REGISTRY.find((item) => item.id === id);
  if (!found) throw new Error(`missing registry entry: ${id}`);
  return found;
};

const labels = (id: string, value: unknown, backend: string | null) =>
  fieldOptionsForBackend(field(id), value, backend).map((option) => option.label);

describe('active backend resolution', () => {
  it('resolves AUTO to the first available backend in preference order', () => {
    expect(activeBackendName('auto', [engine('metal', true), engine('bempp', true)])).toBe('metal');
    expect(activeBackendName('auto', [engine('metal', false), engine('bempp', true)])).toBe('bempp');
  });

  it('honours an explicit selection without consulting availability', () => {
    expect(activeBackendName('bempp', [engine('metal', true)])).toBe('bempp');
  });

  it('returns null rather than throwing when nothing is available', () => {
    expect(activeBackendName('auto', [])).toBeNull();
    expect(activeBackendName('auto', [engine('metal', false)])).toBeNull();
  });
});

describe('backend feature support', () => {
  it('grants Metal every gated feature', () => {
    expect(backendSupports('metal', 'infinite-baffle')).toBe(true);
    expect(backendSupports('metal', 'meridian-fast-path')).toBe(true);
    expect(backendSupports('metal', 'imported-geometry')).toBe(true);
  });

  it('denies BEMPP the three features its server path refuses', () => {
    expect(backendSupports('bempp', 'infinite-baffle')).toBe(false);
    expect(backendSupports('bempp', 'meridian-fast-path')).toBe(false);
    expect(backendSupports('bempp', 'imported-geometry')).toBe(false);
  });

  /* A probe that has not landed must not hide controls: the pre-gating
   * behaviour (offer everything, let the server refuse) is the safe fallback,
   * whereas hiding on a guess would strand a design with no way to edit it. */
  it('treats an unresolved or unknown backend as capable', () => {
    expect(backendSupports(null, 'infinite-baffle')).toBe(true);
    expect(backendSupports('some-future-engine', 'infinite-baffle')).toBe(true);
  });

  it('names both the limitation and the way around it', () => {
    const message = backendLimitation('bempp', 'infinite-baffle');
    expect(message).toContain('BEMPP');
    expect(message).toContain('infinite-baffle');
    expect(message).toContain('free-standing');
    expect(backendLimitation('metal', 'infinite-baffle')).toBeUndefined();
  });
});

describe('simulation type gating', () => {
  it('offers infinite baffle on Metal', () => {
    expect(labels('simulation.sim_type', 'freestanding', 'metal')).toEqual(['Free-standing', 'Infinite baffle']);
  });

  it('hides infinite baffle on BEMPP while the design is free-standing', () => {
    expect(labels('simulation.sim_type', 'freestanding', 'bempp')).toEqual(['Free-standing']);
    expect(fieldUnsupportedFeature(field('simulation.sim_type'), 'freestanding', 'bempp')).toBeUndefined();
  });

  /* An ATH .cfg with `ABEC.SimType = 1`, or a .wg file authored on a Mac,
   * arrives already set to a value this host cannot solve. Removing the option
   * would leave the select blank and the design unfixable. */
  it('reveals and flags infinite baffle when the design already selects it', () => {
    expect(labels('simulation.sim_type', 'infinite-baffle', 'bempp')).toEqual(['Free-standing', 'Infinite baffle']);
    expect(fieldUnsupportedFeature(field('simulation.sim_type'), 'infinite-baffle', 'bempp'))
      .toBe('infinite-baffle');
  });
});

describe('solver mode gating', () => {
  it('offers all three modes on Metal with the meridian label intact', () => {
    expect(labels('simulation.solver_mode', 'auto', 'metal'))
      .toEqual(['Auto — full 3D', 'Full 3D', 'Axisymmetric meridian (CPU, force)']);
  });

  it('drops the forced meridian mode on BEMPP', () => {
    expect(labels('simulation.solver_mode', 'auto', 'bempp'))
      .toEqual(['Auto — full 3D', 'Full 3D']);
  });

  it('reveals and flags a forced meridian mode the design already carries', () => {
    expect(labels('simulation.solver_mode', 'circsym', 'bempp'))
      .toEqual(['Auto — full 3D', 'Full 3D', 'Axisymmetric meridian (CPU, force)']);
    expect(fieldUnsupportedFeature(field('simulation.solver_mode'), 'circsym', 'bempp'))
      .toBe('meridian-fast-path');
  });
});

describe('aperture mesh scale', () => {
  /* It sizes the infinite-baffle aperture cap and nothing else, so it is dead
   * on a free-standing design regardless of which backend runs it. */
  it('is hidden for a free-standing design and shown for an infinite-baffle one', () => {
    const design = designForFamily('R-OSSE');
    const entry = field('mesh.aperture_resolution_scale');
    expect(fieldIsVisible(entry, { ...design, simulation: { ...design.simulation, sim_type: 'freestanding' } })).toBe(false);
    expect(fieldIsVisible(entry, { ...design, simulation: { ...design.simulation, sim_type: 'infinite-baffle' } })).toBe(true);
  });
});
