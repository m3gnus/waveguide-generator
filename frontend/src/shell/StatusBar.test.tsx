import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';

const previewSnapshot = vi.hoisted(() => ({ connection: 'connected' as const, epoch: null, frame: null, displayedRevision: null, lastValidRevision: null, stale: false, dropped: 0, error: null, errorRevision: null }));

vi.mock('../jobs/useCapabilities', () => ({
  accuracyEngine: (engine: string) => engine,
  useCapabilities: () => ({
    engines: [
      { name: 'metal', available: true, reason: null, version: '1.0', fast_paths: [] },
      { name: 'bempp', available: true, reason: null, version: '2.0', fast_paths: [] },
    ],
    engineSelection: {
      default: 'auto', resolvedDefault: 'metal', full3dOrder: ['metal', 'bempp'],
    },
    error: null,
    isLoading: false,
  }),
}));

const backend = vi.hoisted(() => ({
  plan: { engine: 'bempp' } as { engine: string | null; reason?: string } | null,
  error: null as string | null, isPending: false,
}));
vi.mock('../jobs/useSolveControlBackend', () => ({ useSolveControlBackend: () => backend }));
vi.mock('../api/previewSocket', () => ({
  previewSocket: {
    subscribe: () => () => undefined,
    getSnapshot: () => previewSnapshot,
  },
}));

import { StatusBar } from './StatusBar';

describe('StatusBar workspace modes', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    backend.plan = { engine: 'bempp' }; backend.error = null; backend.isPending = false;
    preferencesStore.resetForTests();
    resetCadReturnStore();
    resetDesignStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    workspaceModeStore.setMode('parametric');
    useDocumentStore.setState({ filename: 'parametric-design.cfg' });
    useSolveOptionsStore.getState().setEngine('bempp');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<StatusBar/>));
  });

  afterEach(() => {
    act(() => root.unmount());
    workspaceModeStore.setMode('parametric');
    host.remove();
  });

  it('replaces every parametric next-solve fact in CAD mode', () => {
    expect(host.textContent).toContain('BEMPP · 2.0');
    expect(host.textContent).toContain('preview no frame');
    expect(host.textContent).toContain('next solve 50 Hz – 16 kHz · 32 f');
    expect(host.textContent).toContain('parametric-design.cfg');

    act(() => {
      useCadReturnStore.setState({
        selectedBundle: { documentName: 'Speaker Assembly', name: 'speaker.wgreturn' } as never,
        needsIngest: false,
        ingestRecord: { mesh: { stats: { triangle_count: 12_345 } } } as CadReturnIngestRecord,
        frequencyStartHz: 250,
        frequencyEndHz: 18_000,
        frequencyCount: 31,
      });
      useImportedSolveOptionsStore.getState().setEngine('bempp');
      workspaceModeStore.setMode('cad');
    });

    // The server plan remains authoritative when the visible choice is Auto.
    expect(host.textContent).toContain('BEMPP · 2.0');
    act(() => { useImportedSolveOptionsStore.getState().setEngine('auto'); });
    expect(host.textContent).toContain('BEMPP · 2.0');
    // Grouped in the runner's locale, not en-US — see summary.test.ts.
    expect(host.textContent).toContain(`CAD model · ${(12_345).toLocaleString()} triangles`);
    expect(host.textContent).toContain('next CAD solve 250 Hz – 18 kHz · 31 f');
    expect(host.textContent).toContain('Speaker Assembly');
    expect(host.textContent).not.toContain('CAD return');
    expect(host.textContent).not.toContain('parametric-design.cfg');
    expect(host.textContent).not.toContain('preview no frame');
  });
  it('keeps the engine cell about the engine when readiness is blocked', () => {
    act(() => { backend.plan = null; backend.isPending = true; root.render(<StatusBar/>); });
    expect(host.textContent).toContain('BEMPP');
    act(() => { backend.isPending = false; backend.error = 'Engine needs preparing'; root.render(<StatusBar/>); });
    expect(host.textContent).not.toContain('SOLVE BLOCKED');
    expect(host.textContent).not.toContain('No solve plan');
    expect(host.textContent).not.toContain('Engine needs preparing');
  });

  it('uses one model cell for a selected unprepared return', () => {
    act(() => {
      workspaceModeStore.setMode('cad');
      useCadReturnStore.setState({ selectedBundle: { name: 'speaker.wgreturn', documentName: 'Speaker' } as never });
    });
    expect(host.textContent).toContain('CAD model · not prepared');
    expect(host.textContent?.match(/CAD model ·/g)).toHaveLength(1);
    expect(host.textContent).not.toMatch(/ingest|CAD return|No solve plan|SOLVE BLOCKED/i);
  });
});
