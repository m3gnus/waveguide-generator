import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { afterEach, expect, it, vi } from 'vitest';
import { hydrateDesignDocument, importNotice, openDesignText, serializeDesignDocument } from './designIo';
import { restoreAutosave, writeAutosave } from '../stores/autosave';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { buildParameterCatalog } from '../design/parameterCatalogWire';
import { applyOpenedDesign } from '../design/openCadProject';
import { canLoadJobDesign, jobRerunState, openJobDesign } from '../jobs/jobDesign';
import { designFingerprint, jobDesignFingerprint } from '../results/runCoherence';
import { currentModel, runVerdict } from '../model/currentModel';

const python = process.env.WG_TEST_PYTHON;
const legacy = JSON.parse(readFileSync('../server/tests/data/lookup_oldconfig_corpus/morph-snapping.json', 'utf8')).design;
const bridge = `import asyncio,json,sys
sys.path.insert(0,'..')
from server.design_io.api import open_design,serialize_design
r=json.load(sys.stdin)
print(json.dumps(asyncio.run(open_design(r['body']) if r['path'].endswith('/open') else serialize_design(json.loads(r['body'])))))`;
const serverFetch = (async (url, init) => new Response(execFileSync(python, ['-c', bridge], {
  input: JSON.stringify({ path: String(url), body: String(init?.body) }), encoding: 'utf8',
}), { status: 200 }));

afterEach(() => { vi.unstubAllGlobals(); resetDesignStore(); });

it('publishes one Custom profile family and no retired controls', () => {
  const catalog = buildParameterCatalog();
  expect(catalog.design_families).not.toContain('LOOKUP');
  expect(catalog.parameters.some((field) => field.id.startsWith('lookup.'))).toBe(false);
});

it.skipIf(!python)('loads a table file, saves untouched as cfg, and reopens through the real server', async () => {
  const opened = await openDesignText(JSON.stringify({ ...legacy, source: { velocity: { value: 1, raw: '2/2' } }, simulation: { f1: { value: 250, raw: '500/2' } } }), serverFetch);
  const document = hydrateDesignDocument(opened.design);
  expect(document.formula).toBe('FREEFORM');
  expect(document.shared_profile).toBe(true);
  expect(document.profile_h.points.every((point) => point.join === 'corner')).toBe(true);
  expect(importNotice(opened)).toContain('Morph-start snapping');
  const saved = await serializeDesignDocument(document, 'converted', serverFetch, undefined, null);
  expect(saved.suggestedFilename).toBe('converted.cfg');
  expect(saved.text).toContain('Freeform.Shared = 1');
  const reopened = await openDesignText(saved.text, serverFetch);
  const again = hydrateDesignDocument(reopened.design);
  expect(again.formula).toBe('FREEFORM');
  expect(again.shared_profile).toBe(true);
  expect(again.profile_h.points).toEqual(document.profile_h.points);
  expect(again.morph.fixed_part).toBe(document.morph.fixed_part);
  expect(again.source.shape).toBe(document.source.shape);
  expect(again._expressions['source.velocity'].raw).toBe('2/2');
  expect(again._expressions['simulation.f1'].raw).toBe('500/2');
  expect(again.simulation.f1).toBe(250);
  expect(importNotice(reopened)).toBeNull();
  expect(serializeDesign(again)).not.toHaveProperty('lookup_profile');
  applyOpenedDesign(opened, 'legacy.mwg');
  expect(useDesignStore.getState().loadNotice).toBe(importNotice(opened));
}, 20000);

it.skipIf(!python)('restores an old autosave before installing editor state', async () => {
  const { mesh, simulation, source, enclosure, morph, quadrants } = designForFamily('FREEFORM');
  const design = { mesh, simulation, source, enclosure, morph, quadrants, ...legacy };
  const raw = JSON.stringify({ version: 1, filename: 'legacy.mwg', designRevision: 7, savedRevision: null, design });
  const storage = { getItem: () => raw, setItem: vi.fn(), removeItem: vi.fn() };
  const calls = [];
  vi.stubGlobal('fetch', vi.fn(async (url, init) => {
    if (!String(url).startsWith('/api/design/')) return new Response('{}', { status: 200 });
    calls.push(String(url)); return serverFetch(url, init);
  }));
  expect(restoreAutosave(storage)).toBe(true);
  expect(useDesignStore.getState().design.formula).not.toBe('LOOKUP');
  await vi.waitFor(() => expect(useDesignStore.getState().designRevision).toBe(7));
  expect(calls).toEqual(['/api/design/open']);
  expect(useDesignStore.getState().design.formula).toBe('FREEFORM');
  expect(useDesignStore.getState().loadNotice).toContain('Morph-start snapping');
  expect(storage.setItem).not.toHaveBeenCalled();
  expect(storage.removeItem).not.toHaveBeenCalled();
}, 20000);

it.skipIf(!python)('opens and roundtrips a non-circular table as separate constructed H/V profiles', async () => {
  const payload = JSON.parse(readFileSync('../server/tests/data/lookup_oldconfig_corpus/aspect-section.json', 'utf8')).design;
  const opened = await openDesignText(JSON.stringify(payload), serverFetch);
  const document = hydrateDesignDocument(opened.design);
  expect(document.shared_profile).toBe(false);
  const h = document.profile_h.points.at(-1).r;
  const v = document.profile_v.points.at(-1).r;
  expect(h).toBeCloseTo(12.7 + (110 - 12.7) * 1.6, 9);
  expect(v).toBeCloseTo(110, 9);
  expect(importNotice(opened).toLowerCase()).not.toContain('aspect');
  const saved = await serializeDesignDocument(document, 'aspect', serverFetch, undefined, null);
  expect(saved.text).toContain('Freeform.Shared = 0');
  const reopened = hydrateDesignDocument((await openDesignText(saved.text, serverFetch)).design);
  expect(reopened.shared_profile).toBe(false);
  for (const field of ['profile_h', 'profile_v']) {
    expect(reopened[field].points).toEqual(document[field].points.map((point) => ({
      ...point, r: expect.closeTo(point.r, 10),
    })));
  }
}, 60000);

it.skipIf(!python)('lazily opens a historical run for editing without changing its design or result identity', async () => {
  const job = { id: 'historical-lookup', status: 'complete', has_results: true, config_summary: {},
    script_snapshot: { version: 1, design: legacy }, design_availability: {
    reopenable: true, source: 'v2-snapshot', reason_code: 'ok', reason: null, note: null,
  } };
  const original = structuredClone(job);
  const fingerprint = jobDesignFingerprint(job);
  expect(fingerprint).not.toBeNull();
  const fetcher = vi.fn(serverFetch);
  expect(canLoadJobDesign(job)).toBe(true);
  expect(jobRerunState(job)).toEqual({ enabled: true, reason: null });
  expect(fetcher).not.toHaveBeenCalled();
  expect(await openJobDesign(job, undefined, fetcher)).toBe(true);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(useDesignStore.getState().design.formula).toBe('FREEFORM');
  expect(useDesignStore.getState().loadNotice).toContain('Source shape preserved');
  expect(job).toEqual(original);
  expect(jobDesignFingerprint(job)).toBe(fingerprint);
  expect(designFingerprint(useDesignStore.getState().design)).not.toBe(fingerprint);
  expect(runVerdict(job, currentModel())).not.toBe('current');
}, 20000);


it.each(['network failure', 'invalid response'])('retains a pending legacy draft after %s until an edit supersedes it', async (failure) => {
  const { mesh, simulation, source, enclosure, morph, quadrants } = designForFamily('FREEFORM');
  const raw = JSON.stringify({ version: 1, filename: 'legacy.mwg', designRevision: 7, savedRevision: null,
    design: { mesh, simulation, source, enclosure, morph, quadrants, ...legacy } });
  const storage = { getItem: () => raw, setItem: vi.fn(), removeItem: vi.fn() };
  let reject, resolve;
  vi.stubGlobal('fetch', vi.fn((url) => String(url) === '/api/design/open'
    ? new Promise((ready, fail) => { resolve = ready; reject = fail; })
    : Promise.resolve(new Response('{}', { status: 200 }))));
  expect(restoreAutosave(storage)).toBe(true);
  expect(writeAutosave(storage)).toBe(false);
  expect(storage.setItem).not.toHaveBeenCalled();
  if (failure === 'network failure') reject(new Error('offline'));
  else resolve(new Response(JSON.stringify({ design: { formula: 'UNKNOWN' }, migrationsApplied: [] }), { status: 200 }));
  await vi.waitFor(() => expect(useDesignStore.getState().loadNotice).toContain('retained'));
  expect(writeAutosave(storage)).toBe(false);
  expect(storage.setItem).not.toHaveBeenCalled();
  expect(storage.removeItem).not.toHaveBeenCalled();
  useDesignStore.getState().updateField('scale', 2);
  expect(writeAutosave(storage)).toBe(true);
  expect(storage.setItem).toHaveBeenCalledOnce();
});
