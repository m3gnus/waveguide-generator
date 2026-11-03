import type { SourceAssemblyDocument } from './sourceEditor';
import { activityStore } from '../shell/activity';
import { afterEach, expect, it, vi } from 'vitest';
import { sourceEditorApi } from './sourceEditor';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';
import { seed } from '../sketch/adapters/sourceContour';
import { serializeSourceAuthoring } from '../sketch/adapters/sourceAuthoring';

afterEach(() => vi.unstubAllGlobals());
it('sends authoring beside the unchanged preset document and omits it from validation and export', async () => {
  const calls: { url: string; body: string }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url, init) => {
    calls.push({ url, body: init.body });
    return { ok: true, json: async () => ({}), blob: async () => new Blob() };
  }));
  const envelope = serializeSourceAuthoring(seed(INITIAL_SOURCE));
  const preset = { id: 'saved', revision: 'r1', name: 'Saved', document: INITIAL_SOURCE, authoring: envelope };
  await sourceEditorApi.save('Saved', INITIAL_SOURCE);
  const legacyBody = calls.at(-1)!.body;
  expect(legacyBody).toBe(JSON.stringify({ name: 'Saved', document: INITIAL_SOURCE, expected_revision: null }));
  await sourceEditorApi.save('Saved', INITIAL_SOURCE, undefined, envelope);
  await sourceEditorApi.save('Saved', INITIAL_SOURCE, preset, envelope);
  for (const call of calls.slice(1)) {
    const payload = JSON.parse(call.body);
    expect(JSON.stringify(payload.document)).toBe(JSON.stringify(INITIAL_SOURCE));
    expect(payload.authoring).toEqual(envelope);
    expect(payload.document).not.toHaveProperty('authoring');
  }
  expect(calls.at(-1)!.url).toBe('/api/source-editor/presets/saved');
  expect(JSON.parse(calls.at(-1)!.body).expected_revision).toBe('r1');
  await sourceEditorApi.validate(preset.document);
  expect(calls.at(-1)!.body).toBe(JSON.stringify(INITIAL_SOURCE));
  const attachment = { kind: 'horn' as const, dimensions: { length_mm: 24, mouth_radius_mm: 10, housing_radius_mm: 12, backing_depth_mm: 4 } };
  await sourceEditorApi.export(preset.document, attachment, 2);
  expect(calls.at(-1)!.body).toBe(JSON.stringify({ document: INITIAL_SOURCE, attachment, mesh_size_mm: 2 }));
});

it.each(['source', 'assembly'] as const)('tracks %s STEP export through success and failure', async kind => {
  for (const fail of [false, true]) {
    activityStore.reset();
    let resolve!: (value: Response) => void;
    vi.stubGlobal('fetch', () => new Promise<Response>(done => { resolve = done; }));
    const pending = kind === 'source'
      ? sourceEditorApi.export(INITIAL_SOURCE, { kind: 'baffle', dimensions: {} }, 2)
      : sourceEditorApi.exportAssembly({ horn: INITIAL_SOURCE } as SourceAssemblyDocument);
    expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'export' }]);
    const settled = pending.catch(error => error);
    resolve(new Response(fail ? JSON.stringify({ detail: 'export failed' }) : 'STEP', { status: fail ? 422 : 200 }));
    await settled;
    expect(activityStore.getSnapshot()).toHaveLength(0);
    expect(activityStore.typicalMs('export') === null).toBe(fail);
  }
});
