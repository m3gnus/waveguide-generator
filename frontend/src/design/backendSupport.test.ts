import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import { activeBackendName, backendLimitation, backendSupports, type BackendIdentity } from './backendSupport';
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

const labels = (id: string, value: unknown, backend: BackendIdentity) =>
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
  it('grants Metal full-3D mounting/import features but keeps meridian separate', () => {
    expect(backendSupports('metal', 'infinite-baffle')).toBe(true);
    expect(backendSupports('metal', 'meridian-fast-path')).toBe(false);
    expect(backendSupports('metal', 'imported-geometry')).toBe(true);
  });

  it('gives BEMPP coupled IB but not meridian or imported geometry', () => {
    expect(backendSupports('bempp', 'infinite-baffle')).toBe(true);
    expect(backendSupports('bempp', 'meridian-fast-path')).toBe(false);
    expect(backendSupports('bempp', 'imported-geometry')).toBe(false);
    expect(backendSupports('axisym', 'meridian-fast-path')).toBe(true);
  });

  it('offers coupled IB when the host carries Axisym, whatever the full-3D backend', () => {
    // The planner reaches for the meridian runner before any full-3D fallback,
    // so a BEAT+Axisym host solves an eligible circular infinite-baffle design
    // even though BEAT refuses one. Gating on the full-3D record alone removed
    // the option from designs the server would have accepted.
    const beat = { ...engine('beat', true), formulations: ['full-3d'], mountings: ['free-standing'] };
    const axisym = { ...engine('axisym', true), formulations: ['axisymmetric'], mountings: ['free-standing', 'infinite-baffle'] };

    expect(backendSupports(beat, 'infinite-baffle')).toBe(false);
    expect(backendSupports(beat, 'infinite-baffle', [beat, axisym])).toBe(true);
    expect(backendLimitation(beat, 'infinite-baffle', [beat, axisym])).toBeUndefined();

    // An unavailable Axisym runner is not a capability, and the host list must
    // not rescue a feature Axisym itself does not claim.
    expect(backendSupports(beat, 'infinite-baffle', [beat, { ...axisym, available: false }])).toBe(false);
    expect(backendSupports(beat, 'infinite-baffle', [beat, { ...axisym, mountings: ['free-standing'] }])).toBe(false);
    // Unrelated features are unaffected by the host list.
    expect(backendSupports(beat, 'imported-geometry', [beat, axisym])).toBe(false);
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
    const message = backendLimitation('bempp', 'imported-geometry');
    expect(message).toContain('BEMPP');
    expect(message).toContain('imported');
    expect(message).toContain('Metal');
    expect(backendLimitation('metal', 'infinite-baffle')).toBeUndefined();
  });
});

describe('simulation type gating', () => {
  it('offers infinite baffle on Metal', () => {
    expect(labels('simulation.sim_type', 'freestanding', 'metal')).toEqual(['Free-standing', 'Infinite baffle']);
  });

  it('offers infinite baffle on BEMPP', () => {
    expect(labels('simulation.sim_type', 'freestanding', 'bempp')).toEqual(['Free-standing', 'Infinite baffle']);
    expect(fieldUnsupportedFeature(field('simulation.sim_type'), 'freestanding', 'bempp')).toBeUndefined();
  });

  /* An ATH .cfg with `ABEC.SimType = 1`, or a .wg file authored on a Mac,
   * arrives already set to a value this host cannot solve. Removing the option
   * would leave the select blank and the design unfixable. */
  it('keeps a selected BEMPP infinite baffle supported', () => {
    expect(labels('simulation.sim_type', 'infinite-baffle', 'bempp')).toEqual(['Free-standing', 'Infinite baffle']);
    expect(fieldUnsupportedFeature(field('simulation.sim_type'), 'infinite-baffle', 'bempp'))
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
