import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import {
  activeBackendName,
  backendLimitation,
  backendSupports,
  plannedBackendCapabilities,
  type BackendIdentity,
} from './backendSupport';
import type { EngineCapability, EngineSelection } from '../jobs/actions';
import {
  PARAMETER_REGISTRY,
  fieldIsVisible,
  fieldOptionsForBackend,
  fieldUnsupportedFeature,
} from './parameterRegistry';

const engine = (name: string, available: boolean): EngineCapability => ({
  name, available, reason: null, version: null, fast_paths: [],
  formulations: name === 'axisym' ? ['axisymmetric'] : ['full-3d'],
  mountings: name === 'beat' ? ['free-standing'] : ['free-standing', 'infinite-baffle'],
  geometry_sources: name === 'metal' ? ['parametric', 'imported'] : ['parametric'],
});

const selection: EngineSelection = {
  default: 'auto',
  resolvedDefault: 'metal',
  full3dOrder: ['metal', 'beat', 'bempp', 'dryrun'],
  axisymmetricRunner: 'axisym',
};

const field = (id: string) => {
  const found = PARAMETER_REGISTRY.find((item) => item.id === id);
  if (!found) throw new Error(`missing registry entry: ${id}`);
  return found;
};

const labels = (id: string, value: unknown, backend: BackendIdentity) =>
  fieldOptionsForBackend(field(id), value, backend).map((option) => option.label);

describe('active backend resolution', () => {
  it('resolves AUTO to the first available backend in preference order', () => {
    expect(activeBackendName('auto', [engine('metal', true), engine('bempp', true)], selection)).toBe('metal');
    expect(activeBackendName('auto', [engine('metal', false), engine('bempp', true)], { ...selection, resolvedDefault: 'bempp' })).toBe('bempp');
  });

  it('honours an explicit selection without consulting availability', () => {
    expect(activeBackendName('bempp', [engine('metal', true)])).toBe('bempp');
  });

  it('returns null rather than throwing when nothing is available', () => {
    expect(activeBackendName('auto', [], { ...selection, resolvedDefault: null })).toBeNull();
    expect(activeBackendName('auto', [engine('metal', false)], { ...selection, resolvedDefault: null })).toBeNull();
  });
});

describe('backend feature support', () => {
  it('grants Metal full-3D mounting/import features but keeps meridian separate', () => {
    const metal = engine('metal', true);
    expect(backendSupports(metal, 'infinite-baffle')).toBe(true);
    expect(backendSupports(metal, 'meridian-fast-path')).toBe(false);
    expect(backendSupports(metal, 'imported-geometry')).toBe(true);
  });

  it('gives BEMPP coupled IB but not meridian or imported geometry', () => {
    const bempp = engine('bempp', true);
    expect(backendSupports(bempp, 'infinite-baffle')).toBe(true);
    expect(backendSupports(bempp, 'meridian-fast-path')).toBe(false);
    expect(backendSupports(bempp, 'imported-geometry')).toBe(false);
    expect(backendSupports(engine('axisym', true), 'meridian-fast-path')).toBe(true);
  });

  it('offers coupled IB in AUTO when a planned Axisym candidate supports it', () => {
    // The planner reaches for the meridian runner before any full-3D fallback,
    // so a BEAT+Axisym host solves an eligible circular infinite-baffle design
    // even though BEAT refuses one. Gating on the full-3D record alone removed
    // the option from designs the server would have accepted.
    const beat = { ...engine('beat', true), formulations: ['full-3d'], mountings: ['free-standing'] };
    const axisym = { ...engine('axisym', true), formulations: ['axisymmetric'], mountings: ['free-standing', 'infinite-baffle'] };

    const autoPlan = plannedBackendCapabilities('auto', [beat, axisym], {
      ...selection, resolvedDefault: 'beat', full3dOrder: ['beat'],
    });
    expect(backendSupports(beat, 'infinite-baffle')).toBe(false);
    expect(backendSupports(beat, 'infinite-baffle', autoPlan)).toBe(true);
    expect(backendLimitation(beat, 'infinite-baffle', autoPlan)).toBeUndefined();

    // Explicit BEAT is not rescued by some other capability on the host.
    const explicitPlan = plannedBackendCapabilities('beat', [beat, axisym], selection);
    expect(backendSupports(beat, 'infinite-baffle', explicitPlan)).toBe(false);
  });

  it('lets AUTO skip BEAT for a coupled IB-capable BEMPP without Axisym', () => {
    const beat = { ...engine('beat', true), mountings: ['free-standing'] };
    const bempp = { ...engine('bempp', true), mountings: ['free-standing', 'infinite-baffle'] };
    const axisym = { ...engine('axisym', false), mountings: ['free-standing', 'infinite-baffle'] };
    const advertised = {
      ...selection, resolvedDefault: 'beat', full3dOrder: ['metal', 'beat', 'bempp'],
    };

    const autoPlan = plannedBackendCapabilities('auto', [beat, bempp, axisym], advertised);
    expect(autoPlan.map((item) => item.name)).toEqual(['beat', 'bempp']);
    expect(backendSupports(beat, 'infinite-baffle', autoPlan)).toBe(true);
    expect(backendSupports(beat, 'infinite-baffle', plannedBackendCapabilities('beat', [beat, bempp], advertised))).toBe(false);
  });

  it('uses the server capability payload for version-dependent BEMPP IB support', () => {
    const oldBempp = { ...engine('bempp', true), formulations: ['full-3d'], mountings: ['free-standing'] };
    const coupledBempp = { ...oldBempp, mountings: ['free-standing', 'infinite-baffle'] };
    expect(backendSupports(oldBempp, 'infinite-baffle')).toBe(false);
    expect(backendSupports(coupledBempp, 'infinite-baffle')).toBe(true);
  });

  /* A probe that has not landed must not hide controls: the pre-gating
   * behaviour (offer everything, let the server refuse) is the safe fallback,
   * whereas hiding on a guess would strand a design with no way to edit it. */
  it('treats an unresolved or unknown backend as capable', () => {
    expect(backendSupports(null, 'infinite-baffle')).toBe(true);
    expect(backendSupports('some-future-engine', 'infinite-baffle')).toBe(true);
  });

  it('names both the limitation and the way around it', () => {
    const message = backendLimitation(engine('bempp', true), 'imported-geometry');
    expect(message).toContain('BEMPP');
    expect(message).toContain('imported');
    expect(message).toContain('Metal');
    expect(backendLimitation(engine('metal', true), 'infinite-baffle')).toBeUndefined();
  });
});

describe('simulation type gating', () => {
  it('offers infinite baffle on Metal', () => {
    expect(labels('simulation.sim_type', 'freestanding', engine('metal', true))).toEqual(['Free-standing', 'Infinite baffle']);
  });

  it('offers infinite baffle on BEMPP', () => {
    expect(labels('simulation.sim_type', 'freestanding', engine('bempp', true))).toEqual(['Free-standing', 'Infinite baffle']);
    expect(fieldUnsupportedFeature(field('simulation.sim_type'), 'freestanding', engine('bempp', true))).toBeUndefined();
  });

  /* An ATH .cfg with `ABEC.SimType = 1`, or a .wg file authored on a Mac,
   * arrives already set to a value this host cannot solve. Removing the option
   * would leave the select blank and the design unfixable. */
  it('keeps a selected BEMPP infinite baffle supported', () => {
    expect(labels('simulation.sim_type', 'infinite-baffle', engine('bempp', true))).toEqual(['Free-standing', 'Infinite baffle']);
    expect(fieldUnsupportedFeature(field('simulation.sim_type'), 'infinite-baffle', engine('bempp', true)))
      .toBeUndefined();
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
