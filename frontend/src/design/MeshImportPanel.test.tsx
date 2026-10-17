import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ImportedSolveSubmission } from '../jobs/actions';
import type { RegisteredMesh } from '../api/meshImport';
import { useMeshImportStore } from '../stores/meshImport';
import { MeshImportPanel } from './MeshImportPanel';

vi.mock('../jobs/useCapabilities', () => ({ useCapabilities: () => ({ engines: [] }) }));

const initial = useMeshImportStore.getState();
let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  useMeshImportStore.setState(initial, true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  useMeshImportStore.setState(initial, true);
});

it('shows actual replay frequencies and physics, with full resolved settings inspectable', () => {
  const replay: ImportedSolveSubmission = {
    geometry: { type: 'imported', ingest_id: 'wgi_replay', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact',
      drive_channels: [{ id: 'source', source_ids: ['source'], motion: 'normal' }], drive_voltage_v: 4.2, acknowledged_findings: [], skipped_source_ids: [] },
    options: { engine: 'metal', accuracy: 'accurate', symmetry: 'auto', solver_mode: 'auto',
      frequencies_hz: [137, 211, 3987], frequency_spacing: 'linear', mesh_validation_mode: 'strict', verbose: true,
      polar_config: { angle_range: [-60, 60, 13], angle_step: 10, distance: 3.7, norm_angle: 17, inclination: 45,
        enabled_axes: ['vertical'], observation_origin: 'mouth', spherical_sampling: false, field_plane: true } },
  };
  useMeshImportStore.setState({ exactSubmission: replay, filename: 'speaker.msh', engine: 'metal', accuracy: 'accurate',
    registered: { ingest_id: 'wgi_replay', producer: 'mesh-file', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact',
      source_file: { filename: 'speaker.msh', transform: { units: 'mm', forward: '+z', up: '+y' }, source_tags: [2] },
      msh_text: '', source_tags: { source: 2 }, summary: { node_count: 4, triangle_count: 4, dimensions: [1, 1, 1], groups: [] } } satisfies RegisteredMesh });
  act(() => root.render(<MeshImportPanel/>));
  const summary = host.querySelector('[aria-label="Exact mesh replay"]')!;
  expect(summary.textContent).toContain('3 explicit frequencies: 137, 211, 3987 Hz');
  expect(summary.textContent).toContain('vertical polars · 10° steps · 3.7 m');
  expect(summary.textContent).toContain('Engine: metal · Accuracy: accurate');
  expect(host.textContent).not.toContain('No electrical driver model or crossover is applied.');
  expect(host.textContent).not.toContain('Full domain, 5° horizontal/vertical/diagonal polars at 2 m');
  expect(host.querySelector('details summary')?.textContent).toBe('Full resolved setup');
  expect(JSON.parse(host.querySelector('[aria-label="Full resolved replay setup"]')!.textContent!)).toEqual(replay);
  expect(host.textContent).toContain('New setup controls');
  act(() => useMeshImportStore.setState({ count: 29 }));
  expect(host.querySelector('[aria-label="Exact mesh replay"]')).toBeNull();
  expect(host.textContent).toContain('Logarithmic sweep.');
  expect(host.textContent).toContain('starts a new setup');
});


it('offers arbitrary labels as independent radiating surfaces and invalidates prepared assignments', () => {
  useMeshImportStore.setState({ filename: 'labels.msh', original: 'mesh', units: 'mm', selected: [17],
    registered: { summary: {}, source_tags: { group_17: 17 } } as unknown as RegisteredMesh,
    summary: { node_count: 6, triangle_count: 9, dimensions: [1, 1, 1], groups: [
      { tag: 17, name: 'Cone patch', triangle_count: 3 }, { tag: 42, name: 'Rear diaphragm', triangle_count: 4 },
      { tag: 0, name: 'Unassigned', triangle_count: 2 }] } });
  act(() => root.render(<MeshImportPanel/>));
  const section = [...host.querySelectorAll('fieldset')].find(field => field.querySelector('legend')?.textContent === 'Radiating surfaces')!;
  expect(section.textContent).toContain('unchecked labels are rigid');
  const boxes = section.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
  expect(boxes[0].checked).toBe(true);
  expect(boxes[2].disabled).toBe(true);
  act(() => boxes[1].click());
  expect(useMeshImportStore.getState().selected).toEqual([17, 42]);
  expect(useMeshImportStore.getState().registered).toBeNull();
  act(() => { boxes[0].click(); boxes[1].click(); });
  expect(section.textContent).toContain('No radiating surfaces selected');
  expect([...host.querySelectorAll('button')].find(button => button.textContent === 'Prepare mesh')?.disabled).toBe(true);
});

it('shows retained radiating assignments with a clear reimport requirement', () => {
  useMeshImportStore.setState({ filename: 'retained.msh', selected: [42],
    registered: { source_tags: { group_42: 42 }, summary: {} } as unknown as RegisteredMesh,
    summary: { node_count: 6, triangle_count: 4, dimensions: [1, 1, 1], groups: [
      { tag: 42, name: 'Rear diaphragm', triangle_count: 4 }] } });
  act(() => root.render(<MeshImportPanel/>));
  const section = [...host.querySelectorAll('fieldset')].find(field => field.querySelector('legend')?.textContent === 'Radiating surfaces')!;
  expect(section.disabled).toBe(true);
  expect(section.querySelector<HTMLInputElement>('input')?.checked).toBe(true);
  expect(section.textContent).toContain('Import the original mesh to change them');
});
