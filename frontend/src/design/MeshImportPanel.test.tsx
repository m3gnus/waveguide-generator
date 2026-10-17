import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ImportedSolveSubmission } from '../jobs/actions';
import type { RegisteredMesh } from '../api/meshImport';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useMeshImportStore } from '../stores/meshImport';
import { resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';
import { workspaceModeStore } from '../stores/workspaceMode';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { MeshImportPanel } from './MeshImportPanel';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const panel = () => <QueryClientProvider client={queryClient}><MeshImportPanel/></QueryClientProvider>;

const initial = useMeshImportStore.getState();
let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  useMeshImportStore.setState(initial, true);
  resetSolveOptionsStore();
  workspaceModeStore.setImportedInput('file');
  resetImportedModelStore();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ engines: [] }))));
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
  act(() => root.unmount());
  host.remove();
  useMeshImportStore.setState(initial, true);
  resetImportedModelStore();
});

/** The file model on screen, as `openImported` leaves it. */
function opened(state: Partial<ReturnType<typeof useImportedModelStore.getState>>) {
  useImportedModelStore.setState({ input: 'file', phase: 'ready', modelId: 'mesh-file:sha256:artifact', ...state });
}

it('shows actual replay frequencies and physics, with full resolved settings inspectable', () => {
  const replay: ImportedSolveSubmission = {
    geometry: { type: 'imported', ingest_id: 'wgi_replay', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact',
      drive_channels: [{ id: 'source', source_ids: ['source'], motion: 'normal' }], drive_voltage_v: 4.2, acknowledged_findings: [], skipped_source_ids: [] },
    options: { engine: 'metal', accuracy: 'accurate', symmetry: 'auto', solver_mode: 'auto',
      frequencies_hz: [137, 211, 3987], frequency_spacing: 'linear', mesh_validation_mode: 'strict', verbose: true,
      polar_config: { angle_range: [-60, 60, 13], angle_step: 10, distance: 3.7, norm_angle: 17, inclination: 45,
        enabled_axes: ['vertical'], observation_origin: 'mouth', spherical_sampling: false, field_plane: true } },
  };
  const { type: _t, ingest_id: _i, manifest_sha256: _m, artifact_sha256: _a, acknowledged_findings: _f, ...geometry } = replay.geometry;
  useMeshImportStore.setState({ filename: 'speaker.msh' });
  opened({ setup: { geometry, options: replay.options }, restoredRevisionId: 'wgs_recorded', originalAvailable: true,
    prepared: { ingest_id: 'wgi_replay', model_id: 'mesh-file:sha256:artifact', producer: 'mesh-file', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact',
      source_file: { filename: 'speaker.msh', transform: { units: 'mm', forward: '+z', up: '+y' }, source_tags: [2] },
      msh_text: '', source_tags: { source: 2 }, summary: { node_count: 4, triangle_count: 4, dimensions: [1, 1, 1], groups: [] } } as RegisteredMesh });
  act(() => root.render(panel()));
  const summary = host.querySelector('[aria-label="Exact mesh replay"]')!;
  expect(summary.textContent).toContain('3 explicit frequencies: 137, 211, 3987 Hz');
  expect(summary.textContent).toContain('vertical polars · 10° steps · 3.7 m');
  expect(summary.textContent).toContain('Engine: metal · Accuracy: accurate');
  expect(host.textContent).not.toContain('No electrical driver model or crossover is applied.');
  expect(host.textContent).not.toContain('starts as a copy of the parametric settings');
  expect(host.querySelector('details summary')?.textContent).toBe('Full resolved setup');
  expect(JSON.parse(host.querySelector('[aria-label="Full resolved replay setup"]')!.textContent!)).toEqual({ geometry, options: replay.options });
  expect(summary.textContent).toContain('the setup revision this mesh was opened with');
  expect(host.textContent).toContain('New setup controls');
  act(() => useImportedSolveOptionsStore.getState().setSweep({ count: 29 }));
  expect(host.querySelector('[aria-label="Exact mesh replay"]')).toBeNull();
  expect(useImportedModelStore.getState().restoredRevisionId).toBeNull();
  expect(host.textContent).toContain('starts as a copy of the parametric settings');
});


it('offers arbitrary labels as independent radiating surfaces and invalidates prepared assignments', () => {
  opened({ fileDraft: { units: 'mm', forward: '+z', up: '+y', selected: [17] },
    prepared: { ingest_id: 'wgi_labels', summary: {}, source_tags: { group_17: 17 } } as unknown as RegisteredMesh });
  useMeshImportStore.setState({ filename: 'labels.msh', original: 'mesh',
    summary: { node_count: 6, triangle_count: 9, dimensions: [1, 1, 1], groups: [
      { tag: 17, name: 'Cone patch', triangle_count: 3 }, { tag: 42, name: 'Rear diaphragm', triangle_count: 4 },
      { tag: 0, name: 'Unassigned', triangle_count: 2 }] } });
  act(() => root.render(panel()));
  const section = [...host.querySelectorAll('fieldset')].find(field => field.querySelector('legend')?.textContent === 'Radiating surfaces')!;
  expect(section.textContent).toContain('unchecked labels are rigid');
  const boxes = section.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
  expect(boxes[0].checked).toBe(true);
  expect(boxes[2].disabled).toBe(true);
  act(() => boxes[1].click());
  expect(useImportedModelStore.getState().fileDraft.selected).toEqual([17, 42]);
  expect(useImportedModelStore.getState().prepared).toBeNull();
  act(() => { boxes[0].click(); boxes[1].click(); });
  expect(section.textContent).toContain('No radiating surfaces selected');
  expect([...host.querySelectorAll('button')].find(button => button.textContent === 'Prepare mesh')?.disabled).toBe(true);
});

it('shows retained radiating assignments with a reimport requirement only when the original is gone', () => {
  opened({ originalAvailable: false, fileDraft: { units: 'mm', forward: '+z', up: '+y', selected: [42] },
    prepared: { ingest_id: 'wgi_retained', source_tags: { group_42: 42 }, summary: {} } as unknown as RegisteredMesh });
  useMeshImportStore.setState({ filename: 'retained.msh',
    summary: { node_count: 6, triangle_count: 4, dimensions: [1, 1, 1], groups: [
      { tag: 42, name: 'Rear diaphragm', triangle_count: 4 }] } });
  act(() => root.render(panel()));
  const section = [...host.querySelectorAll('fieldset')].find(field => field.querySelector('legend')?.textContent === 'Radiating surfaces')!;
  expect(section.disabled).toBe(true);
  expect(section.querySelector<HTMLInputElement>('input')?.checked).toBe(true);
  expect(section.textContent).toContain('Import the original mesh to change them');
  expect(host.querySelector('[aria-label="Mesh units"]')).toBeNull();
  expect([...host.querySelectorAll('button')].some(button => button.textContent?.startsWith('Prepare'))).toBe(false);
});

it('keeps a restored file editable while the server retains its original', () => {
  opened({ originalAvailable: true, fileDraft: { units: 'mm', forward: '+z', up: '+y', selected: [42] },
    prepared: { ingest_id: 'wgi_retained', source_tags: { group_42: 42 }, summary: {} } as unknown as RegisteredMesh });
  // Restored from a run: this browser never read the original.
  useMeshImportStore.setState({ filename: 'retained.msh', original: null,
    summary: { node_count: 6, triangle_count: 9, dimensions: [1, 1, 1], groups: [
      { tag: 42, name: 'Rear diaphragm', triangle_count: 4 }, { tag: 7, name: 'Port', triangle_count: 5 }] } });
  act(() => root.render(panel()));
  const section = [...host.querySelectorAll('fieldset')].find(field => field.querySelector('legend')?.textContent === 'Radiating surfaces')!;
  expect(section.disabled).toBe(false);
  expect(section.textContent).not.toContain('Import the original mesh');
  const units = host.querySelector<HTMLSelectElement>('[aria-label="Mesh units"]')!;
  expect(units.value).toBe('mm');
  choose(units, 'cm');
  expect(useImportedModelStore.getState().fileDraft.units).toBe('cm');
  act(() => section.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')[1].click());
  expect(useImportedModelStore.getState().fileDraft.selected).toEqual([42, 7]);
  const prepare = [...host.querySelectorAll('button')].find(button => button.textContent === 'Prepare mesh')!;
  expect(prepare.disabled).toBe(false);
});

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
function type(area: HTMLTextAreaElement | HTMLInputElement, value: string) {
  act(() => {
    const proto = area instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

it('shows the solve controls in the file rail and writes edits to the imported options', () => {
  useMeshImportStore.setState({ filename: 'speaker.msh' });
  opened({ prepared: { ingest_id: 'wgi_f', source_tags: { group_2: 2 }, summary: {} } as unknown as RegisteredMesh });
  act(() => root.render(panel()));
  // Sweep range, engine and accuracy, spacing, and the directivity rig are all on screen.
  expect(host.querySelector('[aria-label="Mesh sweep start"]')).not.toBeNull();
  expect(host.querySelector('#solve-accuracy')).not.toBeNull();
  expect(host.querySelector('#cad-solve-engine')).not.toBeNull();
  expect(host.querySelector('#polar-distance')).not.toBeNull();
  // The rig on screen is the one a new file starts with.
  expect((host.querySelector('#polar-distance') as HTMLInputElement).value).toBe(String(useImportedSolveOptionsStore.getState().polar.distance));
  choose(host.querySelector<HTMLSelectElement>('#cad-solve-frequency-spacing')!, 'linear');
  expect(useImportedSolveOptionsStore.getState().frequencySpacing).toBe('linear');
  choose(host.querySelector<HTMLSelectElement>('#solve-accuracy')!, 'accurate');
  expect(useImportedSolveOptionsStore.getState().accuracy).toBe('accurate');
  type(host.querySelector<HTMLInputElement>('[aria-label="Mesh frequency count"]')!, '21');
  expect(useImportedSolveOptionsStore.getState().sweepCount).toBe(21);
  // The parametric options are not the ones edited.
  expect(useSolveOptionsStore.getState().frequencySpacing).not.toBe('linear');
  // List mode replaces the range inputs.
  choose(host.querySelector<HTMLSelectElement>('#cad-solve-frequency-mode')!, 'list');
  expect(host.querySelector('[aria-label="Mesh sweep start"]')).toBeNull();
  type(host.querySelector<HTMLTextAreaElement>('#cad-solve-frequency-list')!, '300 700 1500');
  expect(useImportedSolveOptionsStore.getState().frequencyListText).toBe('300 700 1500');
});
