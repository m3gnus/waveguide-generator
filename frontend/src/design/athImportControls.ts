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
