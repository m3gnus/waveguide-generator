import { describe, expect, it, vi } from 'vitest';
import { designForFamily } from '../stores/design';
import {
  getOnshapeConnection,
  getOnshapeStatus,
  OnshapePublicConsentRequired,
  returnOnshapeToWg,
  sendDesignToOnshape,
  unlinkOnshape,
} from './onshape';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function recorder(body: unknown, status = 200) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return json(body, status);
  }) as unknown as typeof fetch;
  return { calls, fetcher };
}

const identity = { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 1 };

// The five Onshape routes the CAD Link panel depends on: connection, status,
// send, unlink and return.
describe('Onshape client', () => {
  it('reads the connection, refreshing only when asked', async () => {
    const { calls, fetcher } = recorder({ configured: true, reachable: true, credentialsPath: 'keys.json', detail: null, insecureKeyFile: false, account: null, plan: null });
    expect((await getOnshapeConnection(false, fetcher)).reachable).toBe(true);
    await getOnshapeConnection(true, fetcher);
    expect(calls.map(({ url }) => url)).toEqual([
      '/api/cadlink/onshape/connection',
      '/api/cadlink/onshape/connection?refresh=true',
    ]);
  });

  it('posts the design, its identity and the chosen link for status', async () => {
    const { calls, fetcher } = recorder({ state: 'current', link: null, matchingLinks: [], selectedInstanceId: 'inst-1' });
    const status = await getOnshapeStatus(designForFamily('OSSE'), identity, fetcher, 'inst-1');
    expect(status.state).toBe('current');
    expect(calls[0].url).toBe('/api/cadlink/onshape/status');
    expect(calls[0].init?.method).toBe('POST');
    const body = JSON.parse(String(calls[0].init?.body)) as Record<string, unknown>;
    expect(body.identity).toEqual(identity);
    expect(body.instanceId).toBe('inst-1');
    expect(body.design).toBeTypeOf('object');
  });

  it('sends with an idempotency key and turns a public-document refusal into a consent request', async () => {
    const sent = recorder({ onshape: { documentName: 'Speaker' } });
    await sendDesignToOnshape(designForFamily('OSSE'), 3, 'speaker', identity, { instanceId: 'inst-1', solveSettings: null }, sent.fetcher, 'key-1');
    expect(sent.calls[0].url).toBe('/api/cadlink/onshape/send');
    expect((sent.calls[0].init?.headers as Record<string, string>)['Idempotency-Key']).toBe('key-1');
    expect(JSON.parse(String(sent.calls[0].init?.body))).toMatchObject({
      designRevision: 3, baseName: 'speaker', identity, instanceId: 'inst-1', allowPublic: false,
    });

    const refused = recorder({ detail: 'This plan creates public documents.' }, 428);
    const failure = await sendDesignToOnshape(designForFamily('OSSE'), 3, 'speaker', identity, { solveSettings: null }, refused.fetcher, 'key-2')
      .catch((reason: unknown) => reason);
    expect(failure).toBeInstanceOf(OnshapePublicConsentRequired);
    expect((failure as Error).message).toBe('This plan creates public documents.');
  });

  it('unlinks one design link, leaving the Onshape document alone', async () => {
    const { calls, fetcher } = recorder({ unlinked: true });
    expect(await unlinkOnshape('wgd_a', 'inst-1', fetcher)).toEqual({ unlinked: true });
    expect(calls[0].url).toBe('/api/cadlink/onshape/unlink');
    expect(calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ designId: 'wgd_a', instanceId: 'inst-1' });

    const failed = recorder({ detail: { message: 'Choose an instance before unlinking it.' } }, 409);
    await expect(unlinkOnshape('wgd_a', null, failed.fetcher)).rejects.toThrow('Choose an instance before unlinking it.');
    expect(JSON.parse(String(failed.calls[0].init?.body))).toEqual({ designId: 'wgd_a', instanceId: null });
  });

  it('returns the linked Part Studio into WG', async () => {
    const { calls, fetcher } = recorder({ translationId: 't-1', bundle: { name: 'speaker.wgreturn' }, ingest: { ingest_id: 'wgi_1' } });
    const result = await returnOnshapeToWg('wgd_a', fetcher, 'inst-1');
    expect(result.ingest.ingest_id).toBe('wgi_1');
    expect(calls[0].url).toBe('/api/cadlink/onshape/return');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ designId: 'wgd_a', instanceId: 'inst-1' });

    const failed = recorder({ detail: 'Onshape is unreachable.' }, 502);
    await expect(returnOnshapeToWg('wgd_a', failed.fetcher)).rejects.toThrow('Onshape is unreachable.');
  });
});
