import { describe, expect, it } from 'vitest';
import { fetchMeshReplay, MeshReplayRefusal, openMeshReplay } from './meshReplay';

const REPLAY_FALLBACK = 'WG could not reopen this mesh. Solve the design again.';

describe('mesh replay refusals', () => {
  it.each([
    ['legacy refusal', 'legacy refusal'],
    [[{ loc: ['body', 'version'], msg: 'Value error, unsupported' }], 'unsupported'],
    ['', REPLAY_FALLBACK],
    [null, REPLAY_FALLBACK],
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
