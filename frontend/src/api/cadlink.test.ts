import { describe, expect, it, vi } from 'vitest';
import { designForFamily } from '../stores/design';
import { CadLinkApiError, getFusionCadStatus, getIngest, ingestReturn, listReturns, requestFusionReturn } from './cadlink';

describe('CAD-link client', () => {
  it('uses the server aliases for listing, ingestion, and record lookup', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return new Response(JSON.stringify({ items: [] }), { status: 200 });
      if (path.endsWith('/ingest')) return new Response(JSON.stringify({ ingest_id: 'wgi_test' }), { status: 200 });
      return new Response(JSON.stringify({ ingest_id: 'wgi_test' }), { status: 200 });
    });
    await listReturns(fetcher as typeof fetch);
    await ingestReturn({
      bundlePath: 'wgreturn/a.wgreturn',
      mesh: { rigidSizeMm: 10, transitionMm: 12, sourceSizeMm: { source: 4 } },
      skippedSourceIds: [],
      areaDriftOverrides: [],
    }, fetcher as typeof fetch);
    await getIngest('wgi_test', fetcher as typeof fetch);
    expect(fetcher.mock.calls.map(([path]) => String(path))).toEqual([
      '/api/cadlink/returns', '/api/cadlink/ingest', '/api/cadlink/ingest/wgi_test',
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toMatchObject({
      bundlePath: 'wgreturn/a.wgreturn',
      mesh: { rigidSizeMm: 10, sourceSizeMm: { source: 4 } },
    });
  });

  it('unwraps string and object error details', async () => {
    const stringError = vi.fn(async () => new Response(JSON.stringify({ detail: 'Choose a workspace.' }), { status: 409, statusText: 'Conflict' })) as typeof fetch;
    await expect(listReturns(stringError)).rejects.toThrow('Choose a workspace.');
    const objectError = vi.fn(async () => new Response(JSON.stringify({ detail: { message: 'Bad return manifest' } }), { status: 422 })) as typeof fetch;
    await expect(getIngest('bad', objectError)).rejects.toThrow('Bad return manifest');
  });

  it('retains structured area-drift source ids on a refusal', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      detail: { message: 'Area drift requires an override.', area_drift_sources: ['source-hf'] },
    }), { status: 422 })) as typeof fetch;
    const error = await ingestReturn({
      bundlePath: 'wgreturn/a.wgreturn',
      mesh: { rigidSizeMm: 10, transitionMm: 12, sourceSizeMm: { 'source-hf': 4 } },
      skippedSourceIds: [], areaDriftOverrides: [],
    }, fetcher).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(CadLinkApiError);
    expect((error as CadLinkApiError).areaDriftSources).toEqual(['source-hf']);
  });

  it('sends the current config and CAD identity when checking Fusion freshness', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      cadApplication: 'fusion360', state: 'closed', processRunning: false, running: false, updatedAt: null,
      documentName: null, currentFormula: 'R-OSSE', fusionFormula: null, link: null,
    }), { status: 200 }));
    const identity = { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 4 };

    await getFusionCadStatus(designForFamily('R-OSSE'), identity, 'wgreturn/a.wgreturn', fetcher as typeof fetch);

    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toBe('/api/cadlink/fusion-status');
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({
      design: { formula: 'R-OSSE' },
      identity,
      returnBundlePath: 'wgreturn/a.wgreturn',
    });
  });

  it('targets an exact Fusion document and linked instance for a return', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      status: 'requested', requestId: 'request-a', documentName: 'Tritonia V',
    }), { status: 200 }));
    await requestFusionReturn({
      designId: 'wgd_a', documentId: 'fusion:doc-a', instanceId: 'instance-a',
      expectedReturnStateHash: 'sha256:return-state',
    }, fetcher as typeof fetch);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      designId: 'wgd_a', documentId: 'fusion:doc-a', instanceId: 'instance-a',
      expectedReturnStateHash: 'sha256:return-state',
    });
  });
});
