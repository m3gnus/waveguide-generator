import type { ImportedMeshScene } from './importedMesh';

/** The horn wall's preview record, which describes what is on screen and not the solve mesh. */
export interface PreviewSurfaceRecord {
  surface_deviation?: { peak_mm?: number | null; measurement_complete?: boolean } | null;
}

export type MeshReportStatus = 'current' | 'building' | 'stale';

const mm = (value: number) => `${value.toFixed(3)} mm`;

/** Null while the preview has no complete measurement: nothing is better than a guess. */
export function previewSurfaceNote(record: PreviewSurfaceRecord | undefined): string | null {
  const deviation = record?.surface_deviation;
  if (deviation?.peak_mm == null || deviation.measurement_complete === false) return null;
  return `Preview surface within ${mm(deviation.peak_mm)} of the horn. Max surface deviation does not change it; the Mesh view reports the solve mesh.`;
}

interface ReportRow { label: string; value: string }

/** What a built mesh's report says, with each number's meaning in words. */
export function meshReport(mesh: ImportedMeshScene): { title: string; rows: ReportRow[]; notes: string[] } {
  const { tessellation, source, triangleCount, solvedTriangleCount } = mesh;
  const deviation = tessellation?.surface_deviation;
  const shape = tessellation?.element_shape;
  const rows: ReportRow[] = [];
  const notes: string[] = [];
  const target = tessellation?.surface_deviation_mm;
  if (target !== undefined) rows.push({ label: 'Deviation target', value: target === null ? 'Off' : mm(target) });
  rows.push({
    label: 'Triangles',
    value: triangleCount === solvedTriangleCount ? solvedTriangleCount.toLocaleString()
      : `${solvedTriangleCount.toLocaleString()} solved`,
  });
  if (deviation?.peak_mm == null) rows.push({ label: 'Deviation', value: 'not measured' });
  else if (deviation.peak_is_upper_bound) {
    rows.push({ label: 'Deviation', value: `at most ${mm(deviation.peak_mm)}` });
    notes.push('A bound that holds across the whole mesh.');
  } else {
    rows.push({ label: 'Deviation', value: `at least ${mm(deviation.peak_mm)}` });
    const sampled = deviation.sampled_triangle_count ?? 0;
    const total = deviation.triangle_count ?? 0;
    notes.push(`The highest of ${sampled.toLocaleString()} sampled triangles${total > sampled ? ` out of ${total.toLocaleString()}` : ''}, so the true peak can be higher.`
      + ` Measured against the CAD ${source === 'solver' ? 'surface the mesh is built from, not the horn formula' : 'model the mesh is built from'}.`);
  }
  const percentile = shape?.p1_radius_ratio;
  const worst = percentile ?? shape?.min_radius_ratio;
  if (worst == null) rows.push({ label: 'Element shape', value: 'not measured' });
  else {
    rows.push({ label: 'Element shape', value: worst.toFixed(2) });
    notes.push(`${percentile != null ? 'The worst 1% of triangles' : 'The worst triangle'}: 1 is equilateral, lower is more stretched.`);
  }
  return { title: source === 'solver' ? 'Solve mesh' : 'Mesh', rows, notes };
}

const STATUS_WORDS: Record<Exclude<MeshReportStatus, 'current'>, string> = { building: 'rebuilding…', stale: 'out of date' };

export function MeshReportCard({ mesh, status }: { mesh: ImportedMeshScene; status: MeshReportStatus }) {
  const { title, rows, notes } = meshReport(mesh);
  return <div className={`frame-stat-card mesh-quality-card${status === 'current' ? '' : ' stale'}`} role="group" aria-label={`${title} report`}>
    <header><b>{title}</b>{status !== 'current' && <span>{STATUS_WORDS[status]}</span>}</header>
    <dl>{rows.map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {notes.map((note) => <p key={note}>{note}</p>)}
  </div>;
}
