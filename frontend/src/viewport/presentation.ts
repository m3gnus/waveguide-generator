import type { ConnectionState } from '../api/previewSocket';
import type { DecodedFrame } from '../api/frame';
import type { DesignDocument } from '../stores/design';

export interface PreviewBadge {
  label: string;
  className: 'paused-badge' | 'reconnect-badge' | 'error-badge' | 'stale-badge' | 'live-badge';
}

export function previewBadge(
  liveUpdate: boolean,
  connection: ConnectionState,
  error: string | null,
  stale: boolean,
): PreviewBadge {
  if (!liveUpdate) return { label: 'PAUSED', className: 'paused-badge' };
  if (connection !== 'connected') return { label: connection.toUpperCase(), className: 'reconnect-badge' };
  if (error) return { label: 'ERROR', className: 'error-badge' };
  if (stale) return { label: 'STALE', className: 'stale-badge' };
  return { label: 'LIVE', className: 'live-badge' };
}

export function previewErrorMessage(
  error: string,
  errorRevision: number | null = null,
  currentRevision: number | null = null,
): string {
  // An error raised by an edit the user has already moved past is still worth
  // showing — it explains why the viewport stopped following them — but it must
  // say which edit it came from rather than implying the current one failed.
  const superseded = errorRevision !== null && currentRevision !== null && errorRevision !== currentRevision;
  const attempt = superseded ? `Preview failed at revision r${errorRevision}` : 'Preview failed';
  return `${attempt}: ${error}. Displayed geometry is not the current design.`;
}

/** Why the viewport is not showing the current design, in one plain sentence. */
export function staleReason(
  liveUpdate: boolean,
  connection: ConnectionState,
  error: string | null,
): string {
  if (!liveUpdate) return 'Live updates are paused. Refresh to request this design once.';
  if (connection !== 'connected') return `Preview engine ${connection}. The viewport resumes on reconnect.`;
  if (error) return 'The last preview attempt failed. Refresh to try this design again.';
  return 'Waiting for the preview engine to return this design. Refresh to ask again.';
}

function finitePositive(value: number | undefined): value is number {
  return value !== undefined && Number.isFinite(value) && value > 0;
}

function millimetres(value: number): string {
  return Number(value.toFixed(1)).toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function mouthClause(design: DesignDocument): string | null {
  if ((design.formula === 'R-OSSE' || design.formula === 'ICW') && finitePositive(design.R)) {
    return `Ø ${millimetres(design.R * 2)} mm`;
  }
  if (design.formula === 'FREEFORM') {
    const horizontal = design.profile_h?.points.at(-1)?.r;
    const vertical = design.profile_v?.points.at(-1)?.r;
    if (finitePositive(horizontal) && finitePositive(vertical)) {
      const width = millimetres(horizontal * 2);
      const height = millimetres(vertical * 2);
      return width === height ? `Ø ${width} mm` : `${width} × ${height} mm`;
    }
  }
  return null;
}

function symmetryClause(quadrants: number[]): string {
  if (quadrants.length === 4) return 'full';
  if (quadrants.length === 2) return 'half';
  if (quadrants.length === 1) return 'quarter';
  return `${quadrants.length}/4`;
}

export function viewportSubtitle(design: DesignDocument): string {
  return [design.formula, mouthClause(design), symmetryClause(design.quadrants)]
    .filter((clause): clause is string => clause !== null)
    .join(' · ');
}

/**
 * Vertex and triangle totals for the frame currently on screen.
 *
 * Read from the length of the decoded typed arrays rather than by walking them,
 * so this stays O(1) per surface and is safe to call on every frame while a
 * parameter is being scrubbed.
 *
 * The status rail used to state "preview mesh metrics unavailable" as a literal,
 * unconditional string. The metrics were never unavailable -- the frame the
 * viewport is drawing carries them -- so the rail was reporting a fact about
 * itself rather than about the geometry.
 */
export function previewMeshMetrics(frame: DecodedFrame | null): { vertices: number; triangles: number } | null {
  const surfaces = frame?.header.surfaces;
  if (!frame || !surfaces?.length) return null;
  let vertices = 0;
  let triangles = 0;
  for (const surface of surfaces) {
    vertices += (frame.sections[surface.positions]?.length ?? 0) / 3;
    triangles += (frame.sections[surface.indices]?.length ?? 0) / 3;
  }
  if (!vertices && !triangles) return null;
  return { vertices: Math.floor(vertices), triangles: Math.floor(triangles) };
}
