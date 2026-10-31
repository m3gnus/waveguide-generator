import { describe, expect, it } from 'vitest';
import { fetchMeshReplay, MeshReplayRefusal, openMeshReplay } from './meshReplay';

describe('mesh replay refusals', () => {
  it.each([
    ['legacy refusal', 'legacy refusal'],
    [[{ loc: ['body', 'version'], msg: 'Value error, unsupported' }], 'body.version: Value error, unsupported'],
    ['', ''],
    [null, 'Mesh replay failed (422).'],
  ])('retains detail %j and status on import and download', async (detail, message) => {
    for (const request of [openMeshReplay, fetchMeshReplay]) {
      const response = Response.json({ detail }, { status: 422 });
      const error = await request('input', async () => response).catch(error => error);
      expect(error).toBeInstanceOf(MeshReplayRefusal);
      expect(error).toMatchObject({ message, status: 422 });
      expect(response.bodyUsed).toBe(true);
    }
  });
});
