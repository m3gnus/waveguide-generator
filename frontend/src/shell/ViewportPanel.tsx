import { lazy, Suspense, useEffect } from 'react';
import { previewSocket } from '../api/previewSocket';
import { viewerPreferences } from '../viewerprefs/viewerPreferences';

/**
 * three.js, @react-three/fiber and drei are ~918 kB raw of the SPA's 1.65 MB,
 * and nothing outside this panel touches them. Loading them on demand lets the
 * rest of the shell -- parameter rail, results, jobs -- parse and paint without
 * waiting for the renderer, the same split `results/EChart.tsx` already makes.
 *
 * The preview socket below deliberately stays eager: it starts connecting and
 * requesting geometry while this chunk is still in flight, so the split costs
 * no time to first frame.
 */
const LazyViewport = lazy(async () => {
  const module = await import('../viewport/Viewport');
  return { default: module.Viewport };
});

export function ViewportPanel() {
  useEffect(() => {
    let active = true;
    const applyLiveUpdateGate = () => queueMicrotask(() => {
      if (!active) return;
      if (viewerPreferences.getSnapshot().liveUpdate) previewSocket.start();
      else previewSocket.stop();
    });
    applyLiveUpdateGate();
    const unsubscribe = viewerPreferences.subscribe(applyLiveUpdateGate);
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  return <Suspense fallback={<div className="viewport-panel wg2-viewport" role="status" aria-label="Viewport loading" />}>
    <LazyViewport />
  </Suspense>;
}
