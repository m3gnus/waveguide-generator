import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { startCadSetupPublisher } from './cadSetupPublisher';
import { openImported, resetImportedModelStore } from '../stores/importedModel';
import { editMeshImport, fileMeshSetup, fileSetupForSave, loadMeshFile, useMeshImportStore } from '../stores/meshImport';
import { fileSimulationSetup } from '../stores/importedSimulationSetup';
import { resetSolveOptionsStore, useImportedSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { parseMSH } from '../viewport/mshParser';
import type { RegisteredMesh } from '../api/meshImport';

const text = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
const record: RegisteredMesh = { ingest_id: 'wgi_file', model_id: 'mesh-file:sha256:file', producer: 'mesh-file',
  artifact_sha256: 'sha256:file', manifest_sha256: 'sha256:prepared', msh_text: text,
  source_file: { filename: 'speaker.msh', transform: { units: 'mm', forward: '+z', up: '+y' }, source_tags: [2] },
  source_tags: { group_2: 2 }, summary: { node_count: 4, triangle_count: 4, dimensions: [100, 100, 100], groups: [] } };
const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
const initial = useMeshImportStore.getState();
let stop: (() => void) | undefined;
beforeEach(async () => {
  vi.useFakeTimers();
  workspaceModeStore.setImportedInput('file');
  workspaceModeStore.setMode('cad');
  resetImportedModelStore();
  resetSolveOptionsStore();
  useMeshImportStore.setState({ ...initial, filename: 'speaker.msh', original: text });
  await openImported({ kind: 'file', prepared: record });
  importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(text), 'file', record.ingest_id));
});
afterEach(() => {
  stop?.(); stop = undefined;
  vi.useRealTimers();
  useMeshImportStore.setState(initial, true);
  resetImportedModelStore();
  importedMeshStore.clear();
});

it('automatically saves motion and solver edits through the shared publisher', async () => {
  const fetcher = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_saved' }));
  stop = startCadSetupPublisher({ fetcher, debounceMs: 100 });
  fileSimulationSetup.get().setChannelMotion('group_2', 'axial');
  useImportedSolveOptionsStore.getState().setSweep({ count: 17 });
  await vi.advanceTimersByTimeAsync(101);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe(`/api/models/${encodeURIComponent(record.model_id)}/setup`);
  expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ prepared_id: record.ingest_id, setup: { schema_version: 1, ...fileSetupForSave(fileMeshSetup()) } });
  expect(fileMeshSetup().geometry.drive_channels[0].motion).toBe('axial');
  expect(useMeshImportStore.getState().notice).toBeNull();
});

it('saves geometry edits with the new preparation and retains the latest edit on page close', async () => {
  const fetcher = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_saved' }));
  stop = startCadSetupPublisher({ fetcher, debounceMs: 100 });
  const next = { ...record, ingest_id: 'wgi_resized', source_file: { ...record.source_file,
    transform: { ...record.source_file.transform, units: 'cm' } } };
  await editMeshImport({ units: 'cm' }, vi.fn(async () => response({ prepared: next })));
  await vi.advanceTimersByTimeAsync(101);
  expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string).prepared_id).toBe('wgi_resized');
  fileSimulationSetup.get().setChannelMotion('group_2', 'axial');
  window.dispatchEvent(new Event('pagehide'));
  await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][1]?.keepalive).toBe(true);
  expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string).setup.geometry.drive_channels[0].motion).toBe('axial');
  const sequence = (index: number) => Number((fetcher.mock.calls[index][1]?.headers as Record<string, string>)['X-WG-Setup-Seq']);
  expect(sequence(1)).toBeGreaterThan(sequence(0));
});

it('orders in-flight writes so a later edit is the final saved setup', async () => {
  let resolve!: (response: Response) => void;
  const pending = new Promise<Response>(done => { resolve = done; });
  const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(() => pending)
    .mockImplementation(async () => response({ revision_id: 'wgs_saved' }));
  stop = startCadSetupPublisher({ fetcher, debounceMs: 100 });
  fileSimulationSetup.get().setChannelMotion('group_2', 'axial');
  await vi.advanceTimersByTimeAsync(101);
  useImportedSolveOptionsStore.getState().setSweep({ count: 19 });
  await vi.advanceTimersByTimeAsync(101);
  expect(fetcher).toHaveBeenCalledOnce();
  resolve(response({ revision_id: 'wgs_earlier' }));
  await vi.advanceTimersByTimeAsync(1);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string).setup.options.num_frequencies).toBe(19);
});

it('flushes the last edit and waits for it before inspecting the same file again', async () => {
  let finishSave!: (response: Response) => void;
  const saving = new Promise<Response>(resolve => { finishSave = resolve; });
  const writer = vi.fn<typeof fetch>(() => saving);
  stop = startCadSetupPublisher({ fetcher: writer, debounceMs: 750 });
  fileSimulationSetup.get().setChannelMotion('group_2', 'axial');
  const setup = fileMeshSetup();
  const reader = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...record.summary, known_model: { model_id: record.model_id, latest_prepared_id: record.ingest_id } })
    : String(url).includes('/setup?') ? response({ setup, revision_id: 'wgs_last' })
    : response({ model: { model_id: record.model_id, producer: 'mesh-file', display_name: 'speaker.msh' },
      prepared: record, latest_prepared: record, original_available: true }));
  const loading = loadMeshFile('speaker.msh', text, reader);
  expect(writer).toHaveBeenCalledOnce();
  expect(reader).not.toHaveBeenCalled();
  finishSave(response({ revision_id: 'wgs_last' }));
  await loading;
  expect(reader).toHaveBeenCalledTimes(3);
  expect(fileMeshSetup().geometry.drive_channels[0].motion).toBe('axial');
});

it('does not replace the model setup merely by reopening a saved model', async () => {
  useMeshImportStore.setState({ original: null });
  const fetcher = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_saved' }));
  stop = startCadSetupPublisher({ fetcher, debounceMs: 100 });
  const setup = fileMeshSetup();
  const reader = vi.fn<typeof fetch>(async url => String(url).includes('/setup?')
    ? response({ setup, revision_id: 'wgs_last' })
    : response({ model: { model_id: record.model_id, producer: 'mesh-file', display_name: 'speaker.msh' },
      prepared: record, latest_prepared: record, original_available: true }));
  await openImported({ kind: 'model', modelId: record.model_id }, reader);
  await vi.advanceTimersByTimeAsync(101);
  expect(fetcher).not.toHaveBeenCalled();
});

it('attributes a late file save failure to the model that issued it', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>(resolve => { finish = resolve; });
  const onSaveState = vi.fn();
  const fetcher = vi.fn<typeof fetch>(async url => String(url).startsWith('/api/models/') ? pending : response({}));
  stop = startCadSetupPublisher({ fetcher, debounceMs: 100, retryDelaysMs: [], onSaveState });
  fileSimulationSetup.get().setChannelMotion('group_2', 'axial');
  await vi.advanceTimersByTimeAsync(101);
  workspaceModeStore.setImportedInput('cad');
  finish(new Response(JSON.stringify({ detail: 'Could not save settings.' }), { status: 500 }));
  await vi.advanceTimersByTimeAsync(1);
  expect(onSaveState).toHaveBeenCalledExactlyOnceWith('failed', { input: 'file', modelId: record.model_id });
});

it('records the latest options for a departed file when its preparation finishes', async () => {
  let finishPreparation!: (value: Response) => void;
  const preparing = new Promise<Response>(resolve => { finishPreparation = resolve; });
  const writer = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_saved' }));
  stop = startCadSetupPublisher({ fetcher: writer, debounceMs: 100 });
  const next = { ...record, ingest_id: 'wgi_resized', source_file: { ...record.source_file,
    transform: { ...record.source_file.transform, units: 'cm' } } };
  // A role edit avoids a frame inspection; the request below is preparation itself.
  const editing = editMeshImport({ roles: { 2: 'HF' } }, vi.fn(() => preparing));
  useImportedSolveOptionsStore.getState().setSweep({ count: 19 });
  const reader = vi.fn<typeof fetch>(async () => response({ ...record.summary, suggested_units: null }));
  const loading = loadMeshFile('another.msh', text + '\n', reader);
  expect(reader).not.toHaveBeenCalled();
  finishPreparation(response({ prepared: next }));
  await editing;
  await loading;
  await vi.advanceTimersByTimeAsync(101);
  const writes = writer.mock.calls.filter(([url]) => String(url).startsWith('/api/models/'));
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe(`/api/models/${encodeURIComponent(record.model_id)}/setup`);
  expect(JSON.parse(writes[0][1]?.body as string)).toMatchObject({ prepared_id: next.ingest_id,
    setup: { options: { num_frequencies: 19 } } });
  expect(useMeshImportStore.getState().filename).toBe('another.msh');
});
