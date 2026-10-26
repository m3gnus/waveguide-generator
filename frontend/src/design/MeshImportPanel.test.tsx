import { useDriverLibraryStore } from '../stores/driverLibrary';
import { readFileSync } from 'node:fs';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ImportedSolveSubmission } from '../jobs/actions';
import type { RegisteredMesh } from '../api/meshImport';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expectNoJargon } from '../testing/expectNoJargon';
import { editMeshImport, loadMeshFile, useMeshImportStore } from '../stores/meshImport';
import { openImported, resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';
import { workspaceModeStore } from '../stores/workspaceMode';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { CAPABILITIES_QUERY_KEY } from '../jobs/useCapabilities';
import { preferencesStore } from '../prefs/preferences';
import { MeshImportPanel } from './MeshImportPanel';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const panel = () => <QueryClientProvider client={queryClient}><MeshImportPanel tab="geometry"/><MeshImportPanel tab="simulation"/></QueryClientProvider>;

const initial = useMeshImportStore.getState();
let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  useMeshImportStore.setState(initial, true);
  resetSolveOptionsStore();
  preferencesStore.update({ showDeveloperDetails: false });
  useDriverLibraryStore.setState({ info: null, status: 'ready' });
  workspaceModeStore.setImportedInput('file');
  resetImportedModelStore();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ engines: [] }))));
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  const geometry = host.querySelector('[aria-label="Imported mesh geometry"]');
  if (geometry) expectNoJargon(geometry);
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
  expect(summary.querySelector('.developer-details')).toBeNull();
  act(() => preferencesStore.update({ showDeveloperDetails: true }));
  expect(summary.querySelector('details summary')?.textContent).toBe('Full resolved setup');
  expect(JSON.parse(host.querySelector('[aria-label="Full resolved replay setup"]')!.textContent!)).toEqual({ geometry, options: replay.options });
  expect(summary.textContent).toContain('the setup revision this mesh was opened with');
  expect(host.querySelectorAll('.cad-channel-list')).toHaveLength(1);
  act(() => useImportedSolveOptionsStore.getState().setSweep({ count: 29 }));
  expect(host.querySelector('[aria-label="Exact mesh replay"]')).toBeNull();
  expect(useImportedModelStore.getState().restoredRevisionId).toBeNull();
  expect(host.textContent).not.toContain('Full domain, free space');
  preferencesStore.update({ showDeveloperDetails: false });
});


const fileSummary = {
  node_count: 420, triangle_count: 9730, dimensions: [.1845, .1845, .3], suggested_units: 'm',
  detected_planes: ['x0', 'y0'], kept_sides: { x0: 'positive' as const, y0: 'positive' as const },
  groups: [
    { tag: 101, name: 'wg-import-v1|tag=101|role=HF', display_name: 'HF source (101)', suggested_role: 'HF', role_from: 'file' as const, triangle_count: 60 },
    { tag: 102, name: 'wg-import-v1|tag=102|role=MF', display_name: 'MF source (102)', suggested_role: 'MF', role_from: 'file' as const, triangle_count: 221 },
    { tag: 0, name: 'Untagged surface', display_name: 'Untagged surface', suggested_role: 'RIGID', role_from: 'default' as const, triangle_count: 9449 },
  ],
};
function geometry({ original = true, prepared = false } = {}) {
  useMeshImportStore.setState({ filename: 'speaker.msh', original: original ? 'mesh' : null, summary: fileSummary });
  opened({ originalAvailable: original, fileDraft: { units: 'm', forward: '+z', up: '+y', selected: [101, 102],
    roles: { 101: 'HF', 102: 'MF', 0: 'RIGID' }, domainPlanes: ['x0', 'y0'], reflectPlanes: null },
    prepared: prepared ? { ingest_id: 'wgi_file', summary: fileSummary, source_tags: { group_101: 101, group_102: 102 } } as unknown as RegisteredMesh : null });
  act(() => root.render(<MeshImportPanel tab="geometry"/>));
}
function button(label: string) { return host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!; }

it('shows descriptor roles, the quarter size and an editable untagged wall without a Prepare button', () => {
  geometry({ prepared: true });
  const roles = host.querySelectorAll<HTMLSelectElement>('select');
  expect([...roles].map(select => select.value)).toEqual(['HF', 'MF', 'RIGID']);
  expect([...roles[0].options].map(option => option.textContent)).toEqual(['HF driver', 'MF driver', 'LF driver', 'Port or passive opening', 'Rigid wall']);
  expect(host.textContent).toContain('Quarter model · open at x = 0 and y = 0');
  expect(host.textContent).toContain('369 × 369 × 300 mm · file in metres');
  expect(host.textContent).toContain('Ready to solve · 9,730 triangles · quarter model');
  expect(host.textContent).not.toContain('wg-import-v1');
  expect(roles[2].disabled).toBe(false);
  expect(host.querySelector('.cad-model-details .section-head')?.getAttribute('aria-expanded')).toBe('false');
  expect(host.textContent).toContain('Faces +z · up +y');
  expect(host.textContent).toContain('Roles: from the file');
  expect([...host.querySelectorAll('button')].some(node => /Prepare|Try again/.test(node.textContent ?? ''))).toBe(false);
  expectNoJargon(host);
});

it.each(['inspect', 'parse'] as const)('shows a %s refusal without an error prefix or Try again', async (stage) => {
  const inspect = vi.fn<typeof fetch>(async () => stage === 'inspect'
    ? new Response(JSON.stringify({ detail: 'The mesh contains no triangles.' }), { status: 422 })
    : new Response(JSON.stringify(fileSummary)));
  let refusal: Error | null = null;
  await act(async () => {
    await loadMeshFile('broken.msh', 'not a mesh', inspect).catch(error => { refusal = error; });
  });
  expect(refusal).toBeInstanceOf(Error);
  act(() => root.render(<MeshImportPanel tab="geometry"/>));
  expect(host.querySelector('.cad-status-line')?.textContent).toBe(refusal!.message);
  expect(useMeshImportStore.getState().error).toBe(refusal!.message);
  expect(host.querySelector('.cad-status-line button')).toBeNull();
  expect(inspect).toHaveBeenCalledOnce();
  expectNoJargon(host);
});

it('shows one status action, and Try again only after a preparation problem', async () => {
  geometry({ prepared: true });
  expect(host.querySelectorAll('.cad-status-line button')).toHaveLength(1);
  expect(host.querySelector('.cad-status-line button')?.textContent).toBe('Solve');
  act(() => useMeshImportStore.setState({ busy: true }));
  expect(host.querySelector('.cad-status-line')?.textContent).toContain('Preparing speaker.msh…');
  expect(host.querySelectorAll('.cad-status-line button')).toHaveLength(0);
  expectNoJargon(host);
  act(() => useMeshImportStore.setState({ busy: false, error: 'The mesh has an opening. Close it in the meshing tool.' }));
  expect(host.querySelector('.cad-status-line')?.textContent).toContain('The mesh has an opening.');
  expect(host.querySelectorAll('.cad-status-line button')).toHaveLength(1);
  expect(host.querySelector('.cad-status-line button')?.textContent).toBe('Try again');
  expectNoJargon(host);
});

it('asks only for missing units and marks the choice at its field', () => {
  geometry();
  act(() => useImportedModelStore.setState(state => ({ fileDraft: { ...state.fileDraft, units: '' } })));
  expect(host.querySelector('.cad-status-line')?.textContent).toBe("Choose the file's units.");
  expect(host.querySelector('[aria-label="Mesh units"]')?.getAttribute('aria-invalid')).toBe('true');
  expect(host.querySelectorAll('.cad-status-line button')).toHaveLength(0);
  expectNoJargon(host);
});

it('keeps raw names and tags behind the existing developer setting', () => {
  geometry({ prepared: true });
  expect(host.querySelector('.developer-details')).toBeNull();
  act(() => preferencesStore.update({ showDeveloperDetails: true }));
  expect(host.querySelector('.developer-details')?.textContent).toContain('wg-import-v1');
  expectNoJargon(host);
  act(() => preferencesStore.update({ showDeveloperDetails: false }));
});

it('offers symmetry, mirror, size and axes changes in their owning controls', () => {
  geometry();
  act(() => useMeshImportStore.setState({ summary: { ...fileSummary, kept_sides: { x0: 'negative', y0: 'positive' } } }));
  expect(host.textContent).toContain('mirrored from the negative side');
  act(() => button('Change symmetry').click());
  expect([...host.querySelectorAll<HTMLOptionElement>('[aria-label="Mesh symmetry"] option')].map(option => option.textContent))
    .toEqual(['Full model', 'Half x = 0', 'Half y = 0', 'Quarter']);
  expect(host.querySelector<HTMLInputElement>('[aria-label="Mirror from the negative side"]')?.checked).toBe(true);
  expectNoJargon(host);
  act(() => button('Change size').click());
  expect(host.querySelector<HTMLSelectElement>('[aria-label="Mesh units"]')?.value).toBe('m');
  act(() => button('Change axes').click());
  expect(host.querySelector<HTMLSelectElement>('[aria-label="Mesh forward axis"]')?.value).toBe('+z');
  expect(host.querySelector<HTMLSelectElement>('[aria-label="Mesh up axis"]')?.value).toBe('+y');
  expectNoJargon(host);
});

it('automatically prepares role, mirror, size and axis edits and retries only the failed preparation', async () => {
  const prepare = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? new Response(JSON.stringify(fileSummary))
    : new Response(JSON.stringify({ detail: 'Close the opening in the meshing tool.' }), { status: 422 }));
  const otherRequests = globalThis.fetch;
  // Shared sections may flush durable UI settings while a preparation is
  // awaited. Count actual mesh requests rather than unrelated settings writes.
  vi.stubGlobal('fetch', ((url, init) => String(url).startsWith('/api/models/files')
    ? prepare(url, init) : otherRequests(url, init)) as typeof fetch);
  const preparations = () => prepare.mock.calls.filter(([url]) => String(url) === '/api/models/files');
  geometry();
  act(() => useMeshImportStore.setState({ summary: { ...fileSummary, kept_sides: { x0: 'negative', y0: 'positive' } } }));
  await act(async () => choose(host.querySelector<HTMLSelectElement>('[aria-label="Role for HF source (101)"]')!, 'LF'));
  expect(JSON.parse(preparations()[0][1]?.body as string).roles).toEqual({ 0: 'RIGID', 101: 'LF', 102: 'MF' });
  expect(host.querySelector('.cad-status-line')?.textContent).toContain('Close the opening');
  expectNoJargon(host);
  await act(async () => host.querySelector<HTMLButtonElement>('.cad-status-line button')!.click());
  expect(preparations()).toHaveLength(2);
  act(() => button('Change symmetry').click());
  await act(async () => host.querySelector<HTMLInputElement>('[aria-label="Mirror from the negative side"]')!.click());
  expect(JSON.parse(preparations()[2][1]?.body as string).reflect_planes).toEqual([]);
  expect(host.textContent).toContain('kept on the negative side');
  expectNoJargon(host);
  act(() => button('Change size').click());
  await act(async () => choose(host.querySelector<HTMLSelectElement>('[aria-label="Mesh units"]')!, 'cm'));
  expect(JSON.parse(preparations()[3][1]?.body as string).units).toBe('cm');
  act(() => button('Change axes').click());
  await act(async () => choose(host.querySelector<HTMLSelectElement>('[aria-label="Mesh up axis"]')!, '+x'));
  expect(JSON.parse(preparations()[4][1]?.body as string)).toMatchObject({ up: '+x', domain_planes: ['x0', 'y0'] });
  expect(host.querySelectorAll('.cad-status-line button')).toHaveLength(1);
  expectNoJargon(host);
});

it('keeps a retained mesh read-only only when its original is gone', () => {
  geometry({ original: false, prepared: true });
  expect(host.querySelector('fieldset')?.disabled).toBe(true);
  expect(button('Change size').disabled).toBe(true);
  expect(host.textContent).toContain('Import the original mesh to change its settings.');
  act(() => useImportedModelStore.setState({ originalAvailable: true }));
  expect(host.querySelector('fieldset')?.disabled).toBe(false);
  expect(button('Change size').disabled).toBe(false);
  expectNoJargon(host);
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
  opened({ prepared: { ingest_id: 'wgi_f', source_tags: { group_2: 2 }, summary: fileSummary } as unknown as RegisteredMesh });
  act(() => root.render(panel()));
  // Sweep range, engine and accuracy, spacing, and the directivity rig are all on screen.
  expect(host.querySelector('[data-control-reveal-id="cad.frequency.start"] input')).not.toBeNull();
  expect(host.querySelector('#solve-accuracy')).not.toBeNull();
  expect(host.querySelector('#cad-solve-engine')).not.toBeNull();
  expect(host.querySelector('#polar-distance')).not.toBeNull();
  // The rig on screen is the one a new file starts with.
  expect((host.querySelector('#polar-distance') as HTMLInputElement).value).toBe(String(useImportedSolveOptionsStore.getState().polar.distance));
  choose(host.querySelector<HTMLSelectElement>('#cad-solve-frequency-spacing')!, 'linear');
  expect(useImportedSolveOptionsStore.getState().frequencySpacing).toBe('linear');
  choose(host.querySelector<HTMLSelectElement>('#solve-accuracy')!, 'accurate');
  expect(useImportedSolveOptionsStore.getState().accuracy).toBe('accurate');
  const count = host.querySelector<HTMLInputElement>('[data-control-reveal-id="cad.frequency.samples"] input')!;
  act(() => count.focus());
  type(count, '21');
  act(() => count.blur());
  expect(useImportedSolveOptionsStore.getState().sweepCount).toBe(21);
  // The parametric options are not the ones edited.
  expect(useSolveOptionsStore.getState().frequencySpacing).not.toBe('linear');
  // List mode replaces the range inputs.
  choose(host.querySelector<HTMLSelectElement>('#cad-solve-frequency-mode')!, 'list');
  expect(host.querySelector('[data-control-reveal-id="cad.frequency.start"] input')).toBeNull();
  type(host.querySelector<HTMLTextAreaElement>('#cad-solve-frequency-list')!, '300 700 1500');
  expect(useImportedSolveOptionsStore.getState().frequencyListText).toBe('300 700 1500');
});

it('shows the file sweep range error beside the sweep fields', () => {
  opened({ prepared: { ingest_id: 'wgi_f', source_tags: { group_2: 2 }, summary: fileSummary } as unknown as RegisteredMesh });
  act(() => root.render(panel()));
  act(() => useImportedSolveOptionsStore.getState().setSweep({ startHz: 1000, endHz: 500 }));
  expect(host.querySelector('[data-section="Sweep"] [role="alert"]')?.textContent)
    .toBe('Enter an increasing frequency range and 2–401 frequencies.');
  act(() => useImportedSolveOptionsStore.getState().setSweep({ endHz: 2000 }));
  expect(host.querySelector('[data-section="Sweep"] [role="alert"]')).toBeNull();
});

it('offers the shared passive cardioid controls for a file aperture role', () => {
  opened({ prepared: { ingest_id: 'wgi_f', source_tags: { cone: 1, opening: 2 }, summary: fileSummary,
    sources: [{ id: 'cone', role: 'MF' }, { id: 'opening', role: 'PASSIVE_CARDIOID' }] } as unknown as RegisteredMesh });
  act(() => root.render(panel()));
  const section = host.querySelector('[data-section="Passive cardioid"]')!;
  expect(section).not.toBeNull();
  const toggle = section.querySelector<HTMLInputElement>('#cad-passive-cardioid')!;
  act(() => toggle.click());
  expect(useImportedModelStore.getState().fileSimulation?.passiveCardioid.enabled).toBe(true);
  expect(section.querySelector('[role="alert"]')?.textContent).toContain('Passive cardioid needs');
});

it.each([false, true])('retains the file mesh engine list and adaptive hint (developer: %s)', (developer) => {
  preferencesStore.update({ showDeveloperDetails: developer });
  queryClient.setQueryData(CAPABILITIES_QUERY_KEY, { engines: [
    { name: 'dry-run', label: 'Dry run', available: true, reason: null, version: null, fast_paths: [], geometry_sources: ['parametric'] },
  ] });
  useImportedSolveOptionsStore.getState().setSweep({ count: 10 });
  act(() => root.render(panel()));
  expect(host.querySelector<HTMLOptionElement>('#cad-solve-engine option[value="dry-run"]')?.disabled).toBe(true);
  expect(host.querySelector('#adaptive-frequency-sampling')).not.toBeNull();
  expect(host.textContent).toContain('Adaptive sampling requires at least 24 frequencies.');
  preferencesStore.update({ showDeveloperDetails: false });
});

it('offers closed-face removal in the existing symmetry menu and prepares the choice', async () => {
  const prepare = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ detail: 'Close the opening in the meshing tool.' }), { status: 422 }));
  vi.stubGlobal('fetch', prepare);
  geometry();
  act(() => useMeshImportStore.setState({ summary: { ...fileSummary, lids: {
    x0: { candidate: true, triangle_count: 2, groups: [{ tag: 0, triangle_count: 2 }] },
    y0: { candidate: false, triangle_count: 2, groups: [{ tag: 0, triangle_count: 2 }] },
  } } }));
  act(() => button('Change symmetry').click());
  const removal = [...host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')]
    .find(input => input.parentElement?.textContent?.includes('Remove the closed face on x = 0'))!;
  expect(removal).toBeDefined();
  expect(host.textContent).not.toContain('Remove the closed face on y = 0');
  expectNoJargon(host);
  await act(async () => removal.click());
  const registrations = () => prepare.mock.calls.filter(([url]) => String(url) === '/api/models/files');
  expect(JSON.parse(registrations()[0][1]?.body as string).remove_lid_planes).toEqual(['x0']);
  await act(async () => choose(host.querySelector<HTMLSelectElement>('[aria-label="Mesh symmetry"]')!, ''));
  expect(JSON.parse(registrations()[1][1]?.body as string).remove_lid_planes).toEqual([]);
});

it('shows removed closed faces in Model details', () => {
  geometry({ prepared: true });
  act(() => useImportedModelStore.setState(state => ({ prepared: {
    ...(state.prepared as RegisteredMesh), source_file: { ...(state.prepared as RegisteredMesh).source_file,
      transform: { units: 'm', forward: '+z', up: '+y', removed_lids: { x0: { triangle_count: 2, groups: [{ tag: 0, triangle_count: 2 }] } } },
    },
  } as RegisteredMesh })));
  expect(host.textContent).toContain('Removed the closed face on x = 0 · 2 triangles');
  expectNoJargon(host);
});

it('shows remembered settings once in the status line and has no Save setup button', () => {
  geometry({ prepared: true });
  act(() => useMeshImportStore.setState({ notice: 'Ready to solve · settings from your last import' }));
  expect(host.querySelector('.cad-status-line')?.textContent).toBe('Ready to solve · settings from your last importSolve');
  expect(host.textContent).not.toContain('Save setup');
  expectNoJargon(host);
  act(() => useMeshImportStore.setState({ notice: 'Using the settings from speaker.msh (09:31)' }));
  expect(host.querySelector('.cad-status-line')?.textContent).toBe('Using the settings from speaker.msh (09:31)Solve');
  expectNoJargon(host);
});

it('shows a shared setup-save failure in the file status line', async () => {
  const { useCadSetupPublisherStore } = await import('../shell/cadSetupPublisher');
  geometry({ prepared: true });
  act(() => useCadSetupPublisherStore.getState().record('failed', { input: 'file', modelId: useImportedModelStore.getState().modelId }));
  expect(host.querySelector('.cad-status-line')?.textContent).toBe('Settings not saved. Edit a setting to retry.');
  expect(host.querySelector('.cad-status-line button')).toBeNull();
  expectNoJargon(host);
  act(() => useCadSetupPublisherStore.setState({ errors: {} }));
});


it('shows only save errors belonging to this file model', async () => {
  const { useCadSetupPublisherStore } = await import('../shell/cadSetupPublisher');
  geometry({ prepared: true });
  const modelId = useImportedModelStore.getState().modelId;
  act(() => {
    const { record } = useCadSetupPublisherStore.getState();
    record('failed', { input: 'cad', modelId });
    record('failed', { input: 'file', modelId: 'mesh-file:other' });
  });
  expect(host.querySelector('.cad-status-line')?.textContent).toContain('Ready to solve');
  act(() => useCadSetupPublisherStore.getState().record('failed', { input: 'file', modelId }));
  expect(host.querySelector('.cad-status-line')?.textContent).toBe('Settings not saved. Edit a setting to retry.');
  act(() => useCadSetupPublisherStore.getState().record('saved', { input: 'file', modelId }));
  expect(host.querySelector('.cad-status-line')?.textContent).toContain('Ready to solve');
  expect(Object.keys(useCadSetupPublisherStore.getState().errors)).toHaveLength(2);
  act(() => useCadSetupPublisherStore.setState({ errors: {} }));
});

it('renders one shared Drivers section and edits file drivers through the shared picker', () => {
  const mesh = { ingest_id: 'wgi_roles', source_tags: { left: 1, right: 2, high: 3 }, summary: fileSummary,
    sources: [{ id: 'left', role: 'MF' }, { id: 'right', role: 'MF' }, { id: 'high', role: 'HF' }],
  } as unknown as RegisteredMesh;
  opened({ prepared: mesh });
  act(() => root.render(panel()));
  expect(host.querySelectorAll('[data-section="Drivers"]')).toHaveLength(1);
  expect(host.querySelectorAll('.cad-channel')).toHaveLength(2);
  expect(host.textContent).toContain('2 surfaces · identical drivers');
  expect(host.querySelector<HTMLInputElement>('[aria-label="Solve driver in BEAT for drive-mf::left"]')?.disabled).toBe(true);
  expect(host.textContent).toContain('Drive the surfaces separately to model each driver in BEAT.');
  type(host.querySelector<HTMLInputElement>('[aria-label="Sd for drive-mf::left"]')!, '90');
  expect(useImportedModelStore.getState().fileSimulation?.channelDrivers['drive-mf::right'].fields.sd_cm2).toBe(90);
  const sharing = host.querySelector<HTMLSelectElement>('[aria-label^="Driver sharing for"]')!;
  expect(sharing.closest('details')).toBeNull();
  expect(sharing.value).toBe('share');
  expect([...sharing.options].map(option => option.textContent)).toEqual(['Share one driver', 'Drive separately']);
  choose(sharing, 'separate');
  expect(host.querySelectorAll('.cad-channel')).toHaveLength(3);
  expect(host.querySelector<HTMLInputElement>('[aria-label="Solve driver in BEAT for left"]')?.disabled).toBe(false);
  expect(host.querySelector<HTMLInputElement>('[aria-label="Solve driver in BEAT for right"]')?.disabled).toBe(false);
  type(host.querySelector<HTMLInputElement>('[aria-label="Sd for drive-hf"]')!, '75');
  expect(useImportedModelStore.getState().fileSimulation?.channelDrivers['drive-hf'].fields.sd_cm2).toBe(75);
  expect(host.textContent).not.toContain('Each selected group is a separate');
  expect(host.textContent).not.toContain('Full domain, free space');
  expect(host.querySelector('[data-section="Crossover"]')).toBeNull();
  expect(host.querySelector('[data-section="Passive cardioid"]')).toBeNull();
});

it('disables the shared Simulation controls while no prepared file is available', () => {
  opened({ prepared: null });
  act(() => root.render(panel()));
  expect(host.querySelector<HTMLFieldSetElement>('[aria-label="Imported mesh simulation"] fieldset')?.disabled).toBe(true);
});

it.each(['share', 'separate'] as const)('shows the %s choice after adding a surface to its role', async (choice) => {
  const msh = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
  const mesh: RegisteredMesh = { ingest_id: 'wgi_roles', model_id: 'mesh-file:sha256:roles', producer: 'mesh-file',
    manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact', msh_text: msh, summary: fileSummary,
    source_file: { filename: 'speaker.msh', transform: { units: 'mm', forward: '+z', up: '+y' },
      source_tags: [1, 2], roles: { 1: 'MF', 2: 'MF', 3: 'RIGID' } },
    source_tags: { group_1: 1, group_2: 2 }, sources: [{ id: 'group_1', role: 'MF' }, { id: 'group_2', role: 'MF' }] };
  const next: RegisteredMesh = { ...mesh, ingest_id: 'wgi_added', source_tags: { ...mesh.source_tags, group_3: 3 },
    sources: [...mesh.sources!, { id: 'group_3', role: 'MF' }],
    source_file: { ...mesh.source_file, source_tags: [1, 2, 3], roles: { 1: 'MF', 2: 'MF', 3: 'MF' } } };
  await act(async () => {
    await openImported({ kind: 'file', prepared: mesh });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(msh), 'file', mesh.ingest_id));
    root.render(panel());
  });
  choose(host.querySelector<HTMLSelectElement>('[aria-label^="Driver sharing for"]')!, choice);
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    model: { model_id: mesh.model_id, producer: 'mesh-file', display_name: 'speaker.msh' },
    prepared: next, latest_prepared: next, original_available: true,
  }), { headers: { 'Content-Type': 'application/json' } }));
  await act(async () => { await editMeshImport({ roles: next.source_file.roles }, fetcher); });
  expect(useMeshImportStore.getState().error).toBeNull();
  expect(host.querySelector<HTMLSelectElement>('[aria-label^="Driver sharing for"]')?.value).toBe(choice);
  expect(host.querySelectorAll('.cad-channel')).toHaveLength(choice === 'share' ? 1 : 3);
  if (choice === 'share') expect(host.textContent).toContain('3 surfaces · identical drivers');
  importedMeshStore.clear();
});
