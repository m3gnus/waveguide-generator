import type { DesignDocument } from '../stores/design';

export const isAthV2 = (design: DesignDocument): boolean => design.text_import_version === 'ath-2026-09a-v2';

export function athControlError(path: string | undefined, value: number): string | undefined {
  if (path === 'mesh.ath_roundover.radius' && (!Number.isFinite(value) || value <= 0)) return 'Mesh.Roundover.Radius must be finite and > 0';
  if (path === 'mesh.ath_roundover.segments' && (!Number.isFinite(value) || value < 1 || !Number.isInteger(value))) return 'Mesh.Roundover.Segments must be a finite integer >= 1';
  if (path === 'mesh.ath_rear_front_offset') {
    if (!Number.isFinite(value)) return 'Mesh.RearShape.FrontOffset must be finite';
    if (value < 0) return 'Mesh.RearShape.FrontOffset < 0 is unsupported: negative offsets have no qualified ATH ring fit';
  }
  if (path === 'mesh.ath_rear_step' && (!Number.isFinite(value) || value <= 0)) return 'Mesh.RearShape.Step must be finite and > 0';
  return undefined;
}

export function opusMountingReason(design: DesignDocument): string | undefined {
  if (design.simulation.sim_type === 'infinite-baffle' || design.enclosure.depth > 0 || design.mesh.wall_thickness <= 0) {
    return 'Mesh.RearShape = 3 requires a free-standing wall. Change Mounting to Thickened waveguide (freestanding).';
  }
  return undefined;
}

export function opusClampNote(design: DesignDocument): string | undefined {
  if (design.mesh.ath_rear_shape !== 3 || opusMountingReason(design)) return undefined;
  const wall = design.mesh.wall_thickness;
  const clamped = Math.min(1000, Math.max(.1, wall));
  return clamped !== wall
    ? `Mesh.RearShape = 3 clamps Mesh.WallThickness to ${clamped} mm for geometry (requested ${wall} mm). The saved value stays ${wall} mm.`
    : undefined;
}

/** Why the mouth lip cannot be switched on for this design (mesher and server refusals). */
export function lipUnavailableReason(design: DesignDocument): string | undefined {
  if (design.formula === 'R-OSSE') return 'Mesh.Roundover with R-OSSE is unsupported: ATH folds the lip back.';
  if (design.simulation.sim_type === 'infinite-baffle' || design.enclosure.depth > 0) {
    return 'Mesh.Roundover requires a free-standing or bare shell. Change Mounting to Thickened waveguide (freestanding) or Bare shell.';
  }
  if (design.mesh.length_segments < 3) return 'Mesh.Roundover requires Mesh.LengthSegments >= 3 for the mouth chord angle.';
  return undefined;
}

/**
 * Mesher rule: a lip over a full (RearShape 1) free-standing wall needs
 * Radius > WallThickness. Pass the candidate radius and/or wall to test an edit.
 */
export function lipRadiusWallError(design: DesignDocument, candidate: { radius?: number; wall?: number } = {}): string | undefined {
  const lip = design.mesh.ath_roundover;
  if (!isAthV2(design) || !lip || design.mesh.ath_rear_shape === 3) return undefined;
  if (design.simulation.sim_type === 'infinite-baffle' || design.enclosure.depth > 0) return undefined;
  const wall = candidate.wall ?? design.mesh.wall_thickness;
  const radius = candidate.radius ?? lip.radius;
  if (wall > 0 && wall >= radius) return 'Mesh.Roundover with RearShape = 1 requires Radius > Mesh.WallThickness: the outer lip would collapse or fold';
  return undefined;
}
