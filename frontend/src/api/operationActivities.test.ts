import { afterEach, expect, it, vi } from 'vitest';
import { activityStore, type ActivityKind } from '../shell/activity';
import { buildGeometryExport, convertDesignToFreeform, exportGeometryToOutputFolder, exportProfileCsv, sendDesignToCad, serializeDesignDocument } from './designIo';
import { ingestReturn } from './cadlink';
import { readImportedModel, readRunWorkspace } from './importedModels';
import { sendDesignToOnshape, returnOnshapeToWg } from './onshape';
import { fetchMeshReplay, openMeshReplay } from './meshReplay';
import { postSolverMesh } from './solverMesh';
import { loadCadFrameModel } from '../shell/CadSolverFrameConfirm';
import { designForFamily } from '../stores/design';
import { deferred, json, mesh } from '../shell/cadlink/selection.fixtures';

const design = designForFamily('OSSE');
interface Operation { name: string; kind: ActivityKind; run: (fetcher: typeof fetch) => Promise<unknown> }
const operations: Operation[] = [
  { name: 'design file', kind: 'export', run: f => serializeDesignDocument(design, 'horn', f) },
  { name: 'STL build', kind: 'export', run: f => buildGeometryExport('stl', design, 0, 'horn', undefined, undefined, f) },
  { name: 'STEP output', kind: 'export', run: f => exportGeometryToOutputFolder('step', design, 0, 'horn', undefined, undefined, f) },
  { name: 'profile CSV', kind: 'export', run: f => exportProfileCsv(design, 0, undefined, undefined, f) },
  { name: 'Freeform conversion', kind: 'export', run: f => convertDesignToFreeform(design, f) },
  { name: 'Fusion send', kind: 'send-to-cad', run: f => sendDesignToCad(design, 0, 'horn', null, f) },
  { name: 'Onshape send', kind: 'send-to-cad', run: f => sendDesignToOnshape(design, 0, 'horn', null, {}, f) },
  { name: 'Onshape return', kind: 'cad-import', run: f => returnOnshapeToWg('design', f) },
  { name: 'CAD ingest', kind: 'cad-import', run: f => ingestReturn({ bundlePath: 'speaker.wgreturn', mesh: { rigidSizeMm: 4, transitionMm: 4, sourceSizeMm: {} }, skippedSourceIds: [], areaDriftOverrides: [], expectedDesignId: null, symmetryMode: 'full' }, f) },
  { name: 'saved run', kind: 'cad-import', run: f => readRunWorkspace('job', f) },
  { name: 'saved CAD', kind: 'cad-import', run: f => readImportedModel('cad:model', 'prepared', f) },
  { name: 'saved file mesh', kind: 'mesh-import', run: f => readImportedModel('mesh-file:model', 'prepared', f) },
  { name: 'mesh replay import', kind: 'mesh-import', run: f => openMeshReplay('{}', f) },
  { name: 'mesh replay export', kind: 'export', run: f => fetchMeshReplay('job', f) },
  { name: 'solver mesh', kind: 'solver-mesh', run: f => postSolverMesh('{}', f) },
  { name: 'frame preview mesh', kind: 'cad-import', run: f => loadCadFrameModel('prepared', f) },
];
function answer(path: string): Response {
  if (path === '/api/cad-workspace/path') return json({ selected: true });
  if (path === '/api/workspace/write-export') return json({ directory: 'out', files: ['out/horn.step'] });
  if (path === '/api/design/serialize') return json({ text: 'design', suggestedFilename: 'horn.cfg' });
  if (path === '/api/solver-mesh') return json({ msh_text: mesh, stats: { mesh_cache_key: 'mesh', triangle_count: 1 }, cut_planes: [] });
  if (path.endsWith('/mesh')) return new Response(mesh);
  if (path === '/api/export/step?body=solid&delivery=staged') {
    return json({ artifactId: 'retained-step', filename: 'horn.step', sizeBytes: 13 });
  }
  if (path.startsWith('/api/export/') && !path.endsWith('/wglink')) {
    const response = new Response('profile or geometry');
    response.blob = async () => new Blob(['geometry']);
    return response;
  }
  return json({});
}
afterEach(() => { activityStore.reset(); vi.restoreAllMocks(); });

it.each(operations)('$name starts once and ends after the complete response', async ({ kind, run }) => {
  const first = deferred<Response>();
  let firstPath = ''; let calls = 0;
  const started = vi.spyOn(activityStore, 'start');
  const fetcher: typeof fetch = async input => {
    expect(activityStore.getSnapshot()).toHaveLength(1);
    if (++calls === 1) { firstPath = String(input); return first.promise; }
    return answer(String(input));
  };
  const pending = run(fetcher);
  expect(activityStore.getSnapshot()).toMatchObject([{ kind }]);
  first.resolve(answer(firstPath));
  await pending;
  expect(started).toHaveBeenCalledTimes(1);
  expect(activityStore.getSnapshot()).toHaveLength(0);
  expect(activityStore.typicalMs(kind)).not.toBeNull();
});

it.each(operations)('$name ends without recording a successful duration on failure', async ({ kind, run }) => {
  const first = deferred<Response>();
  const pending = run(() => first.promise);
  expect(activityStore.getSnapshot()).toMatchObject([{ kind }]);
  const rejected = expect(pending).rejects.toThrow('request failed');
  first.reject(new Error('request failed'));
  await rejected;
  expect(activityStore.getSnapshot()).toHaveLength(0);
  expect(activityStore.typicalMs(kind)).toBeNull();
});

it.each(['caller', 'Stop'] as const)('solver mesh cancellation via %s aborts the actual fetch', async via => {
  const caller = new AbortController();
  let signal!: AbortSignal;
  const pending = postSolverMesh('{}', (_input, init) => {
    signal = init!.signal!;
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  }, caller.signal);
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  if (via === 'caller') caller.abort(); else activityStore.getSnapshot()[0].cancel!();
  await rejected;
  expect(signal.aborted).toBe(true);
  expect(activityStore.getSnapshot()).toHaveLength(0);
});
