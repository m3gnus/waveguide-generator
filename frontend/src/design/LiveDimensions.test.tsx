import { readFileSync } from 'node:fs';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FrameHeader } from '../api/frame';
import type { PreviewSnapshot } from '../api/previewSocket';

const preview = vi.hoisted(() => ({
  listeners: new Set<() => void>(),
  snapshot: {} as PreviewSnapshot,
}));
vi.mock('../api/previewSocket', () => ({ previewSocket: {
  getSnapshot: () => preview.snapshot,
  subscribe: (listener: () => void) => { preview.listeners.add(listener); return () => preview.listeners.delete(listener); },
} }));
import { resetDesignStore, useDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { LiveDimensions } from './LiveDimensions';
import { currentDocumentLoad } from '../stores/design';
import { ParamPanel } from './ParamPanel';

// Captured verbatim from the producer mesher build_preview_geometry(OSSE enclosure, fine).
const metadata = JSON.parse(new TextDecoder().decode(readFileSync('../shared/preview-fixtures/c2-dimensions-metadata.json'))) as NonNullable<FrameHeader['previewMetadata']>;

function frame(data: FrameHeader['previewMetadata'], revision = 1, lod: 'coarse' | 'fine' = 'coarse') {
  return { header: { v: 1 as const, kind: 'preview' as const, designRevision: revision,
    previewMetadata: data, lod, sections: [] }, sections: {} };
}

describe('live design dimensions', () => {
  let host: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear(); resetDesignStore(); workspaceModeStore.setMode('parametric');
    preview.snapshot = { connection: 'connected', epoch: 3, frame: null, displayedRevision: null,
      lastValidRevision: null, stale: true, dropped: 0, error: null, errorFields: null, errorRevision: null };
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); queryClient.clear(); preview.listeners.clear(); });
  function render(tab: 'geometry' | 'simulation' = 'geometry') {
    act(() => root.render(<QueryClientProvider client={queryClient}><div className="wg2-viewport"><LiveDimensions /></div><ParamPanel tab={tab} /></QueryClientProvider>));
  }
  function publish(patch: Partial<PreviewSnapshot>) {
    act(() => { preview.snapshot = { ...preview.snapshot, ...patch }; preview.listeners.forEach((listener) => listener()); });
  }
  function valid(data = metadata) {
    publish({ frame: frame(data), displayedRevision: 1, lastValidRevision: 1, stale: false });
  }
  const dimensions = () => host.querySelector('[data-testid="design-dimensions"]');
  const expectedMouth = metadata.dimensions_mm!.mouth_opening!.map((v) => v.toFixed(1)).join(' × ');
  it('hides all readouts when the pinned mesher has no dimension keys', () => {
    render(); valid({ warnings: [] });
    expect(dimensions()).toBeNull();
  });
  it('shows the standalone source and body dimensions without horn datums', () => {
    act(() => useDesignStore.getState().setFamily('SOURCE-DISK'));
    render();
    const revision = useDesignStore.getState().designRevision;
    publish({ frame: frame({ dimensions_mm: { source_diameter: [36, 36], body_overall: [44, 44, 8] } }, revision),
      displayedRevision: revision, lastValidRevision: revision, stale: false });
    expect(dimensions()?.textContent).toContain('Source 36.0 × 36.0');
    expect(dimensions()?.textContent).toContain('Body 44.0 × 44.0 × 8.0');
    expect(dimensions()?.textContent).not.toContain('Mouth ');
    expect(dimensions()?.textContent).not.toContain('Overall ');
  });
  it('displays the exact emitted W×H and W×H×D contract and preserves coarse/fine values', () => {
    render(); valid();
    expect(dimensions()?.textContent).toContain(expectedMouth);
    expect(dimensions()?.textContent).not.toContain('Overall ');
    expect(dimensions()?.textContent).toContain(`Depth ${metadata.dimensions_mm!.horn_overall![2].toFixed(1)}`);
    expect(dimensions()?.textContent).toContain('Enclosure ');
    expect(dimensions()?.getAttribute('title')).toContain('Current preview');
    expect(dimensions()?.textContent).not.toContain('revision');
    const before = dimensions()?.textContent;
    publish({ frame: frame(metadata, 1, 'fine') });
    expect(dimensions()?.textContent).toBe(before);
  });
  it('retains this document canonical values while a coarse measurement is pending', () => {
    render(); valid();
    const pending = JSON.parse(new TextDecoder().decode(readFileSync('../shared/preview-fixtures/c2-dimensions-pending-metadata.json')));
    publish({ frame: frame(pending), lastCanonicalDimensions: metadata.dimensions_mm });
    expect(dimensions()?.textContent).toContain(expectedMouth);
    expect(dimensions()?.getAttribute('title')).toContain('Updating dimensions');
    expect(dimensions()?.getAttribute('title')).not.toContain('Current preview');
    publish({ frame: frame(metadata, 1, 'fine') });
    expect(dimensions()?.getAttribute('title')).toContain('Current preview');
    expect(dimensions()?.textContent).not.toMatch(/approx|tolerance|~|≈/i);
    publish({ frame: frame({ dimensions_mm: null, dimensions_status: 'unavailable', dimensions_error: 'Invalid geometry' }) });
    expect(dimensions()?.getAttribute('title')).toContain('unavailable');
    expect(dimensions()?.textContent).not.toContain(expectedMouth);
  });
  it('shows updating with no values on a new document pending frame', () => {
    render();
    valid({ dimensions_mm: null, dimensions_status: 'pending' });
    expect(dimensions()?.getAttribute('title')).toContain('Updating dimensions');
    expect(dimensions()?.textContent).not.toContain(expectedMouth);
  });
  it('omits the enclosure readout for a free horn', () => {
    render(); const free = structuredClone(metadata); delete free.dimensions_mm!.enclosure_overall; valid(free);
    expect(dimensions()?.textContent).not.toContain('Enclosure ');
  });
  it('labels design size and shows a floored morph request beside its effective aperture', () => {
    render();
    const floored = structuredClone(metadata);
    floored.dimensions_requested_mm = { mouth_opening: [320, 240] };
    valid(floored);
    expect(dimensions()?.getAttribute('aria-label')).toContain('Design dimensions');
    expect(dimensions()?.textContent).not.toContain('requested');
    expect(dimensions()?.getAttribute('title')).toContain(`Mouth requested 320.0 × 240.0 mm, effective ${expectedMouth} mm.`);
    floored.dimensions_requested_mm = { mouth_opening: floored.dimensions_mm!.mouth_opening };
    valid(floored);
    expect(dimensions()?.textContent).toContain(expectedMouth);
    expect(dimensions()?.getAttribute('title')).not.toContain('requested');
  });
  it('labels the accepted older frame after a new edit and a matching failure', () => {
    render(); valid();
    act(() => useDesignStore.setState({ designRevision: 2 }));
    publish({ stale: true, error: 'Invalid profile', errorRevision: 2 });
    expect(dimensions()?.textContent).toContain(expectedMouth);
    expect(dimensions()?.getAttribute('title')).toContain('Last valid preview');
    expect(dimensions()?.classList.contains('stale')).toBe(true);
    expect(dimensions()?.getAttribute('aria-label')).toContain('Last valid preview');
    expect(dimensions()?.textContent).not.toContain('Last valid preview');
    publish({ frame: frame(metadata, 2), displayedRevision: 2, stale: false, error: null, errorRevision: null });
    expect(dimensions()?.getAttribute('title')).toContain('Current preview');
  });
  it('labels values last valid beside an invalid uncommitted NumberField draft', async () => {
    act(() => useDesignStore.getState().updateValue('morph.target_shape', 1));
    render(); valid();
    const input = host.querySelector<HTMLInputElement>('[data-parameter-id="morph.corner_radius"] input')!;
    await act(async () => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '-20');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(dimensions()?.getAttribute('title')).toContain('Last valid preview');
  });
  it('shows unavailable for rejected canonical geometry and malformed values', () => {
    render(); valid({ dimensions_mm: null, dimensions_error: 'Cannot fit geometry' });
    expect(dimensions()?.getAttribute('title')).toContain('unavailable');
    const bad = structuredClone(metadata); bad.dimensions_mm!.mouth_opening = [Number.NaN, 0]; valid(bad);
    expect(dimensions()?.textContent).not.toContain('Mouth ');
    expect(dimensions()?.textContent).not.toContain('NaN');
  });
  it('keeps the readouts in the parametric viewport beside either tab', () => {
    render('simulation'); valid(); expect(dimensions()?.closest('.wg2-viewport')).not.toBeNull();
    expect(host.querySelector('.param-panel [data-testid="design-dimensions"]')).toBeNull();
    act(() => workspaceModeStore.setMode('cad')); render(); expect(dimensions()).toBeNull();
  });
  it('shows overall width and height only beyond the 0.05 mm difference threshold', () => {
    render();
    const data = structuredClone(metadata);
    data.dimensions_mm = { mouth_opening: [411.7, 320], horn_overall: [411.75, 320.05, 176.5] };
    valid(data);
    expect(dimensions()?.textContent).toBe('Mouth 411.7 × 320.0 · Depth 176.5 mm');
    expect(dimensions()?.classList.contains('stale')).toBe(false);
    data.dimensions_mm.horn_overall = [421.7, 330, 176.5]; valid(data);
    expect(dimensions()?.textContent).toBe('Mouth 411.7 × 320.0 · Depth 176.5 · Overall 421.7 × 330.0 mm');
    expect(dimensions()?.getAttribute('aria-live')).toBe('polite');
  });
  it('dims pending retained values and keeps the status in accessible metadata only', () => {
    render(); valid();
    publish({ frame: frame({ dimensions_mm: null, dimensions_status: 'pending' }), lastCanonicalDimensions: metadata.dimensions_mm });
    expect(dimensions()?.classList.contains('stale')).toBe(true);
    expect(dimensions()?.getAttribute('aria-label')).toContain('Updating dimensions');
    expect(dimensions()?.textContent).not.toContain('Updating');
  });
  it('hides previous document dimensions during New/Open', () => {
    render(); valid();
    publish({ frame: { ...frame(metadata), documentLoad: currentDocumentLoad() - 1 }, awaitingDocumentFrame: true });
    expect(dimensions()).toBeNull();
  });
});
