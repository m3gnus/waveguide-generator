import type { ConnectionState } from '../api/previewSocket';
import type { DecodedFrame } from '../api/frame';

export interface PreviewBadge {
  label: string;
  className: 'paused-badge' | 'reconnect-badge' | 'error-badge' | 'stale-badge' | 'live-badge';
}

/** The preview's state when it is not the normal one; following the design live has no badge. */
export function previewBadge(
  liveUpdate: boolean,
  connection: ConnectionState,
  error: string | null,
  stale: boolean,
): PreviewBadge | null {
  if (!liveUpdate) return { label: 'PAUSED', className: 'paused-badge' };
  if (connection !== 'connected') return { label: connection.toUpperCase(), className: 'reconnect-badge' };
  if (error) return { label: 'ERROR', className: 'error-badge' };
  if (stale) return { label: 'STALE', className: 'stale-badge' };
  return null;
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
  const attempt = superseded ? 'An earlier edit could not be shown' : 'WG could not show this edit';
  return `${attempt}: ${error.replace(/\.$/, '')}. The view still shows the last design that worked.`;
}

/** What a dismissal names: the failure itself, since different ones can share plain words. */
export function previewErrorKey(preview: { error: string | null; errorDetail?: string | null }): string | null {
  return preview.errorDetail ?? preview.error;
}

/** Why the viewport is not showing the current design, in one plain sentence. */
export function staleReason(
  liveUpdate: boolean,
  connection: ConnectionState,
  error: string | null,
): string {
  if (!liveUpdate) return 'Live updates are paused. Refresh to request this design once.';
  if (connection !== 'connected') return 'WG is reconnecting to the preview. The view follows your edits again once it is back.';
  if (error) return 'WG could not show the latest edit. Refresh to try this design again.';
  return 'WG is still drawing this design. Refresh to ask again.';
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
