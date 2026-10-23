import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { migratedCadRequestFixture } from '../jobs/cadSolve.fixtures';
import { useCadReturnStore } from '../stores/cadReturn';
import { useDocumentStore, resetDocumentStore } from '../stores/document';
import { RunModelDetails } from './RunModelDetails';

let host: HTMLDivElement, root: Root;
beforeEach(() => {
  resetDocumentStore();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [{
    returnStateHash: 'old-state', documentName: 'Archived document', filename: 'old.f3d', capturedAt: '2026-10-01',
  }] }))));
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); useCadReturnStore.setState({ selectedBundle: null }); });
const run = (): JobItem => ({ ...migratedCadRequestFixture(), config_summary: { geometry_type: 'imported' },
  cad_source: { ...migratedCadRequestFixture().cad_source!, document_name: 'Run’s original document', lineage_id: 'lineage',
    return_state_hash: 'old-state', solve_model_sha256: 'sha256:immutable-model', model_id: 'cad:old-model' } });

it('shows the run’s immutable document and full model identity independently of Fusion', async () => {
  const job = run();
  await act(async () => root.render(<RunModelDetails job={job}/>));
  expect(host.textContent).toContain('Run’s original document');
  expect(host.textContent).toContain('CAD Link');
  expect(host.textContent).toContain('cad:old-model');
  expect(host.textContent).toContain('sha256:immutable-model');
  act(() => useCadReturnStore.setState({ selectedBundle: { documentName: 'Current unrelated Fusion document' } as never }));
  expect(host.textContent).not.toContain('Current unrelated');
  expect(host.textContent).not.toContain('Archived document');
  expect(host.querySelector('a')?.getAttribute('href')).toContain('old-state');
});

it('falls back to the run snapshot, then its archived attachment for old records', async () => {
  const job = run(); job.cad_source!.document_name = '';
  await act(async () => root.render(<RunModelDetails job={job}/>));
  expect(host.textContent).toContain(job.cad_state!.snapshot!.document_name);
  await act(async () => root.render(<RunModelDetails job={{ ...job, cad_state: null }}/>));
  expect(host.textContent).toContain('Archived document');
});

it('keeps identity visible when the attachment cannot be read', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  await act(async () => root.render(<RunModelDetails job={run()}/>));
  expect(host.textContent).toContain('Run’s original document');
  expect(host.textContent).toContain('sha256:immutable-model');
  expect(host.querySelector('[role="status"]')?.textContent).toContain('offline');
});

it('shows retained file provenance without claiming a Fusion attachment', async () => {
  const job = run(); job.config_summary = { geometry_type: 'imported', import_origin: 'mesh-file' };
  await act(async () => root.render(<RunModelDetails job={job}/>));
  expect(host.textContent).toContain('Mesh file');
  expect(host.textContent).not.toContain('Fusion file');
});

it('opens the archived model by lineage and hash and explains the new document import', async () => {
  const listing = await fetch('/unused').then(response => response.json());
  const fetcher = vi.fn(async (_url: RequestInfo | URL, options?: RequestInit) => new Response(JSON.stringify(
    options?.method === 'POST' ? { status: 'opened', message: 'Sent to Fusion.' } : listing,
  )));
  vi.stubGlobal('fetch', fetcher);
  await act(async () => root.render(<RunModelDetails job={run()}/>));
  expect(host.textContent).toContain('imports this file as a new document');
  expect(host.textContent).toContain('does not reopen the cloud document');
  const button = host.querySelector('button')!;
  expect(button.textContent).toBe('Open in Fusion');
  await act(async () => button.click());
  const [url, options] = fetcher.mock.calls[1];
  expect(url).toBe('/api/cadlink/projects/lineage/documents/old-state/open');
  expect(options?.method).toBe('POST');
  expect(options?.body).toBeUndefined();
  expect(host.querySelector('[role="status"]')?.textContent).toBe('Sent to Fusion.');
});

it.each(['Finder', 'Explorer'])('offers Show in %s when CAD launching is disabled', async (fileManager) => {
  const listing = await fetch('/unused').then(response => response.json());
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...listing, launchDisabled: true, fileManager }))));
  await act(async () => root.render(<RunModelDetails job={run()}/>));
  expect(host.querySelector('button')?.textContent).toBe(`Show in ${fileManager}`);
  expect(host.textContent).toContain('Opening Fusion is disabled on this computer.');
});

it('shows a missing Fusion error and keeps the download available', async () => {
  const listing = await fetch('/unused').then(response => response.json());
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => options?.method === 'POST'
    ? new Response(JSON.stringify({ detail: 'Install Fusion 360 to open this file.' }), { status: 409 })
    : new Response(JSON.stringify(listing))));
  await act(async () => root.render(<RunModelDetails job={run()}/>));
  await act(async () => host.querySelector('button')!.click());
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('Install Fusion 360');
  expect(host.querySelector('a')?.textContent).toBe('Fusion file');
  expect(host.querySelector('button')?.disabled).toBe(false);
});

it('does not claim capture was off when the attachment is missing', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [] }))));
  await act(async () => root.render(<RunModelDetails job={run()}/>));
  const missing = host.querySelector('.cad-model-missing')!;
  expect(missing.textContent).toBe('no Fusion file');
  expect(missing.getAttribute('title')).not.toContain('Capture was off');
  expect(missing.getAttribute('title')).toContain('may have been removed');
  expect([...host.querySelectorAll('button')].some(button => button.textContent === 'Open in Fusion')).toBe(false);
  expect(host.textContent).toContain('Move into');
});


it('shows the immutable folder and a current rename only for the same document', async () => {
  useDocumentStore.setState({ designUid: 'same-design', designName: 'Big Horn' });
  const job = { ...run(), design_uid: 'same-design', design_name: 'Big Horn', archive_folder: 'Big_Horn', archive_run: '1_Big_Horn' };
  await act(async () => root.render(<RunModelDetails job={job}/>));
  expect(host.textContent).toContain('Folder:Big_Horn/');
  expect(host.textContent).not.toContain('design now');
  act(() => useDocumentStore.getState().setDesignName('Renamed'));
  expect(host.textContent).toContain('design now Renamed');
  act(() => useDocumentStore.setState({ designUid: 'unrelated', designName: 'Other' }));
  expect(host.textContent).not.toContain('design now');
});

it.each([
  ['model:mesh-file:horn', undefined, undefined],
  ['lineage:run-lineage', 'unrelated-lineage', 'run-design'],
  ['design:run-design', undefined, 'run-design'],
  ['name:Mesh horn', undefined, undefined],
])('uses the recorded %s archive owner while another design is open', async (archiveKey, lineage, designUid) => {
  useDocumentStore.setState({ designUid: 'open-other', designName: 'Unrelated design' });
  const job: JobItem = { ...run(), label: 'Mesh horn', design_name: 'Mesh horn', design_uid: designUid,
    archive_key: archiveKey, cad_state: null, cad_source: { ...run().cad_source!, model_id: 'mesh-file:horn', lineage_id: lineage ?? null },
    config_summary: { geometry_type: 'imported', import_origin: 'mesh-file' } };
  const fetcher = vi.fn(async (url: RequestInfo | URL, _options?: RequestInit) => new Response(JSON.stringify(
    String(url).endsWith('/preview') ? { selected: [], candidates: [], operations: [], old_folders: [],
      summary: { runs: 0, files: 0, bytes: 0, fusion_files: 0, duplicates: 0, saved_bytes: 0 } } : { reports: [] },
  )));
  vi.stubGlobal('fetch', fetcher);
  await act(async () => root.render(<RunModelDetails job={job}/>));
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === 'Move earlier runs…')!.click());
  const preview = fetcher.mock.calls.find(([url]) => String(url).endsWith('/preview'));
  expect(preview).toBeDefined();
  const body = JSON.parse((preview as unknown as [string, RequestInit])[1].body as string);
  expect(body.key).toBe(archiveKey);
  expect(body.name).toBe('Mesh horn');
  expect(document.body.textContent).not.toContain('Bring earlier runs into Unrelated design');
});

it('uses a legacy mesh run’s own model when no archive key was recorded', async () => {
  useDocumentStore.setState({ designUid: 'open-other', designName: 'Unrelated design' });
  const job: JobItem = { ...run(), archive_key: undefined, design_uid: undefined, design_name: 'Mesh horn',
    cad_state: null, cad_source: { ...run().cad_source!, model_id: 'mesh-file:horn', lineage_id: null },
    config_summary: { geometry_type: 'imported', import_origin: 'mesh-file' } };
  const fetcher = vi.fn(async (url: RequestInfo | URL, _options?: RequestInit) => new Response(JSON.stringify(
    String(url).endsWith('/preview') ? { selected: [], candidates: [], operations: [], old_folders: [],
      summary: { runs: 0, files: 0, bytes: 0, fusion_files: 0, duplicates: 0, saved_bytes: 0 } } : { reports: [] },
  )));
  vi.stubGlobal('fetch', fetcher);
  await act(async () => root.render(<RunModelDetails job={job}/>));
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent?.startsWith('From an earlier version'))!.click());
  const preview = fetcher.mock.calls.find(([url]) => String(url).endsWith('/preview'));
  const body = JSON.parse((preview as unknown as [string, RequestInit])[1].body as string);
  expect(body.key).toBe('model:mesh-file:horn');
  expect(body.name).toBe('Mesh horn');
});
