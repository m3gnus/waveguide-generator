import * as httpError from './httpError';
import { afterEach, expect, it, vi } from 'vitest';
import { ImportedModelApiError, requestImportedModel } from './importedModels';

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it.each(['PUT', 'POST'])('leaves %s lifetime, headers and keepalive unchanged', async method => {
  vi.useFakeTimers();
  const controller = new AbortController();
  const init = { method, signal: controller.signal, keepalive: true, headers: { 'X-WG-Setup-Seq': '3' }, body: '{}' };
  let finish!: (response: Response) => void;
  const fetcher = vi.fn<typeof fetch>(() => new Promise(resolve => { finish = resolve; }));
  const pending = requestImportedModel('/api/models/test/setup', fetcher, 'Setup', init);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetcher).toHaveBeenCalledWith('/api/models/test/setup', init);
  expect(fetcher.mock.calls[0][1]).toBe(init);
  expect(controller.signal.aborted).toBe(false);
  finish(Response.json({ revision_id: 'saved' }));
  await expect(pending).resolves.toEqual({ revision_id: 'saved' });
});

it('maps a deadline during refusal decoding to an actionable retry', async () => {
  const reason = new DOMException('Timed out', 'TimeoutError');
  const response = new Response(null, { status: 422 });
  response.json = async () => { throw reason; };
  await expect(requestImportedModel('/api/models/test', async () => response, 'This model')).rejects.toMatchObject({
    message: 'This model took too long to load. Try opening it again.', cause: reason,
  });
});

it.each([200, 422])('preserves the exact body abort (%s)', async status => {
  const reason = new DOMException('Stopped', 'AbortError');
  const fetcher: typeof fetch = async () => ({
    ok: status === 200, status, json: async () => { throw reason; },
  }) as unknown as Response;
  await expect(requestImportedModel('/api/models/test', fetcher, 'This model')).rejects.toBe(reason);
});

it('keeps the HTTP refusal and code for other model readers', async () => {
  const fetcher: typeof fetch = async () => new Response(JSON.stringify({
    error: { code: 'model_not_found', message: 'This model is gone.' },
  }), { status: 404 });
  await expect(requestImportedModel('/api/models/test', fetcher, 'This model')).rejects.toMatchObject({
    name: 'ImportedModelApiError', status: 404, code: 'model_not_found', message: 'This model is gone.',
  });
});

it('retains the fallback refusal for malformed error bodies', async () => {
  const fetcher: typeof fetch = async () => new Response('not JSON', { status: 503 });
  await expect(requestImportedModel('/api/models/test', fetcher, 'This model')).rejects.toBeInstanceOf(ImportedModelApiError);
});

it('uses the shared abort classifier for a refused body', async () => {
  const classify = vi.spyOn(httpError, 'isAbortError');
  const reason = { name: 'AbortError' };
  try {
    const response = new Response(null, { status: 422 });
    response.json = async () => { throw reason; };
    await expect(requestImportedModel('/model', async () => response, 'Model')).rejects.toBe(reason);
    expect(classify).toHaveBeenCalledWith(reason);
  } finally { classify.mockRestore(); }
});
