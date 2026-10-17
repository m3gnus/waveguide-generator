import '../shell/cadlink/restores';
import { openImported, type ImportedDisplay } from '../stores/importedModel';
import { deferred, job as archivedJob, json, mesh as archivedMesh, record as archivedRecord, workspaceFor } from '../shell/cadlink/selection.fixtures';
/** Reopen a CAD run as history does: `openImported` of the run, shown by the CAD rail. */
const restoreCadJobModel = (run: JobItem, display: ImportedDisplay = {}, fetcher: typeof fetch = fetch) =>
  openImported({ kind: 'run', job: run, display }, fetcher).catch(() => false);

import { readFileSync } from 'node:fs';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecodedFrame } from '../api/frame';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { PreviewSnapshot } from '../api/previewSocket';
import { compareSelection } from '../api/results';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { currentDocumentLoad, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { createImportedMeshScene } from './importedMesh';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { importedMeshStore } from './importedMeshStore';
import { parseMSH } from './mshParser';
import { useFieldPlaneStore } from './fieldPlaneStore';
import { useFieldPlaneProbeStore } from './fieldPlaneProbe';
import meshFixture from './test-fixtures/tagged_sources-small.msh?raw';
import { femFieldFixture } from './test-fixtures/femField';
import * as canvasModule from './ViewportCanvas';
import * as femSceneModule from './FemFieldScene';

const frame: DecodedFrame = {
  header: {
    v: 1,
    kind: 'preview',
    epoch: 4,
    seq: 9,
    designRevision: 0,
    lod: 'fine',
    evalMs: 1_388,
    sections: [],
    surfaces: [{
      role: 'horn.inner',
      positions: 'horn.positions',
      normals: 'horn.normals',
      indices: 'horn.indices',
      shading: 'smooth',
      normalMethod: 'analytic-parametric',
    }],
  },
  sections: {
    'horn.positions': new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
    'horn.normals': new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
    'horn.indices': new Uint32Array([0, 1, 2]),
  },
};

const previewSnapshot: PreviewSnapshot = {
  connection: 'connected',
  epoch: 4,
  frame,
  displayedRevision: 0,
  lastValidRevision: 0,
  stale: true,
  dropped: 0,
  error: 'ATH expression is unsupported',
  errorFields: null,
  errorRevision: 0,
};

const refreshCalls: number[] = [];

function setInputValue(input: HTMLInputElement, value: string): void {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
}

// Run coherence is decided by the design a run stored, so a run that stands
// for "the model in the viewport" has to carry the design that is on screen.
function completeJob(id: string, fieldPlaneAvailable: boolean): JobItem {
  return {
    id,
    status: 'complete',
    config_summary: {},
    design_revision: 1,
    script_snapshot: { version: 1, design: serializeDesign(useDesignStore.getState().design) },
    field_plane_available: fieldPlaneAvailable,
  } as unknown as JobItem;
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as {
    snapshot: JobsSnapshot;
    listeners: Set<() => void>;
  };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

const fieldPlane = {
  origin_m: [0, 0, 0] as [number, number, number],
  axis_u: [1, 0, 0] as [number, number, number],
  axis_v: [0, 0, 1] as [number, number, number],
  width_m: 0.2,
  height_m: 0.4,
  nx: 96,
  ny: 96,
};

vi.mock('../api/previewSocket', () => ({
  PREVIEW_FINE_IDLE_MS: 140,
  previewSocket: {
    subscribe: () => () => undefined,
    getSnapshot: () => previewSnapshot,
    refresh: () => refreshCalls.push(1),
    setCurvatureWanted: vi.fn(),
  },
}));

import { femFieldJob, fieldPlaneUnavailableTooltip, cadViewportEmptyCopy, refitsCamera, Viewport } from './Viewport';

describe('CAD viewport empty-state copy', () => {
  it('reports automatic preparation progress with the selected bundle name', () => {
    expect(cadViewportEmptyCopy({
      bundleName: 'Speaker CAD', bundleReadable: true, ingesting: true,
      ingestError: null, cadApplication: 'Fusion 360',
    })).toEqual({
      title: 'Preparing Speaker CAD…',
      detail: 'CAD Link is building the viewport and solver mesh.',
    });
  });

  it('points a failed preparation to the CAD Link retry', () => {
    const copy = cadViewportEmptyCopy({
      bundleName: 'Speaker CAD', bundleReadable: true, ingesting: false,
      ingestError: 'meshing failed', cadApplication: 'Fusion 360',
    });
    expect(copy.title).toBe('CAD preparation failed');
    expect(copy.detail).toContain('“Prepare simulation” to retry');
  });
});

describe('Viewport preview errors', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    compareSelection.clear();
    publishJobs([]);
    useFieldPlaneStore.getState().disable();
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
    useDocumentStore.setState({ designName: 'loaded-design', filename: 'loaded-design.cfg' });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<Viewport />));
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.restoreAllMocks();
    useDocumentStore.setState({ filename: 'tritonia_mk2.cfg' });
    compareSelection.clear();
    publishJobs([]);
    useFieldPlaneStore.getState().disable();
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
    host.remove();
  });

  it('shows a dismissible alert while a retained scene is present', () => {
    const alert = host.querySelector<HTMLElement>('[role="alert"]');
    expect(alert?.textContent).toContain('Displayed geometry is not the current design');
    expect(host.textContent).toContain('WebGL unavailable');
    expect(host.textContent).not.toContain('Waiting for geometry');
    expect(host.querySelector('.error-badge')?.textContent).toContain('ERROR');
    expect(host.querySelector('.viewport-title b')?.textContent).toBe('loaded-design');

    act(() => alert?.querySelector<HTMLButtonElement>('[aria-label="Dismiss preview error"]')?.click());
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('renders the design-size readout in the parametric viewport and removes it in CAD mode', () => {
    const saved = { ...previewSnapshot };
    try {
      const revision = useDesignStore.getState().designRevision;
      Object.assign(previewSnapshot, { frame: { ...frame, documentLoad: currentDocumentLoad(), header: { ...frame.header, designRevision: revision,
        previewMetadata: { dimensions_mm: { mouth_opening: [411.7, 320], horn_overall: [411.7, 320, 176.5], enclosure_overall: [450, 360, 240] } } } },
        dimensionsFrame: null, displayedRevision: revision, stale: false, error: null });
      act(() => root.render(<Viewport />));
      const readout = host.querySelector('[data-testid="design-dimensions"]')!;
      expect(readout.closest('.wg2-viewport')).not.toBeNull();
      expect(readout.textContent).toBe('Mouth 411.7 × 320.0 · Depth 176.5 · Enclosure 450.0 × 360.0 × 240.0 mm');
      expect(readout.classList.contains('stale')).toBe(false);
      act(() => workspaceModeStore.setMode('cad'));
      expect(host.querySelector('[data-testid="design-dimensions"]')).toBeNull();
    } finally { Object.assign(previewSnapshot, saved); }
  });

  it('shows native opening dimensions only for the current document and revision', () => {
    const original = { ...previewSnapshot };
    try {
      act(() => useDesignStore.getState().setFamily('OSSE-ARC'));
      const revision = useDesignStore.getState().designRevision;
      const dimensionsFrame = { ...frame, documentLoad: currentDocumentLoad(), header: {
        ...frame.header, designRevision: revision,
        previewMetadata: { dimensions_mm: { mouth_opening: [280, 280] as [number, number] } },
      } };
      const render = () => act(() => root.render(<Viewport />));
      Object.assign(previewSnapshot, { dimensionsFrame, displayedRevision: revision, stale: false, error: null });
      render();
      expect(host.querySelector('.viewport-title span')?.textContent).toBe('OSSE-ARC · Ø 280 mm · full');
      previewSnapshot.awaitingDocumentFrame = true;
      render();
      expect(host.querySelector('.viewport-title span')?.textContent).toBe('OSSE-ARC · full');
      previewSnapshot.awaitingDocumentFrame = false;
      dimensionsFrame.documentLoad -= 1;
      render();
      expect(host.querySelector('.viewport-title span')?.textContent).toBe('OSSE-ARC · full');
      dimensionsFrame.documentLoad = currentDocumentLoad();
      dimensionsFrame.header.designRevision -= 1;
      render();
      expect(host.querySelector('.viewport-title span')?.textContent).toBe('OSSE-ARC · full');
    } finally {
      delete previewSnapshot.dimensionsFrame;
      delete previewSnapshot.awaitingDocumentFrame;
      Object.assign(previewSnapshot, original);
    }
  });

  it('offers a retry that re-requests the current design', () => {
    refreshCalls.length = 0;
    const retry = [...host.querySelectorAll<HTMLButtonElement>('[role="alert"] button')]
      .find((button) => button.textContent === 'Retry');
    expect(retry).toBeDefined();
    act(() => retry?.click());
    expect(refreshCalls).toHaveLength(1);
    expect(host.querySelector('.error-badge')?.textContent).toContain('REFRESHING');
  });

  it('offers a refresh beside the badge whenever the view lags the design', () => {
    refreshCalls.length = 0;
    const refresh = host.querySelector<HTMLButtonElement>('.viewport-refresh');
    expect(refresh?.textContent).toContain('Refresh');
    act(() => refresh?.click());
    expect(refreshCalls).toHaveLength(1);
  });

  it('presents geometry evaluation and on-screen time as overlapping timings', () => {
    const timing = [...host.querySelectorAll<HTMLElement>('.viewport-live > span')]
      .find((element) => element.textContent?.startsWith('geometry '));
    // 'request→paint' was developer telemetry sitting in a user-facing readout.
    expect(timing?.textContent).toBe('geometry 1388.0 ms · on screen —');
    expect(timing?.textContent).not.toContain('+ client');
  });

  it('starts in an untilted orthographic front view for precise shape inspection', () => {
    const projection = host.querySelector<HTMLButtonElement>('.projection-toggle');

    expect(projection?.textContent).toBe('Ortho');
  });

  it('cycles display modes from one compact toolbar control', () => {
    const mode = host.querySelector<HTMLButtonElement>('.display-mode-tools button');
    expect(host.querySelectorAll('.display-mode-tools button')).toHaveLength(1);
    // Six view controls plus the two-way Geometry/Mesh source switch.
    expect(host.querySelectorAll('.viewport-tools button')).toHaveLength(8);
    expect(host.querySelectorAll('.mesh-source-tools button')).toHaveLength(2);
    expect(host.querySelector('.viewport-tools [aria-label="Import Gmsh 2.2 mesh"]')).toBeNull();
    expect(host.querySelector('.viewport-tools [aria-label="View presets"]')).toBeNull();
    expect(mode?.getAttribute('aria-label')).toContain('Display mode: Clay');
    act(() => mode?.click());
    expect(mode?.getAttribute('aria-label')).toContain('Display mode: Solid + wireframe');
  });

  it('hides all field-plane controls when the selected/latest complete job has no data', () => {
    act(() => publishJobs([completeJob('unavailable', false)]));
    expect(host.querySelector('[aria-label="Acoustic field plane overlay"]')).toBeNull();
    expect(host.querySelector('[aria-label="Clip model to field plane"]')).toBeNull();
    expect(host.querySelector('[aria-label="Invert field-plane clip side"]')).toBeNull();
    expect(host.querySelector<HTMLButtonElement>('.section-cut-toggle')).not.toBeNull();
    expect(host.querySelectorAll('.viewport-tools .viewport-tool-group:empty')).toHaveLength(0);
  });

  it('cycles the section cut through three axes and back off from one button', () => {
    const section = host.querySelector<HTMLButtonElement>('.section-cut-toggle');
    const axis = () => section?.querySelector('.section-cut-axis')?.textContent ?? null;

    expect(section?.getAttribute('aria-pressed')).toBe('false');
    expect(axis()).toBeNull();
    // The tooltip has to say the button cycles; three presses is not something
    // to discover by accident.
    expect(section?.title).toContain('cycles X, Y, Z, off');

    for (const expected of ['X', 'Y', 'Z']) {
      act(() => section?.click());
      expect(section?.getAttribute('aria-pressed')).toBe('true');
      expect(axis()).toBe(expected);
      expect(section?.getAttribute('aria-label')).toContain(`Section cut on ${expected}`);
    }

    act(() => section?.click());
    expect(section?.getAttribute('aria-pressed')).toBe('false');
    expect(axis()).toBeNull();
    expect(section?.getAttribute('aria-label')).toContain('Section cut off');
  });

  it('keeps the interior scene mounted while a combined plane loads and after it fails', async () => {
    vi.spyOn(canvasModule, 'canRenderWebGL').mockReturnValue(true);
    vi.spyOn(canvasModule.ViewportCanvas as unknown as { type: (props: { children: import('react').ReactNode }) => import('react').ReactNode }, 'type')
      .mockImplementation(({ children }) => <>{children}</>);
    vi.spyOn(femSceneModule, 'FemFieldScene').mockImplementation(() => <div data-testid="fem-scene"/>);
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { finish = resolve; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname.endsWith('/fem-field/plane')) return pending;
      if (url.pathname.endsWith('/fem-field')) return new Response(JSON.stringify({ ...femFieldFixture(),
        plane_scopes: ['interior', 'exterior', 'combined'] }));
      return new Response('', { status: 404 });
    });
    act(() => {
      compareSelection.setPrimary('fem');
      publishJobs([{ ...completeJob('fem', false), has_fem_field_artifact: true }]);
      useFieldPlaneStore.getState().enableFem('fem', fieldPlane);
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    act(() => {
      const scope = host.querySelector<HTMLSelectElement>('[aria-label="FEM plane region"]')!;
      scope.value = 'combined'; scope.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 275)); });
    expect(host.textContent).toContain('Loading FEM field');
    expect(host.querySelector('[data-testid="fem-scene"]')).not.toBeNull();
    await act(async () => { finish(new Response(JSON.stringify({ detail: 'BEAT runtime unavailable' }), { status: 409 })); });
    expect(host.textContent).toContain('BEAT runtime unavailable');
    expect(host.querySelector('[data-testid="fem-scene"]')).not.toBeNull();
  });

  it.each(['unit_acceleration', 'voltage_rms'] as const)('mounts FEM controls with %s and selects only the requested frequency/channel', async (normalization) => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname.endsWith('/fem-field')) return new Response(JSON.stringify({ ...femFieldFixture(
        Number(url.searchParams.get('frequency') ?? 100), url.searchParams.get('channel') ?? 'front'),
        drive_normalization: normalization, transducer_reference_voltage_v: normalization === 'voltage_rms' ? 2.83 : undefined }));
      return new Response('', { status: 404 });
    });
    act(() => {
      compareSelection.setPrimary('fem');
      publishJobs([{ ...completeJob('fem', false), has_fem_field_artifact: true }]);
      useFieldPlaneStore.getState().enableFem('fem', fieldPlane);
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(host.querySelector('[aria-label="FEM field controls"]')).not.toBeNull();
    expect(host.textContent).toContain(normalization === 'voltage_rms' ? '2.83 V RMS (Pa)' : 'Pa per m/s²');
    expect(host.querySelector('.experimental-badge')).toBeNull();
    expect(host.textContent).not.toContain('Experimental');
    expect(host.querySelector('[aria-label="FEM field plane presets"]')?.getAttribute('role')).toBe('group');
    expect(host.querySelector('[aria-label="FEM interior field overlay"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(host.querySelector('[aria-label="Field plane controls"]')).toBeNull();
    const slider = host.querySelector<HTMLInputElement>('[aria-label="FEM field frequency"]')!;
    act(() => { setInputValue(slider, '1'); slider.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 275)); });
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('frequency=1000'))).toBe(true);
    expect(slider.getAttribute('aria-valuetext')).toBe('1000 Hz');
    const channel = host.querySelector<HTMLSelectElement>('[aria-label="FEM field channel"]')!;
    act(() => { channel.value = 'rear'; channel.dispatchEvent(new Event('change', { bubbles: true })); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 275)); });
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('frequency=1000&channel=rear'))).toBe(true);
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/field-plane'))).toBe(false);
    const rendering = host.querySelector<HTMLSelectElement>('[aria-label="FEM field rendering"]')!;
    act(() => { rendering.value = 'element'; rendering.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(rendering.value).toBe('element');
    const display = host.querySelector<HTMLSelectElement>('[aria-label="FEM field display mode"]')!;
    act(() => { display.value = 'normalized'; display.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(useFieldPlaneStore.getState().displayMode).toBe('normalized');
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Play FEM phase animation"]')!.click());
    expect(useFieldPlaneStore.getState()).toMatchObject({ animating: true, displayMode: 'instantaneous' });
    expect(host.querySelector('[aria-label="FEM phase animation speed"]')).not.toBeNull();
    act(() => {
      const quantity = host.querySelector<HTMLSelectElement>('[aria-label="FEM field quantity"]')!;
      quantity.value = 'velocity_normal'; quantity.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(host.textContent).toContain(normalization === 'voltage_rms' ? '2.83 V RMS (m/s)' : '(m/s) per m/s²');

    act(() => {
      publishJobs([completeJob('ordinary', false)]);
      compareSelection.setPrimary('ordinary');
    });
    expect(host.querySelector('[aria-label="FEM field controls"]')).toBeNull();
  });

  it.each(['no-marker', 'flag-off', 'running', 'stale', 'unselected'] as const)(
    'does not load a FEM field for a %s result', async (reason) => {
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 404 }));
      const job = { ...completeJob('fem', false), has_fem_field_artifact: true };
      if (reason === 'no-marker') delete (job as Partial<JobItem>).has_fem_field_artifact;
      if (reason === 'flag-off') job.has_fem_field_artifact = false;
      if (reason === 'running') job.status = 'running';
      if (reason === 'stale') job.script_snapshot = null;
      act(() => {
        publishJobs([job]);
        compareSelection.setPrimary(reason === 'unselected' ? 'other' : 'fem');
        useFieldPlaneStore.getState().enableFem('fem', fieldPlane);
      });
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
      expect(host.querySelector('[aria-label="FEM field controls"]')).toBeNull();
      expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/fem-field'))).toBe(false);
    },
  );

  it.each([undefined, false])('makes zero FEM-field requests for a non-FEM CAD result (marker %s)', async (marker) => {
    const ingestId = 'wgi_non_fem_field';
    const job: JobItem = {
      ...completeJob('ordinary-cad', false),
      config_summary: { geometry_type: 'imported' },
      cad_source: { ingest_id: ingestId, archive_stem: null, design_id: null,
        document_name: null, lineage_id: null, manifest_sha256: null, return_state_hash: null },
      has_fem_field_artifact: marker,
    };
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 404 }));
    act(() => {
      useCadReturnStore.setState({ ingestRecord: { ingest_id: ingestId } as CadReturnIngestRecord });
      importedMeshStore.setCad(createImportedMeshScene('CAD model', parseMSH(meshFixture), 'cad', ingestId));
      workspaceModeStore.setMode('cad');
      publishJobs([job]);
      compareSelection.setPrimary(job.id);
      useFieldPlaneStore.getState().enableFem(job.id, fieldPlane);
    });
    await act(async () => { await Promise.resolve(); });
    // It is selected, complete and coherent; only the field advertisement is absent.
    expect(femFieldJob([{ ...job, has_fem_field_artifact: true }], job.id)?.id).toBe(job.id);
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes('/fem-field'))).toHaveLength(0);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(host.querySelector('[aria-label="FEM field controls"]')).toBeNull();
  });

  it('shows overlay alone until enabled, then shows clip and invert', () => {
    act(() => {
      compareSelection.setPrimary('available');
      publishJobs([completeJob('available', true)]);
    });
    expect(host.querySelector('[aria-label="Acoustic field plane overlay"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Clip model to field plane"]')).toBeNull();
    expect(host.querySelector('[aria-label="Invert field-plane clip side"]')).toBeNull();

    act(() => useFieldPlaneStore.setState({
      enabled: true,
      jobId: 'available',
      plane: fieldPlane,
      status: 'ready',
    }));
    expect(host.querySelector('[aria-label="Clip model to field plane"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Invert field-plane clip side"]')).not.toBeNull();
  });

  it('discloses fields evaluated from reconstructed surface traces', () => {
    act(() => {
      compareSelection.setPrimary('available');
      publishJobs([completeJob('available', true)]);
      useFieldPlaneStore.setState({
        enabled: true, jobId: 'available', plane: fieldPlane, status: 'ready',
        frequenciesHz: [500, 1000, 2000], frequencyIndex: 1,
        frequencyStatus: ['solved', 'interpolated', 'solved'],
      });
    });
    expect(host.textContent).toContain('Adaptive sampling · interpolated frequency');
    expect(host.textContent).toContain('reconstructed surface traces');
    expect(host.textContent).toContain('narrow resonances can be missed');
  });

  it('scrubs solved frequencies with a slider instead of a dropdown', () => {
    act(() => {
      compareSelection.setPrimary('available');
      publishJobs([completeJob('available', true)]);
      useFieldPlaneStore.setState({
        enabled: true,
        jobId: 'available',
        plane: fieldPlane,
        status: 'ready',
        frequenciesHz: [500, 1_000, 2_000],
        frequencyIndex: 1,
      });
    });
    const slider = host.querySelector<HTMLInputElement>('[aria-label="Field plane frequency"]')!;
    expect(slider.type).toBe('range');
    expect(slider.max).toBe('2');
    expect(slider.value).toBe('1');
    expect(host.querySelector('.field-plane-legend-title span')?.textContent).toBe(`${(1000).toLocaleString()} Hz`);

    const setFrequencyIndex = vi.spyOn(useFieldPlaneStore.getState(), 'setFrequencyIndex');
    act(() => {
      setInputValue(slider, '2');
      slider.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(setFrequencyIndex).toHaveBeenCalledWith(2);
  });

  it('snaps a typed frequency to the nearest solved one and says when they differ', () => {
    act(() => {
      compareSelection.setPrimary('available');
      publishJobs([completeJob('available', true)]);
      useFieldPlaneStore.setState({
        enabled: true,
        jobId: 'available',
        plane: fieldPlane,
        status: 'ready',
        frequenciesHz: [500, 987, 2_000],
        frequencyIndex: 0,
      });
    });
    const noticeText = () => [...host.querySelectorAll('.field-plane-note')]
      .map((note) => note.textContent)
      .find((text) => text?.includes('requested'));
    const input = host.querySelector<HTMLInputElement>('[aria-label="Field plane frequency in hertz"]')!;
    expect(input).not.toBeNull();
    const enter = (value: string) => {
      act(() => {
        input.focus();
        setInputValue(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      act(() => input.blur());
    };

    enter('1000');
    expect(useFieldPlaneStore.getState().frequencyIndex).toBe(1);
    expect(noticeText()).toBe(`requested ${(1000).toLocaleString()} Hz → showing 987 Hz`);

    // Moving the slider away retires the notice; retyping the exact solved
    // frequency never raises one.
    const slider = host.querySelector<HTMLInputElement>('[aria-label="Field plane frequency"]')!;
    act(() => {
      setInputValue(slider, '2');
      slider.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(noticeText()).toBeUndefined();
    enter('987');
    expect(useFieldPlaneStore.getState().frequencyIndex).toBe(1);
    expect(noticeText()).toBeUndefined();
  });

  it('offers per-driver response views only when the run has combine members', () => {
    act(() => {
      compareSelection.setPrimary('available');
      publishJobs([completeJob('available', true)]);
      useFieldPlaneStore.setState({
        enabled: true,
        jobId: 'available',
        plane: fieldPlane,
        status: 'ready',
      });
    });
    expect(host.querySelector('[aria-label="Field plane response"]')).toBeNull();

    act(() => useFieldPlaneStore.setState({
      memberResponses: [{ id: 'left', label: 'LF' }, { id: 'right', label: 'HF' }],
    }));
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Field plane response"]')!;
    expect([...select.options].map((option) => [option.value, option.textContent])).toEqual([
      ['system', 'Combined'],
      ['member:left', 'LF'],
      ['member:right', 'HF'],
    ]);
    expect(select.value).toBe('system');

    const setResponseId = vi.spyOn(useFieldPlaneStore.getState(), 'setResponseId');
    act(() => {
      select.value = 'member:left';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(setResponseId).toHaveBeenCalledWith('member:left');
  });

  it('moves and resizes the plane from the numeric transform inputs', () => {
    act(() => {
      compareSelection.setPrimary('available');
      publishJobs([completeJob('available', true)]);
      useFieldPlaneStore.setState({
        enabled: true,
        jobId: 'available',
        plane: fieldPlane,
        status: 'ready',
      });
    });
    const enter = (label: string, value: string) => {
      const input = host.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
      act(() => {
        input.focus();
        setInputValue(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      act(() => input.blur());
    };

    enter('Field plane centre Y in millimetres', '25');
    expect(useFieldPlaneStore.getState().plane?.origin_m).toEqual([0, 0.025, 0]);

    enter('Field plane width in millimetres', '600');
    expect(useFieldPlaneStore.getState().plane?.width_m).toBeCloseTo(0.6);

    // Below the request validator's floor, so the store keeps the clamp.
    enter('Field plane height in millimetres', '0');
    expect(useFieldPlaneStore.getState().plane?.height_m).toBeCloseTo(0.005);
  });

  it('labels the pressure under the pointer while the plane is shown', () => {
    act(() => {
      compareSelection.setPrimary('available');
      publishJobs([completeJob('available', true)]);
      useFieldPlaneStore.setState({
        enabled: true,
        jobId: 'available',
        plane: fieldPlane,
        status: 'ready',
      });
      useFieldPlaneProbeStore.getState().show({
        localX: 120,
        localY: 90,
        hostWidth: 900,
        hostHeight: 700,
        offsetU_m: 0.05,
        offsetV_m: -0.02,
        point_m: [0.05, 0, -0.02],
        real: 2,
        imag: 0,
        masked: false,
      });
    });
    const probe = host.querySelector('.field-plane-probe')!;
    expect(probe.querySelector('b')?.textContent).toBe('100.0 dB');
    expect(probe.textContent).toContain('0° phase');
    expect(probe.textContent).toContain('u 50 · v -20 mm');

    act(() => useFieldPlaneProbeStore.getState().hide());
    expect(host.querySelector('.field-plane-probe')).toBeNull();
  });

  it('retries a blocked field plane once per solve-busy transition', () => {
    const retryIfBlocked = vi.spyOn(useFieldPlaneStore.getState(), 'retryIfBlocked')
      .mockImplementation(() => undefined);
    const running = { ...completeJob('running', false), status: 'running' as const };

    act(() => publishJobs([running]));
    expect(retryIfBlocked).not.toHaveBeenCalled();

    act(() => publishJobs([]));
    expect(retryIfBlocked).toHaveBeenCalledOnce();

    act(() => publishJobs([]));
    expect(retryIfBlocked).toHaveBeenCalledOnce();
  });

  it('resets overlay and clip when the selected job loses availability', () => {
    act(() => {
      publishJobs([completeJob('available', true), completeJob('unavailable', false)]);
      compareSelection.setPrimary('available');
      useFieldPlaneStore.setState({
        enabled: true,
        jobId: 'available',
        plane: fieldPlane,
        status: 'ready',
      });
    });
    const clip = host.querySelector<HTMLButtonElement>('[aria-label="Clip model to field plane"]')!;
    act(() => clip.click());
    expect(clip.getAttribute('aria-pressed')).toBe('true');

    act(() => compareSelection.setPrimary('unavailable'));
    expect(host.querySelector('[aria-label="Acoustic field plane overlay"]')).toBeNull();
    expect(useFieldPlaneStore.getState().enabled).toBe(false);

    act(() => compareSelection.setPrimary('available'));
    expect(host.querySelector('[aria-label="Acoustic field plane overlay"]')?.getAttribute('aria-pressed')).toBe('false');
    expect(host.querySelector('[aria-label="Clip model to field plane"]')).toBeNull();
    expect(host.querySelector('[aria-label="Invert field-plane clip side"]')).toBeNull();
  });

  it('keeps enclosure and frame stats in viewer preferences', () => {
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Viewer preferences"]')?.click());
    const labels = [...host.querySelectorAll<HTMLLabelElement>('.viewer-pref-toggle')].map((label) => label.textContent);
    expect(labels).toContain('Show enclosure');
    expect(labels).toContain('Show frame stats');
  });

  it('keys the CAD scene\'s source colours, and only where the colours are on screen', () => {
    const ingestId = 'wgi_viewport_legend';
    act(() => {
      useCadReturnStore.setState({
        ingestRecord: { ingest_id: ingestId } as CadReturnIngestRecord,
        selectedBundle: { documentName: 'Speaker CAD', name: 'speaker.wgreturn' } as never,
      });
      importedMeshStore.setCad(createImportedMeshScene('Speaker CAD', parseMSH(meshFixture), 'cad', ingestId));
      workspaceModeStore.setMode('cad');
    });
    const legend = () => host.querySelector('[aria-label="Acoustic source colours"]');
    // The fixture paints one HF group and one LF group; role order is fixed,
    // so HF leads whichever way round the mesh lists them.
    expect([...legend()!.querySelectorAll('li span')].map((entry) => entry.textContent)).toEqual(['HF', 'LF']);

    // Zebra replaces every surface colour with a reflection pattern, so a
    // colour key there would be describing something that is not on screen.
    const modeButton = () => host.querySelector<HTMLButtonElement>('[aria-label^="Display mode"]')!;
    for (let step = 0; step < 8 && !modeButton().getAttribute('aria-label')?.startsWith('Display mode: Zebra'); step += 1) {
      act(() => modeButton().click());
    }
    expect(modeButton().getAttribute('aria-label')).toContain('Display mode: Zebra');
    expect(legend()).toBeNull();

    act(() => workspaceModeStore.setMode('parametric'));
  });

  it.each(['request', 'record', 'viewport'] as const)('lets shared CAD restoration own a mounted viewport during the %s read', async stage => {
    const pending = deferred<Response>();
    const reached = deferred<void>();
    const fetcher = vi.fn<typeof fetch>(async input => {
      const path = String(input);
      if ((stage === 'request' && path.endsWith('/workspace'))
        || (stage === 'record' && path.endsWith(`/ingest/${archivedRecord.ingest_id}`))
        || (stage === 'viewport' && path.endsWith('/viewport-mesh'))) {
        reached.resolve(); return pending.promise;
      }
      if (path.endsWith('/workspace')) return json(workspaceFor());
      if (path.endsWith('/viewport-mesh')) return new Response(archivedMesh);
      return json(archivedRecord);
    });
    vi.stubGlobal('fetch', fetcher);
    let restoring!: Promise<boolean>;
    await act(async () => { restoring = restoreCadJobModel(archivedJob(), {}, fetcher); await reached.promise; });
    const intent = importedMeshStore.captureGeneration();
    // Both no-record and new-record effects run while the adapter awaits input.
    expect(importedMeshStore.isCurrentGeneration(intent)).toBe(true);
    await act(async () => {
      pending.resolve(stage === 'request' ? json(workspaceFor())
        : stage === 'record' ? json(archivedRecord) : new Response(archivedMesh));
      expect(await restoring).toBe(true);
    });
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe(archivedRecord.ingest_id);
    expect(fetcher.mock.calls.filter(([input]) => String(input).endsWith('/viewport-mesh'))).toHaveLength(1);
    expect(host.querySelector('.viewport-title b')?.textContent).toBe('Archived speaker');
    vi.unstubAllGlobals();
  });

  it('switches between the parametric scene and matching CAD slot', () => {
    const ingestId = 'wgi_viewport_mode';
    act(() => {
      useCadReturnStore.setState({
        ingestRecord: { ingest_id: ingestId } as CadReturnIngestRecord,
        selectedBundle: { documentName: 'Speaker CAD', name: 'speaker.wgreturn' } as never,
      });
      importedMeshStore.setCad(createImportedMeshScene('Speaker CAD', parseMSH(meshFixture), 'cad', ingestId));
    });
    expect(host.querySelector('.viewport-title b')?.textContent).toBe('loaded-design');

    expect(host.querySelector('.mesh-source-tools')).not.toBeNull();

    act(() => workspaceModeStore.setMode('cad'));
    expect(host.querySelector('.viewport-title b')?.textContent).toBe('Speaker CAD');
    // This return carries no independent display artifact, so the geometry on
    // screen already is the solve mesh and there is nothing to switch between.
    expect(host.querySelector('.mesh-source-tools')).toBeNull();

    act(() => workspaceModeStore.setMode('parametric'));
    expect(host.querySelector('.viewport-title b')?.textContent).toBe('loaded-design');
  });

  it('shows the ingested solve mesh in CAD Link when a display artifact exists', async () => {
    const ingestId = 'wgi_viewport_cadmesh';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      expect(String(input)).toBe(`/api/cadlink/ingest/${ingestId}/mesh`);
      return new Response(meshFixture, { status: 200 });
    });
    act(() => {
      useCadReturnStore.setState({
        ingestRecord: {
          ingest_id: ingestId,
          symmetry: { cut_planes: [] },
          viewport_mesh: { available: true },
        } as unknown as CadReturnIngestRecord,
        selectedBundle: { documentName: 'Speaker CAD', name: 'speaker.wgreturn' } as never,
      });
      importedMeshStore.setCad(createImportedMeshScene('Speaker CAD', parseMSH(meshFixture), 'cad', ingestId));
      workspaceModeStore.setMode('cad');
    });

    const buttons = () => [...host.querySelectorAll<HTMLButtonElement>('.mesh-source-tools button')];
    expect(buttons().map((button) => button.textContent)).toEqual(['Geometry', 'Mesh']);
    expect(buttons()[0].getAttribute('aria-pressed')).toBe('true');

    await act(async () => {
      buttons()[1].click();
      await Promise.resolve();
    });
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(buttons()[1].getAttribute('aria-pressed')).toBe('true');
    expect(importedMeshStore.getSnapshot().cadSolver?.ingestId).toBe(ingestId);

    // The artifact is immutable: reselecting it must re-serve the cached scene
    // rather than fetching the same mesh again.
    await act(async () => {
      buttons()[0].click();
      await Promise.resolve();
    });
    await act(async () => {
      buttons()[1].click();
      await Promise.resolve();
    });
    expect(fetchSpy).toHaveBeenCalledOnce();

    fetchSpy.mockRestore();
    act(() => {
      workspaceModeStore.setMode('parametric');
      importedMeshStore.clear();
      resetCadReturnStore();
    });
  });

  it('keeps the CAD mesh view through the record refreshes CAD Link polls out', async () => {
    const ingestId = 'wgi_viewport_cadpoll';
    // CAD Link re-publishes an equal record while it polls the CAD
    // application. Keying the fetch on the record object made every poll
    // cancel it, so the view sat on "loading solve mesh…" forever.
    const record = () => ({
      ingest_id: ingestId,
      symmetry: { cut_planes: [] },
      viewport_mesh: { available: true },
    } as unknown as CadReturnIngestRecord);
    let release: (() => void) | null = null;
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      await new Promise<void>((resolve) => { release = resolve; });
      return new Response(meshFixture, { status: 200 });
    });
    act(() => {
      useCadReturnStore.setState({
        ingestRecord: record(),
        selectedBundle: { documentName: 'Speaker CAD', name: 'speaker.wgreturn' } as never,
      });
      importedMeshStore.setCad(createImportedMeshScene('Speaker CAD', parseMSH(meshFixture), 'cad', ingestId));
      workspaceModeStore.setMode('cad');
    });
    const buttons = () => [...host.querySelectorAll<HTMLButtonElement>('.mesh-source-tools button')];
    act(() => buttons()[1].click());
    expect(host.querySelector('.viewport-live')?.textContent).toContain('loading solve mesh');

    // Three polls land while the fetch is still open.
    act(() => useCadReturnStore.setState({ ingestRecord: record() }));
    act(() => useCadReturnStore.setState({ ingestRecord: record() }));
    act(() => useCadReturnStore.setState({ ingestRecord: record() }));
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(buttons()[1].getAttribute('aria-pressed')).toBe('true');

    await act(async () => {
      release!();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(importedMeshStore.getSnapshot().cadSolver?.ingestId).toBe(ingestId);
    expect(host.querySelector('.viewport-live')?.textContent).toContain('ingested solve mesh');

    fetchSpy.mockRestore();
    act(() => {
      workspaceModeStore.setMode('parametric');
      importedMeshStore.clear();
      resetCadReturnStore();
    });
  });
});

describe('Viewport geometry warnings', () => {
  let host: HTMLDivElement;
  let root: Root;
  const savedError = previewSnapshot.error;

  const render = (warnings: string[] | undefined, { error = null as string | null, dimensions = false } = {}) => {
    previewSnapshot.error = error;
    frame.header.previewMetadata = warnings ? { warnings, ...(dimensions ? { dimensions_mm: { mouth_opening: [411.7, 320], horn_overall: [421.7, 330, 176.5], enclosure_overall: [500, 400, 200] } } : {}) } : undefined;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<Viewport />));
  };

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    workspaceModeStore.setMode('parametric');
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    previewSnapshot.error = savedError;
    frame.header.previewMetadata = undefined;
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
  });

  // The mesher reports a guiding curve its coverage solver could not reach.
  // The geometry still builds, so nothing else in the viewport would say that
  // the mouth is off the guide shape and the coverage angle is pinned.
  const UNREACHABLE =
    'guiding curve unreachable at every probed azimuth: the coverage angle is pinned at 0.5 deg, '
    + 'so the mouth radius is 581.3 mm instead of the requested 500.0 mm; shorten the horn (Length), '
    + 'reduce the termination shape s, or widen the guiding curve';

  it('keeps a mesher geometry warning behind a compact disclosure', () => {
    render([UNREACHABLE]);
    const warning = host.querySelector<HTMLDetailsElement>('.viewport-warning');
    expect(warning).not.toBeNull();
    expect(warning?.getAttribute('role')).toBe('status');
    expect(warning?.open).toBe(false);
    expect(warning?.querySelector('summary')?.textContent).toContain('1 warning');

    act(() => warning?.querySelector('summary')?.click());
    expect(warning?.open).toBe(true);
    expect(warning?.textContent).toContain('guiding curve unreachable');
    expect(warning?.textContent).toContain('581.3 mm instead of the requested 500.0 mm');
  });

  it('anchors wrapped dimensions in the timing header, separately from warnings, errors and stats', () => {
    render([UNREACHABLE], { error: 'Invalid draft', dimensions: true });
    const dimensions = host.querySelector('[data-testid="design-dimensions"]')!;
    const header = dimensions.closest('.viewport-header')!;
    expect(dimensions.closest('.viewport-live')).not.toBeNull();
    expect(header).not.toBeNull();
    expect(dimensions.textContent).toContain('Enclosure 500.0 × 400.0 × 200.0');
    for (const selector of ['.viewport-warning', '.viewport-error-banner']) {
      const banner = host.querySelector(selector)!;
      expect(banner).not.toBeNull();
      expect(header.contains(banner)).toBe(false);
    }
    const css = new TextDecoder().decode(readFileSync('src/viewport/viewport.css'));
    const rule = css.match(/\.viewport-dimensions \{([^}]+)\}/)![1];
    expect(rule).toContain('width: max-content');
    expect(rule).toContain('max-width: 100%');
    expect(rule).toContain('pointer-events: none');
    expect(rule).not.toMatch(/position: absolute|top:|right:/);
    expect(css).toContain('top: calc(var(--viewport-header-height, 34px) + 9px)');
    expect(css).toContain('.parametric-viewport .wg2-stats { top: calc(var(--viewport-header-height, 34px) + 18px)');
  });

  it('moves the HUD below the measured header when dimensions wrap', () => {
    let resize: () => void = () => {};
    const observe = vi.fn();
    const disconnect = vi.fn();
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { resize = callback; }
      observe = observe;
      disconnect = disconnect;
    });
    try {
      render([UNREACHABLE], { dimensions: true });
      const header = host.querySelector<HTMLElement>('.viewport-header')!;
      const viewport = host.querySelector<HTMLElement>('.wg2-viewport')!;
      expect(observe).toHaveBeenCalledWith(header);
      const bounds = vi.spyOn(header, 'getBoundingClientRect');
      bounds.mockReturnValue(new DOMRect(0, 0, 600, 86));
      act(() => resize());
      expect(viewport.style.getPropertyValue('--viewport-header-height')).toBe('86px');
      bounds.mockReturnValue(new DOMRect(0, 0, 1200, 34));
      act(() => resize());
      expect(viewport.style.getPropertyValue('--viewport-header-height')).toBe('34px');
      act(() => workspaceModeStore.setMode('cad'));
      expect(disconnect).toHaveBeenCalledOnce();
      expect(viewport.style.getPropertyValue('--viewport-header-height')).toBe('');
    } finally {
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    }
  });

  it('groups all geometry warnings under one compact indicator', () => {
    render([UNREACHABLE, 'canonical azimuth reference clamped to 4096 samples']);
    const warnings = host.querySelectorAll('.viewport-warning');
    expect(warnings).toHaveLength(1);
    expect(warnings[0]?.querySelector('summary')?.textContent).toContain('2 warnings');
    expect(warnings[0]?.querySelectorAll('li')).toHaveLength(2);
  });

  it('stays out of the way when the mesher reports nothing', () => {
    render(undefined);
    expect(host.querySelector('.viewport-warning')).toBeNull();
    act(() => root.unmount());
    host.remove();
    render([]);
    expect(host.querySelector('.viewport-warning')).toBeNull();
  });

  it('stays compact when a preview error is also present', () => {
    render([UNREACHABLE], { error: 'ATH expression is unsupported' });
    expect(host.querySelector('.viewport-error-banner')).not.toBeNull();
    expect(host.querySelector<HTMLDetailsElement>('.viewport-warning')?.open).toBe(false);
  });
});

describe('BEMPP wall plan-adjustment note', () => {
  let host: HTMLDivElement;
  let root: Root;

  function solvePlanWithAdjustments(adjustments: unknown[]) {
    return {
      engine: 'bempp',
      formulation: 'full-3d',
      reason: 'free-standing BEMPP solve',
      eligibility_reasons: [],
      adjustments,
    };
  }

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    workspaceModeStore.setMode('parametric');
    // Either solver slot may be selected while empty; the mesh view is what
    // makes the effect that reads `/api/solve/plan` active.
    importedMeshStore.showSolver();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
    vi.restoreAllMocks();
  });

  async function renderWithAdjustments(adjustments: unknown[]) {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input) === '/api/solve/plan') {
        return new Response(JSON.stringify(solvePlanWithAdjustments(adjustments)), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      // The solver-mesh build the same view activates is not under test here.
      return new Response('not found', { status: 404 });
    });
    act(() => root.render(<Viewport />));
    // The plan request is debounced (`SYMMETRY_TINT_DEBOUNCE_MS`), then awaited.
    await act(async () => {
      await new Promise((resolve) => { setTimeout(resolve, 450); });
    });
  }

  it('shows the override sentence for an explicit-zero wall', async () => {
    await renderWithAdjustments([{
      kind: 'bempp_wall_default',
      requested: 'explicit_zero',
      effective_mm: 5,
      reason_code: 'bempp_wall_default',
      policy_version: 1,
    }]);
    const note = host.querySelector('.solver-plan-adjustment-note');
    expect(note).not.toBeNull();
    expect(note?.textContent).toContain('0 mm wall thickness is overridden');
    expect(note?.textContent).toContain('5 mm closed wall');
  });

  it('shows the default sentence for an omitted wall', async () => {
    await renderWithAdjustments([{
      kind: 'bempp_wall_default',
      requested: 'omitted',
      effective_mm: 5,
      reason_code: 'bempp_wall_default',
      policy_version: 1,
    }]);
    const note = host.querySelector('.solver-plan-adjustment-note');
    expect(note).not.toBeNull();
    expect(note?.textContent).toContain('No wall thickness is set');
    expect(note?.textContent).toContain('5 mm closed-wall default');
  });

  it('plans again when the engine or accuracy changes', async () => {
    const bodies: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input) === '/api/solve/plan') {
        bodies.push(String(init?.body));
        return new Response(JSON.stringify(solvePlanWithAdjustments([])), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('not found', { status: 404 });
    });
    const settle = async () => act(async () => { await new Promise((resolve) => { setTimeout(resolve, 450); }); });
    act(() => root.render(<Viewport />));
    await settle();
    const first = bodies.length;
    expect(first).toBeGreaterThan(0);
    act(() => useSolveOptionsStore.getState().setEngine('bempp'));
    await settle();
    expect(bodies.length).toBeGreaterThan(first);
    expect(JSON.parse(bodies.at(-1)!).options.engine).toBe('bempp');
    const second = bodies.length;
    act(() => useSolveOptionsStore.getState().setAccuracy('accurate'));
    await settle();
    expect(bodies.length).toBeGreaterThan(second);
    expect(JSON.parse(bodies.at(-1)!).options.accuracy).toBe('accurate');
    useSolveOptionsStore.getState().setEngine('auto');
  });

  it('shows nothing when the plan has no adjustments', async () => {
    await renderWithAdjustments([]);
    expect(host.querySelector('.solver-plan-adjustment-note')).toBeNull();
  });
});

describe('solver-mesh refresh across a design load', () => {
  let host: HTMLDivElement;
  let root: Root;

  function solverMeshResponse(key: string) {
    return {
      msh_text: meshFixture,
      stats: { triangle_count: 1, vertex_count: 1, warnings: [], mesh_cache_key: key, mesh_cache_hit: false },
      cut_planes: [],
      quadrants: 1,
    };
  }

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    workspaceModeStore.setMode('parametric');
    importedMeshStore.showSolver();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
    vi.restoreAllMocks();
  });

  // `resetDesignStore` always rewinds the revision counter to the same value
  // (1), so a solver-mesh build already in flight when New replaces the
  // design is -- by revision number alone -- indistinguishable from one that
  // would answer the new document. `SolverMeshRefreshController.designChanged`
  // trusts the revision number and no-ops when it has not moved, so nothing
  // else asks for a fresh build either: without a separate identity, the
  // stale build's mesh would be shown and nothing would ever correct it.
  it('never shows a solver-mesh build answering the design New already replaced', async () => {
    let resolveOld!: (value: Response) => void;
    const oldResponse = new Promise<Response>((resolve) => { resolveOld = resolve; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => (
      String(input) === '/api/solver-mesh' ? oldResponse : new Response('not found', { status: 404 })
    ));

    act(() => root.render(<Viewport />));
    expect(importedMeshStore.getSnapshot().solver).toBeNull();

    act(() => resetDesignStore());

    await act(async () => {
      resolveOld(new Response(JSON.stringify(solverMeshResponse('old')), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(importedMeshStore.getSnapshot().solver).toBeNull();
  });
});

describe('camera refit', () => {
  const scene = (source: 'cad' | 'solver' | 'file', ingestId: string | null) =>
    createImportedMeshScene('Speaker', parseMSH(meshFixture), source, ingestId);

  it('re-frames for a new subject and holds the framing for a re-render of the same one', () => {
    // Nothing on screen yet, or a genuinely different model: frame it.
    expect(refitsCamera(null, scene('cad', 'wgi_a'))).toBe(true);
    expect(refitsCamera(scene('cad', 'wgi_a'), scene('cad', 'wgi_b'))).toBe(true);
    expect(refitsCamera(scene('file', null), scene('cad', 'wgi_a'))).toBe(true);

    // A solver mesh rebuilt after a parametric edit is the same subject.
    expect(refitsCamera(scene('solver', null), scene('solver', null))).toBe(false);

    // So is a CAD return whose solve mesh is replaced by the display
    // tessellation that finished building behind it.
    expect(refitsCamera(scene('cad', 'wgi_a'), scene('cad', 'wgi_a'))).toBe(false);

    // Two CAD scenes with no ingestion identity are not known to be the same
    // model, so they keep the safe answer rather than pinning a stale framing.
    expect(refitsCamera(scene('cad', null), scene('cad', null))).toBe(true);
  });
});

it('offers a new UI solve for per-band results without referring to a missing control', () => {
  const text = fieldPlaneUnavailableTooltip([{ status: 'complete', unavailable_reason: 'unsupported_per_band_mesh_ladder' } as JobItem]);
  expect(text).toContain('Load its design, enable Keep field plane data');
  expect(text).toContain('submit a new solve from the UI');
  expect(text).not.toContain('turn the per-band mesh ladder off');
});
