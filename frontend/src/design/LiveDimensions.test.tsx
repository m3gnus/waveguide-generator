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
import { ParamPanel } from './ParamPanel';

// Captured verbatim from the producer mesher build_preview_geometry(OSSE enclosure, coarse).
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
    act(() => root.render(<QueryClientProvider client={queryClient}><ParamPanel tab={tab} /></QueryClientProvider>));
  }
  function publish(patch: Partial<PreviewSnapshot>) {
    act(() => { preview.snapshot = { ...preview.snapshot, ...patch }; preview.listeners.forEach((listener) => listener()); });
  }
  function valid(data = metadata) {
    publish({ frame: frame(data), displayedRevision: 1, lastValidRevision: 1, stale: false });
  }
  const dimensions = () => host.querySelector('[aria-label="Design dimensions"]');
  const expectedMouth = metadata.dimensions_mm!.mouth_opening!.map((v) => v.toFixed(1)).join(' × ') + ' mm';
  it('hides all readouts when the pinned mesher has no dimension keys', () => {
    render(); valid({ warnings: [] });
    expect(dimensions()).toBeNull();
  });
  it('displays the exact emitted W×H and W×H×D contract and preserves coarse/fine values', () => {
    render(); valid();
    expect(dimensions()?.textContent).toContain(expectedMouth);
    expect(dimensions()?.textContent).toContain('Horn overall');
    expect(dimensions()?.textContent).toContain('Enclosure overall');
    expect(dimensions()?.textContent).toContain('Current preview');
    expect(dimensions()?.textContent).not.toContain('revision');
    const before = dimensions()?.textContent;
    publish({ frame: frame(metadata, 1, 'fine') });
    expect(dimensions()?.textContent).toBe(before);
  });
  it('omits the enclosure readout for a free horn', () => {
    render(); const free = structuredClone(metadata); delete free.dimensions_mm!.enclosure_overall; valid(free);
    expect(dimensions()?.textContent).not.toContain('Enclosure overall');
  });
  it('labels the accepted older frame after a new edit and a matching failure', () => {
    render(); valid();
    act(() => useDesignStore.setState({ designRevision: 2 }));
    publish({ stale: true, error: 'Invalid profile', errorRevision: 2 });
    expect(dimensions()?.textContent).toContain(expectedMouth);
    expect(dimensions()?.textContent).toContain('Last valid preview');
    expect(dimensions()?.querySelectorAll('dd small')).toHaveLength(3);
    publish({ frame: frame(metadata, 2), displayedRevision: 2, stale: false, error: null, errorRevision: null });
    expect(dimensions()?.textContent).toContain('Current preview');
  });
  it('labels values last valid beside an invalid uncommitted NumberField draft', async () => {
    render(); valid();
    const input = host.querySelector<HTMLInputElement>('[data-parameter-id="morph.corner_radius"] input')!;
    await act(async () => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '-20');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(dimensions()?.textContent).toContain('Last valid preview');
  });
  it('shows unavailable for rejected canonical geometry and malformed values', () => {
    render(); valid({ dimensions_mm: null, dimensions_error: 'Cannot fit geometry' });
    expect(dimensions()?.textContent).toContain('unavailable');
    const bad = structuredClone(metadata); bad.dimensions_mm!.mouth_opening = [Number.NaN, 0]; valid(bad);
    expect(dimensions()?.querySelector('dd')?.textContent).toBe('unavailable');
  });
  it('keeps the readouts in the parametric geometry panel', () => {
    render('simulation'); valid(); expect(dimensions()).toBeNull();
    workspaceModeStore.setMode('cad'); render(); expect(dimensions()).toBeNull();
  });
});
