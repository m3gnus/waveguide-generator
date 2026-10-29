import type { CadMeshSnapshot, CadReturnBundle, CadReturnIngestRecord } from './cadlink';

/** Fusion owns its mesh settings; Onshape returns still use WG's controls. */
export function cadMeshIsEditable(bundle: CadReturnBundle | null): boolean {
  return bundle?.bundleOrigin === 'onshape';
}

/** Shared content policy for Simulation and its Mesh detail palette target. */
export function cadMeshDetailContent(state: {
  selectedBundle: CadReturnBundle | null;
  ingestRecord: CadReturnIngestRecord | null;
  areaDriftSourceIds: readonly string[];
  ingestStaleReason: string | null;
}) {
  const editable = cadMeshIsEditable(state.selectedBundle);
  const driftSourceIds = new Set([...state.areaDriftSourceIds, ...(state.ingestRecord?.role_findings ?? [])
    .filter((finding) => String(finding.kind).includes('area-drift'))
    .map((finding) => String(finding.source_id))]);
  const hasFem = (state.ingestRecord?.evidence?.fem_air_volumes?.length ?? 0) > 0
    || (state.ingestRecord?.evidence?.fem_regions?.length ?? 0) > 0;
  const sources = (state.selectedBundle?.sources ?? [])
    .filter((source) => editable || !source.required || driftSourceIds.has(source.id));
  return { editable, driftSourceIds, hasFem, sources,
    available: Boolean(state.selectedBundle || state.ingestRecord)
      && (editable || hasFem || sources.length > 0 || !!state.ingestStaleReason) };
}

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
