import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DecodedFrame } from '../api/frame';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { PreviewSnapshot } from '../api/previewSocket';
import { compareSelection } from '../api/results';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { createImportedMeshScene } from './importedMesh';
import { importedMeshStore } from './importedMeshStore';
import { parseMSH } from './mshParser';
import { useFieldPlaneStore } from './fieldPlaneStore';
import { useFieldPlaneProbeStore } from './fieldPlaneProbe';
import meshFixture from './test-fixtures/tagged_sources-small.msh?raw';

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

import { cadViewportEmptyCopy, Viewport } from './Viewport';

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
    expect(host.querySelector<HTMLButtonElement>('[aria-label="Section cut at X=0"]')).not.toBeNull();
    expect(host.querySelectorAll('.viewport-tools .viewport-tool-group:empty')).toHaveLength(0);
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
    expect(host.querySelector('.field-plane-legend-title span')?.textContent).toBe('1,000 Hz');

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
    expect(noticeText()).toBe('requested 1,000 Hz → showing 987 Hz');

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

  const render = (warnings: string[] | undefined, { error = null as string | null } = {}) => {
    previewSnapshot.error = error;
    frame.header.previewMetadata = warnings ? { warnings } : undefined;
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
