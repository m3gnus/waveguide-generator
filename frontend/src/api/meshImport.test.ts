import { describe, expect, it, vi } from 'vitest';
import { inspectMesh } from './meshImport';
import { ImportedModelApiError, readImportedModel } from './importedModels';

describe('imported model error envelopes', () => {
  it.each([
    { body: { error: { code: 'mesh_refused', message: 'Close the opening in the meshing tool.' }, detail: 'older detail' }, code: 'mesh_refused', message: 'Close the opening in the meshing tool.' },
    { body: { detail: 'Close the opening in the meshing tool.' }, code: null, message: 'Close the opening in the meshing tool.' },
  ])('uses the same error type and parsing for inspect and model reads ($code)', async ({ body, code, message }) => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(body), { status: 422 }));
    for (const request of [() => inspectMesh('speaker.msh', 'mesh', fetcher), () => readImportedModel('mesh-file:test', null, fetcher)]) {
      const error = await request().catch(error => error);
      expect(error).toBeInstanceOf(ImportedModelApiError);
      expect(error).toMatchObject({ status: 422, code, message });
    }
  });
});
