import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { migratedCadRequestFixture } from '../jobs/cadSolve.fixtures';
import { useCadReturnStore } from '../stores/cadReturn';
import { RunModelDetails } from './RunModelDetails';

let host: HTMLDivElement, root: Root;
beforeEach(() => {
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
