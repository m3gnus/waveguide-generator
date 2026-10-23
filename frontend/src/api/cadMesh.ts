import type { CadMeshSnapshot, CadReturnIngestRecord } from './cadlink';

/** Prefer measured cost over the area estimate on curved CAD surfaces. */
export function cadMeshSizing(record: CadReturnIngestRecord): Record<string, unknown> {
  const estimate = record.sizing_estimate ?? {};
  const measured = estimate.measured;
  return measured && typeof measured === 'object'
    ? measured as Record<string, unknown> : estimate;
}

/** The snapshot owns both sizing and preparation symmetry. */
export function snapshotMeshSettings(mesh: CadMeshSnapshot) {
  return {
    meshSizes: {
      rigid_size_mm: mesh.wall_size_mm, transition_mm: mesh.grading_distance_mm,
      source_size_mm: mesh.source_size_mm, surface_groups: mesh.surface_groups,
    },
    symmetryMode: mesh.symmetry === 'off' ? 'full' as const : 'auto' as const,
  };
}
