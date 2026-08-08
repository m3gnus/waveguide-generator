import { lazy, Suspense } from 'react';

/**
 * three.js, @react-three/fiber and drei are ~918 kB raw of the SPA's 1.65 MB,
 * and nothing outside this panel touches them. Loading them on demand lets the
 * rest of the shell -- parameter rail, results, jobs -- parse and paint without
 * waiting for the renderer, the same split `results/EChart.tsx` already makes.
 *
 * When live updates are enabled, the preview socket owned by Shell deliberately
 * stays eager: it starts connecting and requesting geometry while this chunk is
 * still in flight, so the split costs no time to first frame.
 */
const LazyViewport = lazy(async () => {
  const module = await import('../viewport/Viewport');
  return { default: module.Viewport };
});

export function ViewportPanel() {
  return <Suspense fallback={<div className="viewport-panel wg2-viewport" role="status" aria-label="Viewport loading" />}>
    <LazyViewport />
  </Suspense>;
}
