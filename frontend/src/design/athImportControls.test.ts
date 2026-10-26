import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { athControlError, lipRadiusWallError, lipUnavailableReason, rosseMorphNote } from './athImportControls';

describe('ATH import wall default and controls', () => {
  it('keeps ATH\'s 5 mm wall for an unset wall through lip/rear edits without saving it', () => {
    resetDesignStore();
    useDesignStore.getState().replaceDocument(hydrateDesignDocument({ formula: 'OSSE', text_import_version: 'ath-2026-09a-v2', enclosure: null, mesh: { wall_thickness: null } }));
    const state = () => useDesignStore.getState();
    // ATH's parser defaults an omitted wall to 5 mm, which is also what WG sends the mesher.
    expect(state().design.mesh.wall_thickness).toBe(5);
    state().updateValue('mesh.ath_roundover', { radius: 10, segments: 5 });
    expect(state().design.mesh.wall_thickness).toBe(5);
    expect(serializeDesign(state().design).mesh).toMatchObject({ wall_thickness: null });
    state().updateValue('mesh.ath_rear_shape', 3);
    expect(state().design.mesh.wall_thickness).toBe(5);
    state().updateValue('mesh.wall_thickness', 12);
    state().updateValue('mesh.ath_roundover', { radius: 20, segments: 5 });
    expect(state().design.mesh.wall_thickness).toBe(12);
    expect(serializeDesign(state().design).mesh).toMatchObject({ wall_thickness: 12 });
  });

  it.each(['mesh.ath_roundover.radius', 'mesh.ath_roundover.segments', 'mesh.ath_rear_front_offset', 'mesh.ath_rear_step'])('rejects nonfinite %s', (path) => {
    for (const value of [NaN, Infinity, -Infinity]) expect(athControlError(path, value)).toContain('finite');
  });

  it('permits the inclusive offset and segment bounds and does not invent upper bounds', () => {
    expect(athControlError('mesh.ath_roundover.radius', 1e8)).toBeUndefined();
    expect(athControlError('mesh.ath_roundover.segments', 1)).toBeUndefined();
    expect(athControlError('mesh.ath_rear_front_offset', 0)).toBeUndefined();
    expect(athControlError('mesh.ath_rear_step', 1e8)).toBeUndefined();
  });
});

describe('ATH lip availability and radius/wall rule', () => {
  const load = (wire: Record<string, unknown>) => hydrateDesignDocument({ formula: 'OSSE', text_import_version: 'ath-2026-09a-v2', enclosure: null, ...wire });

  it('reports the mesher refusals and nothing for a supported design', () => {
    expect(lipUnavailableReason(load({}))).toBeUndefined();
    expect(lipUnavailableReason(load({ formula: 'R-OSSE' }))).toBe('ATH builds the mouth lip on OSSE horns only.');
    expect(lipUnavailableReason(load({ simulation: { sim_type: 'infinite-baffle' } }))).toContain('free-standing or bare');
    expect(lipUnavailableReason(load({ enclosure: { depth: 100 } }))).toContain('free-standing or bare');
    expect(lipUnavailableReason(load({ mesh: { length_segments: 2 } }))).toContain('at least 3 length segments');
  });

  it('applies Radius > WallThickness only to a lip over a full rear on a free-standing wall', () => {
    const lip = { ath_roundover: { radius: 10, segments: 5 }, wall_thickness: 6 };
    expect(lipRadiusWallError(load({ mesh: lip }))).toBeUndefined();
    expect(lipRadiusWallError(load({ mesh: lip }), { radius: 6 })).toContain('Radius > Mesh.WallThickness');
    expect(lipRadiusWallError(load({ mesh: lip }), { wall: 10 })).toContain('Radius > Mesh.WallThickness');
    expect(lipRadiusWallError(load({ mesh: { ...lip, ath_rear_shape: 3 } }), { wall: 10 })).toBeUndefined();
    expect(lipRadiusWallError(load({ mesh: { ...lip, wall_thickness: 0 } }), { radius: 1 })).toBeUndefined();
    expect(lipRadiusWallError(load({ mesh: lip, simulation: { sim_type: 'infinite-baffle' } }), { wall: 10 })).toBeUndefined();
  });
});

describe('R-OSSE morph where ATH builds none', () => {
  const load = (wire: Record<string, unknown>) => hydrateDesignDocument({ formula: 'R-OSSE', text_import_version: 'ath-2026-09a-v2', enclosure: null, ...wire });

  it('notes a Rectangle on a length-varying horn and any Superellipse, and nothing else', () => {
    expect(rosseMorphNote(load({ morph: { target_shape: 1 } }))).toBeUndefined();
    expect(rosseMorphNote(load({ morph: { target_shape: 1 }, R: { value: 150, raw: '150 - 20*cos(p)^2', dialect: 'ath' } }))).toContain('its own R-OSSE morph');
    expect(rosseMorphNote(load({ morph: { target_shape: 1 }, k: { value: 2, raw: '4 * abs(cos(p))', dialect: 'ath' } }))).toContain('depth changes');
    expect(rosseMorphNote(load({ morph: { target_shape: 1 }, a0: { value: 10, raw: '10 + 5*cos(p)^2', dialect: 'ath' } }))).toContain('depth changes');
    expect(rosseMorphNote(load({ morph: { target_shape: 1 }, q: { value: 3, raw: '3 + cos(p)^2', dialect: 'ath' } }))).toBeUndefined();
    expect(rosseMorphNote(load({ morph: { target_shape: 3 } }))).toContain('Superellipse');
    expect(rosseMorphNote(load({ morph: { target_shape: 2 }, R: { value: 150, raw: '150 - 20*cos(p)^2', dialect: 'ath' } }))).toBeUndefined();
    expect(rosseMorphNote(hydrateDesignDocument({ formula: 'R-OSSE', morph: { target_shape: 3 } }))).toBeUndefined();
  });
});
