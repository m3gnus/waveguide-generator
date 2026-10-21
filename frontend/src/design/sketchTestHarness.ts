import type { SourceDocument } from '../api/sourceEditor';

// Development builds only: the packaged app must not reopen the hidden sketch editor from a URL.
export const sketchPerfEnabled = () => import.meta.env.DEV && new URLSearchParams(window.location.search).get('sketchPerf') === '1';
const painted = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
let loader: ((document: SourceDocument) => Promise<void>) | undefined;
const waiting = new Set<() => void>();
const ready = () => loader ? Promise.resolve() : new Promise<void>(resolve => waiting.add(resolve));
export function registerSketchLoader(load: (document: SourceDocument) => Promise<void>) {
  loader = load; waiting.forEach(resolve => resolve()); waiting.clear();
  return () => { if (loader === load) loader = undefined; };
}
declare global {
  interface Window {
    __sketchTest?: {
      openEditor(): Promise<void>;
      loadDocument(document: SourceDocument): Promise<void>;
      pointScreen(id: string): { x: number; y: number };
    };
  }
}
export function installSketchTest(open: () => void) {
  if (!sketchPerfEnabled()) return;
  const hook = {
    async openEditor() { open(); await ready(); await painted(); },
    async loadDocument(document: SourceDocument) { await hook.openEditor(); await loader!(document); await painted(); },
    pointScreen(id: string) {
      const point = [...document.querySelectorAll<SVGCircleElement>('.sketch-point')].find(p => p.dataset.id === id);
      if (!point) throw new Error(`No point handle: ${id}`);
      const rect = point.getBoundingClientRect(); return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    },
  };
  window.__sketchTest = hook;
  return () => { if (window.__sketchTest === hook) delete window.__sketchTest; };
}
