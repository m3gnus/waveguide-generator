/**
 * The label every native source-assembly feature carries: front-baffle woofer,
 * phase-plug passages, shared horn and woofer enclosure, and the source-contour
 * editor. Their meshes are geometry-checked, but their acoustics are not yet
 * qualified against measurement, so the label says so on hover and to screen
 * readers. The features stay fully usable.
 */
export const EXPERIMENTAL_NOTE = 'Experimental: results are geometry-checked only and not yet acoustically qualified.';

export const FEM_EXPERIMENTAL_NOTE = 'Experimental: FEM region results are not yet acoustically qualified for general designs.';

export function ExperimentalBadge({ note = EXPERIMENTAL_NOTE }: { note?: string } = {}) {
  return <span className="experimental-badge" title={note} aria-label={note}>Experimental</span>;
}
