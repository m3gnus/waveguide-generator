/**
 * `?sketchPerf=1`: per-frame drag timings for the sketch canvas.
 *
 * Development builds only, so a packaged app never records timings (or reopens
 * the hidden sketch editor) because someone added a query parameter.
 */
export function sketchPerfEnabled(): boolean {
  return import.meta.env.DEV && typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).get('sketchPerf') === '1';
}
