import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';

const previewSnapshot = vi.hoisted(() => ({ connection: 'connected' as const, epoch: null, frame: null, displayedRevision: null, lastValidRevision: null, stale: false, dropped: 0, error: null, errorRevision: null }));

vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({
    engines: [
      { name: 'metal', available: true, reason: null, version: '1.0', fast_paths: [] },
      { name: 'bempp', available: true, reason: null, version: '2.0', fast_paths: [] },
    ],
    engineSelection: {
      default: 'auto', resolvedDefault: 'metal', full3dOrder: ['metal', 'bempp'], axisymmetricRunner: 'axisym',
    },
    error: null,
    isLoading: false,
  }),
}));

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
    expect(host.textContent).toContain('next solve 400 Hz – 16 kHz · 20 f');
    expect(host.textContent).toContain('parametric-design.cfg');

    act(() => {
      useCadReturnStore.setState({
        selectedBundle: { documentName: 'Speaker Assembly', name: 'speaker.wgreturn' } as never,
        ingestRecord: { mesh: { stats: { triangle_count: 12_345 } } } as CadReturnIngestRecord,
        frequencyStartHz: 250,
        frequencyEndHz: 18_000,
        frequencyCount: 31,
      });
      workspaceModeStore.setMode('cad');
    });

    expect(host.textContent).toContain('METAL · 1.0');
    // Grouped in the runner's locale, not en-US — see summary.test.ts.
    expect(host.textContent).toContain(`CAD mesh ${(12_345).toLocaleString()} solved tri`);
    expect(host.textContent).toContain('next CAD solve 250 Hz – 18 kHz · 31 f');
    expect(host.textContent).toContain('Speaker Assembly');
    expect(host.textContent).toContain('CAD return · ingested');
    expect(host.textContent).not.toContain('parametric-design.cfg');
    expect(host.textContent).not.toContain('preview no frame');
  });
});
