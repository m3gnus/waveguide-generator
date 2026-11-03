import { expect, it } from 'vitest';
import { ImportedModelApiError, requestImportedModel } from './importedModels';

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
