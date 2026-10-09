import type { SourceDocument, SourcePatch, SourcePoint } from '../api/sourceEditor';

export const INITIAL_SOURCE: SourceDocument = {
  contour: { version: 1, physical_source_id: 'diaphragm', rim_id: 'source.rim',
    points: [{ id: 'pole', r_mm: 0, z_mm: 0 }, { id: 'rim', r_mm: 8, z_mm: 0 }],
    segments: [{ id: 'piston', start: 'pole', end: 'rim', role: 'moving', kind: 'line', center_mm: null, direction: 'ccw' }],
  }, drive: { channel_id: 'motor', weights: { piston: 1 }, motion: 'normal' },
};

export function editPoint(document: SourceDocument, index: number, r_mm: number, z_mm: number): SourceDocument {
  const next = structuredClone(document);
  next.contour.points[index] = { ...next.contour.points[index], r_mm: index === 0 ? 0 : r_mm, z_mm: index === next.contour.points.length - 1 ? 0 : z_mm };
  return next;
}

export function editPatch(document: SourceDocument, index: number, changes: Partial<SourcePatch>): SourceDocument {
  const next = structuredClone(document);
  const patch = { ...next.contour.segments[index], ...changes };
  if (patch.kind === 'line') { patch.center_mm = null; patch.direction = 'ccw'; }
  else if (!patch.center_mm) {
    const a = next.contour.points[index]; const b = next.contour.points[index + 1];
    patch.center_mm = [(a.r_mm + b.r_mm) / 2, (a.z_mm + b.z_mm) / 2];
  }
  next.contour.segments[index] = patch;
  if (patch.role === 'rigid') delete next.drive.weights[patch.id];
  else next.drive.weights[patch.id] ??= 1;
  return next;
}

/** Drawing is an explicit polyline, never a curve fit or inferred patch role. */
export function drawnSource(document: SourceDocument, points: SourcePoint[], newId: () => string): SourceDocument {
  if (points.length < 2) throw new Error('Draw at least a pole and rim.');
  const next = structuredClone(document);
  next.contour.points = points.map((point, i) => ({ ...point, r_mm: i === 0 ? 0 : point.r_mm, z_mm: i === points.length - 1 ? 0 : point.z_mm }));
  next.contour.segments = next.contour.points.slice(1).map((point, i) => ({ id: newId(), start: next.contour.points[i].id, end: point.id, kind: 'line', role: 'moving', center_mm: null, direction: 'ccw' }));
  next.drive.weights = Object.fromEntries(next.contour.segments.map((patch) => [patch.id, 1]));
  return next;
}

/** Explicit removal merges only two lines, keeping the left patch identity and drive. */
export function removePoint(document: SourceDocument, index: number): SourceDocument {
  if (index <= 0 || index >= document.contour.points.length - 1) throw new Error('Pole and rim cannot be removed.');
  const left = document.contour.segments[index - 1]; const right = document.contour.segments[index];
  if (left.kind !== 'line' || right.kind !== 'line' || left.role !== right.role || document.drive.weights[left.id] !== document.drive.weights[right.id]) {
    throw new Error('Only adjoining lines with the same role and weight can merge.');
  }
  const next = structuredClone(document);
  next.contour.points.splice(index, 1);
  next.contour.segments[index - 1].end = right.end;
  next.contour.segments.splice(index, 1);
  delete next.drive.weights[right.id];
  return next;
}
