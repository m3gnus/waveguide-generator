import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  designForFamily,
  registerRevisionTimer,
  resetDesignStore,
  seedDesign,
  serializeDesign,
  subscribeRevision,
  useDesignStore,
  type DesignDocument,
  type DesignFamily,
  type RevisionEvent,
} from './design';

function configuredDesign(family: DesignFamily): DesignDocument {
  const design = designForFamily(family);
  design.scale = 1.375;
  design.throat_ext_angle = 7;
  design.throat_ext_length = 11;
  design.slot_length = 3;
  design.length_mode = 'total';
  design.coverage_mode = 'manual';
  design.morph = {
    target_shape: 1, target_width: 333, target_height: 222, corner_radius: 17,
    rate: 4.5, fixed_part: .25, allow_shrinkage: 1,
  };
  design.source = {
    shape: 2, radius: 18, curvature: -1, velocity: .625,
    contours: 'custom/source.contours', velocity_convention: 'axial',
  };
  design.quadrants = [1, 4];
  design.enclosure = {
    depth: 321, edge_radius: 23, edge_type: 2,
    space_l: 31, space_t: 32, space_r: 33, space_b: 34,
    front_resolution: 7.5, back_resolution: 9.5, baffle_margin: 31,
  };
  design.mesh = {
    ...design.mesh,
    angular_segments: 91,
    throat_resolution: 2.25,
    mouth_resolution: 3.75,
    quadrants: 14,
    max_triangles: 765_432,
    max_edge: 8.5,
  };
  design.simulation = {
    f1: 173, f2: 19_321, num_frequencies: 73,
    sim_type: 'infinite-baffle', solver_mode: 'full_3d',
  };
  design.output = { stl: 1, msh: 1 };
  design.extra_keys = { 'Vendor.Custom': 'keep-me' };
  design.extra_blocks = {
    Report: { items: { Title: 'family-safe' }, lines: ['raw row'], comments: ['; retained'] },
  };

  if (family === 'OSSE') {
    design.L = 211;
    design.a = 52;
    design.a0 = 13.5;
    design.r0 = 18.25;
    design.k = 9;
    design.q = .777;
    design.guiding_curve.width = 444;
  } else if (family === 'ICW') {
    design.L = 233;
    design.R = 177;
    design.r0 = 19.5;
    design.a0 = 14.25;
    design.a = 44;
    design.k = 8;
    design.q = 7;
  }
  return design;
}

function expectSharedStatePreserved(actual: DesignDocument, expected: DesignDocument): void {
  expect(actual.scale).toBe(expected.scale);
  expect(actual.throat_ext_angle).toBe(expected.throat_ext_angle);
  expect(actual.throat_ext_length).toBe(expected.throat_ext_length);
  expect(actual.slot_length).toBe(expected.slot_length);
  expect(actual.length_mode).toBe(expected.length_mode);
  expect(actual.coverage_mode).toBe(expected.coverage_mode);
  expect(actual.morph).toEqual(expected.morph);
  expect(actual.source).toEqual(expected.source);
  expect(actual.quadrants).toEqual(expected.quadrants);
  expect(actual.enclosure).toEqual(expected.enclosure);
  expect(actual.mesh).toEqual(expected.mesh);
  expect(actual.simulation).toEqual(expected.simulation);
  expect(actual.output).toEqual(expected.output);
  expect(actual.extra_keys).toEqual(expected.extra_keys);
  expect(actual.extra_blocks).toEqual(expected.extra_blocks);
}

describe('design store revision semantics', () => {
  beforeEach(() => resetDesignStore());

  it('groups a drag into one undo step while revisions remain monotonic', () => {
    const revisions: RevisionEvent[] = [];
    const unsubscribe = subscribeRevision((event) => revisions.push(event));
    const initialRevision = useDesignStore.getState().designRevision;
    useDesignStore.getState().beginDrag();
    useDesignStore.getState().updateField('a', 43);
    useDesignStore.getState().updateField('a', 44);
    useDesignStore.getState().updateField('a', 45);
    useDesignStore.getState().endDrag();

    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    expect(useDesignStore.getState().designRevision).toBe(initialRevision + 3);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.a).toBe(seedDesign.a);
    expect(useDesignStore.getState().designRevision).toBe(initialRevision + 4);
    expect(revisions.map((event) => event.revision)).toEqual([2, 3, 4, 5]);
    unsubscribe();
  });

  it('undo during a drag cancels pending timers and restores the drag snapshot', () => {
    const cancel = vi.fn();
    const unregister = registerRevisionTimer(cancel);
    useDesignStore.getState().beginDrag();
    useDesignStore.getState().updateField('R', 175);
    useDesignStore.getState().undo();

    expect(cancel).toHaveBeenCalledOnce();
    expect(useDesignStore.getState().design.R).toBe(seedDesign.R);
    expect(useDesignStore.getState().dragSnapshot).toBeNull();
    expect(useDesignStore.temporal.getState().isTracking).toBe(true);
    unregister();
  });

  it('bumps the revision for edit, undo, redo, load, and family switch', () => {
    const seen: number[] = [];
    const unsubscribe = subscribeRevision((event) => seen.push(event.revision));
    useDesignStore.getState().updateField('a', 43);
    useDesignStore.getState().undo();
    useDesignStore.getState().redo();
    useDesignStore.getState().loadDesign({ ...structuredClone(seedDesign), a: 41 });
    useDesignStore.getState().setFamily('OSSE');
    expect(seen).toEqual([2, 3, 4, 5, 6]);
    unsubscribe();
  });

  it.each(['undo', 'redo'] as const)('leaves revision timers alone when %s has no history state', (operation) => {
    useDesignStore.temporal.getState().clear();
    const cancel = vi.fn();
    const unregister = registerRevisionTimer(cancel);
    useDesignStore.getState()[operation]();
    expect(cancel).not.toHaveBeenCalled();
    unregister();
  });

  it.each(['family', 'load'] as const)('finalizes active drag history before %s replacement', (operation) => {
    useDesignStore.getState().beginDrag();
    useDesignStore.getState().updateField('R', 175);
    if (operation === 'family') useDesignStore.getState().setFamily('OSSE');
    else useDesignStore.getState().loadDesign({ ...structuredClone(seedDesign), R: 190 });

    expect(useDesignStore.getState().dragSnapshot).toBeNull();
    expect(useDesignStore.temporal.getState().isTracking).toBe(true);
    expect(useDesignStore.temporal.getState().pastStates.length).toBeGreaterThanOrEqual(2);
  });

  it('starts a new undo epoch when a document is replaced', () => {
    useDesignStore.getState().updateField('a', 43);
    useDesignStore.getState().replaceDesign({ ...structuredClone(seedDesign), a: 31 });

    expect(useDesignStore.getState().design.a).toBe(31);
    expect(useDesignStore.temporal.getState().pastStates).toEqual([]);
    expect(useDesignStore.temporal.getState().futureStates).toEqual([]);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.a).toBe(31);
  });

  it('creates FREEFORM designs with only the solved-tangent contract', () => {
    const design = designForFamily('FREEFORM');
    expect(design.length).toBe(120);
    expect(design.profile_h?.points).toEqual([{ t: 0, r: 12.7 }, { t: 1, r: 140 }]);
    expect(design.cross_sections?.[0].shape).toBe('ellipse');
    expect(design).not.toHaveProperty('overshoot_policy');
    expect(design.profile_h).not.toHaveProperty('throat_tangent_scale');
    expect(design.profile_h).not.toHaveProperty('mouth_tangent_scale');
  });
});

describe('family transitions', () => {
  beforeEach(() => resetDesignStore());

  it.each([
    ['OSSE', 'R-OSSE'],
    ['OSSE', 'ICW'],
    ['ICW', 'FREEFORM'],
  ] as const)('preserves shared state and emits an immediate revision for %s → %s', (from, to) => {
    const configured = configuredDesign(from);
    useDesignStore.getState().loadDesign(configured);
    const beforeRevision = useDesignStore.getState().designRevision;
    const events: RevisionEvent[] = [];
    const unsubscribe = subscribeRevision((event) => events.push(event));
    try {
      useDesignStore.getState().setFamily(to);
    } finally {
      unsubscribe();
    }

    const state = useDesignStore.getState();
    expectSharedStatePreserved(state.design, configured);
    expect(state.designRevision).toBe(beforeRevision + 1);
    expect(events).toEqual([{
      revision: beforeRevision + 1,
      reason: 'family',
      immediate: true,
    }]);
  });

  it('drops OSSE-only fields, applies R-OSSE defaults, and carries equivalent throat and mouth values', () => {
    useDesignStore.getState().loadDesign(configuredDesign('OSSE'));
    useDesignStore.getState().setFamily('R-OSSE');
    const design = useDesignStore.getState().design;
    const defaults = designForFamily('R-OSSE');

    for (const field of ['L', 's', 'n', 'h', 'throat_profile', 'rotation', 'circ_arc_radius', 'circ_arc_term_angle']) {
      expect(design).not.toHaveProperty(field);
    }
    expect(design).toMatchObject({
      formula: 'R-OSSE',
      R: defaults.R, m: defaults.m, b: defaults.b, r: defaults.r, tmax: defaults.tmax,
      k: defaults.k, q: defaults.q,
      r0: 18.25, a0: 13.5, a: 52,
    });
    expect(design.guiding_curve).toEqual(defaults.guiding_curve);
  });

  it('drops OSSE-only fields, applies ICW defaults, and carries the shared horn dimensions', () => {
    useDesignStore.getState().loadDesign(configuredDesign('OSSE'));
    useDesignStore.getState().setFamily('ICW');
    const design = useDesignStore.getState().design;
    const defaults = designForFamily('ICW');

    for (const field of ['s', 'n', 'h', 'throat_profile', 'rotation', 'circ_arc_radius', 'circ_arc_term_angle']) {
      expect(design).not.toHaveProperty(field);
    }
    expect(design).toMatchObject({
      formula: 'ICW',
      L: 211, r0: 18.25, a0: 13.5,
      R: defaults.R, a: defaults.a, k: defaults.k, q: defaults.q,
      coverage_angle: defaults.coverage_angle,
      hold_start: defaults.hold_start,
      hold_end: defaults.hold_end,
      n_coeff: defaults.n_coeff,
      termination: defaults.termination,
      theta1_deg: defaults.theta1_deg,
      depth: defaults.depth,
      curl: defaults.curl,
    });
    expect(design.guiding_curve).toEqual(defaults.guiding_curve);
  });

  it('drops ICW scalars and initializes FREEFORM defaults around equivalent dimensions', () => {
    useDesignStore.getState().loadDesign(configuredDesign('ICW'));
    useDesignStore.getState().setFamily('FREEFORM');
    const design = useDesignStore.getState().design;
    const defaults = designForFamily('FREEFORM');

    for (const field of [
      'R', 'L', 'r0', 'a0', 'a', 'k', 'q', 'coverage_angle', 'hold_start',
      'hold_end', 'n_coeff', 'termination', 'theta1_deg', 'depth', 'curl',
    ]) {
      expect(design).not.toHaveProperty(field);
    }
    expect(design).toMatchObject({
      formula: 'FREEFORM',
      length: 233,
      cross_sections: defaults.cross_sections,
      inflection_policy: defaults.inflection_policy,
      corner_grids: defaults.corner_grids,
    });
    expect(design.profile_h?.points).toEqual([{ t: 0, r: 19.5 }, { t: 1, r: 177 }]);
    expect(design.profile_v?.points).toEqual([{ t: 0, r: 19.5 }, { t: 1, r: 177 }]);
    expect(design.profile_h?.throat_angle_deg).toBe(14.25);
    expect(design.profile_v?.throat_angle_deg).toBe(14.25);
    expect(design.profile_h?.mouth_angle_deg).toBe(defaults.profile_h?.mouth_angle_deg);
    expect(design.profile_v?.mouth_angle_deg).toBe(defaults.profile_v?.mouth_angle_deg);
  });

  it('maps R-OSSE mouth and throat dimensions into the FREEFORM defaults', () => {
    const configured = configuredDesign('R-OSSE');
    configured.R = 188;
    configured.r0 = 20.5;
    configured.a0 = 16.25;
    configured.a = 48;
    useDesignStore.getState().loadDesign(configured);
    useDesignStore.getState().setFamily('FREEFORM');

    const design = useDesignStore.getState().design;
    const defaults = designForFamily('FREEFORM');
    expect(design.length).toBe(defaults.length);
    expect(design.profile_h?.points).toEqual([{ t: 0, r: 20.5 }, { t: 1, r: 188 }]);
    expect(design.profile_v?.points).toEqual([{ t: 0, r: 20.5 }, { t: 1, r: 188 }]);
    expect(design.profile_h).toMatchObject({ throat_angle_deg: 16.25, mouth_angle_deg: 48 });
    expect(design.profile_v).toMatchObject({ throat_angle_deg: 16.25, mouth_angle_deg: 48 });
  });

  it('keeps FREEFORM mouth-angle defaults and drops the expression when the OSSE value exceeds the target range', () => {
    const configured = designForFamily('OSSE');
    configured.a = 120;
    configured._expressions = {
      a: { value: 120, raw: '60 * 2' },
    };
    useDesignStore.getState().loadDesign(configured);
    useDesignStore.getState().setFamily('FREEFORM');

    const design = useDesignStore.getState().design;
    const defaults = designForFamily('FREEFORM');
    expect(design.profile_h?.mouth_angle_deg).toBe(defaults.profile_h?.mouth_angle_deg);
    expect(design.profile_v?.mouth_angle_deg).toBe(defaults.profile_v?.mouth_angle_deg);
    expect(design.profile_h?.mouth_angle_deg).not.toBe(120);
    expect(design.profile_v?.mouth_angle_deg).not.toBe(120);
    expect(design._expressions?.['profile_h.mouth_angle_deg']).toBeUndefined();
    expect(design._expressions?.['profile_v.mouth_angle_deg']).toBeUndefined();
  });

  it('keeps the FREEFORM length default when the OSSE value is below the target range', () => {
    const configured = designForFamily('OSSE');
    configured.L = 10;
    useDesignStore.getState().loadDesign(configured);
    useDesignStore.getState().setFamily('FREEFORM');

    const design = useDesignStore.getState().design;
    expect(design.length).toBe(designForFamily('FREEFORM').length);
    expect(design.length).not.toBe(10);
  });

  it('carries OSSE mouth angle and length when both fit the FREEFORM target ranges', () => {
    const configured = designForFamily('OSSE');
    configured.a = 45;
    configured.L = 130;
    useDesignStore.getState().loadDesign(configured);
    useDesignStore.getState().setFamily('FREEFORM');

    const design = useDesignStore.getState().design;
    expect(design.length).toBe(130);
    expect(design.profile_h?.mouth_angle_deg).toBe(45);
    expect(design.profile_v?.mouth_angle_deg).toBe(45);
  });

  it.each([
    ['OSSE', 'ICW', 120],
    ['ICW', 'OSSE', .5],
  ] as const)('evaluates L = 0.5 against the %s → %s target range', (from, to, expected) => {
    const configured = designForFamily(from);
    configured.L = .5;
    useDesignStore.getState().loadDesign(configured);
    useDesignStore.getState().setFamily(to);

    expect(useDesignStore.getState().design.L).toBe(expected);
  });

  it('carries equal FREEFORM values while dropping ambiguous raw expressions', () => {
    const configured = configuredDesign('FREEFORM');
    configured.length = 247;
    configured.profile_h!.points[0].r = 21;
    configured.profile_v!.points[0].r = 21;
    configured.profile_h!.throat_angle_deg = 12.5;
    configured.profile_v!.throat_angle_deg = 12.5;
    configured.profile_h!.mouth_angle_deg = 49;
    configured.profile_v!.mouth_angle_deg = 49;
    configured._expressions = {
      'profile_h.points.0.r': { value: 21, raw: '42 / 2' },
      'profile_v.points.0.r': { value: 21, raw: '21' },
    };
    useDesignStore.getState().loadDesign(configured);
    useDesignStore.getState().setFamily('OSSE');

    const design = useDesignStore.getState().design;
    for (const field of ['length', 'profile_h', 'profile_v', 'cross_sections', 'inflection_policy', 'corner_grids']) {
      expect(design).not.toHaveProperty(field);
    }
    expect(design).toMatchObject({ formula: 'OSSE', L: 247, r0: 21, a0: 12.5, a: 49 });
    expect(design._expressions).toBeUndefined();
  });

  it('keeps shared sidecars while preventing outgoing expressions from recreating formula fields', () => {
    const configured = configuredDesign('OSSE');
    configured._expressions = {
      'mesh.mouth_resolution': { value: 3.75, raw: '15 / 4' },
      L: { value: 211, raw: '200 + 11' },
      r0: { value: 18.25, raw: '36.5 / 2' },
      k: { value: 9, raw: '3 * 3' },
    };
    configured._absent = ['mesh.max_edge', 's'];
    useDesignStore.getState().loadDesign(configured);
    useDesignStore.getState().setFamily('R-OSSE');

    const design = useDesignStore.getState().design;
    expect(design._expressions).toEqual({
      'mesh.mouth_resolution': { value: 3.75, raw: '15 / 4' },
      r0: { value: 18.25, raw: '36.5 / 2' },
    });
    expect(design._absent).toEqual(['mesh.max_edge']);
    const payload = serializeDesign(design);
    expect(payload).not.toHaveProperty('L');
    expect(payload).not.toHaveProperty('s');
    expect(payload.k).toBe(designForFamily('R-OSSE').k);
  });

  it('uses target defaults when FREEFORM axes disagree on a scalar dimension', () => {
    const configured = designForFamily('FREEFORM');
    configured.length = 246;
    configured.profile_h!.points[0].r = 11;
    configured.profile_v!.points[0].r = 12;
    configured.profile_h!.points.at(-1)!.r = 171;
    configured.profile_v!.points.at(-1)!.r = 172;
    configured.profile_h!.throat_angle_deg = 13;
    configured.profile_v!.throat_angle_deg = 14;
    useDesignStore.getState().loadDesign(configured);
    useDesignStore.getState().setFamily('ICW');

    const design = useDesignStore.getState().design;
    const defaults = designForFamily('ICW');
    expect(design.L).toBe(246);
    expect(design.r0).toBe(defaults.r0);
    expect(design.a0).toBe(defaults.a0);
    expect(design.R).toBe(defaults.R);
  });
});
