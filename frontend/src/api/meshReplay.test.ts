import { afterEach, describe, expect, it, vi } from 'vitest';
import { activityStore } from '../shell/activity';
import { fetchMeshReplay, MeshReplayRefusal, openMeshReplay } from './meshReplay';

const REPLAY_FALLBACK = 'WG could not reopen this mesh. Solve the design again.';
afterEach(() => { activityStore.reset(); vi.useRealTimers(); vi.restoreAllMocks(); });

it('mesh replay uses the staged operation GET and Stop clears its activity', async () => {
  let signal: AbortSignal | null | undefined;
  const pending = fetchMeshReplay('retained', (url, init) => {
    expect(url).toBe('/api/jobs/retained/mesh-replay');
    expect(new Headers(init?.headers).get('X-WG-Operation-Id')).toBeTruthy();
    signal = init?.signal;
    return new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(signal?.reason), { once: true }));
  });
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  activityStore.getSnapshot()[0].cancel!();
  await rejected;
  expect(signal?.aborted).toBe(true);
  expect(activityStore.getSnapshot()).toEqual([]);
});

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
