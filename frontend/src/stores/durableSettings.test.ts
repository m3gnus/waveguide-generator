import { describe, expect, it, vi } from 'vitest';
import { DurableSettings, SETTINGS_NAMESPACES } from './durableSettings';

const fullStorage = () => ({ getItem: () => null, setItem: () => { throw new Error('quota'); }, removeItem: () => {} }) as unknown as Storage;
const memoryStorage = () => { const values = new Map<string, string>(); return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } } as unknown as Storage; };
const answer = (ok: boolean) => vi.fn(async () => new Response('{}', { status: ok ? 200 : 500 })) as unknown as typeof fetch;

it.each([{}, {
  sourceAssemblyDraft: '{obsolete draft',
  sourceAssemblyAuthoring: '{obsolete authoring',
}])('ignores retired assembly settings in the browser and server on load: %j', async namespaces => {
  const storage = memoryStorage();
  for (const key of ['wg2.source-assembly-draft.v1', 'wg2.source-assembly-authoring.v1']) {
    storage.setItem(key, '{obsolete cache');
    storage.setItem(`${key}.local-newer`, '1');
  }
  const read = vi.spyOn(storage, 'getItem');
  const write = vi.spyOn(storage, 'setItem');
  const remove = vi.spyOn(storage, 'removeItem');
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ namespaces })));
  const settings = new DurableSettings({ storage, fetcher });

  await expect(settings.hydrate()).resolves.toBeUndefined();
  settings.flush();
  expect(fetcher.mock.calls).toEqual([['/api/settings']]);
  expect(Object.keys(SETTINGS_NAMESPACES)).not.toContain('sourceAssemblyDraft');
  expect(Object.keys(SETTINGS_NAMESPACES)).not.toContain('sourceAssemblyAuthoring');
  expect(read.mock.calls.some(([key]) => key.includes('source-assembly'))).toBe(false);
  expect(write).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
});

describe('whether a draft is kept anywhere', () => {
  it('reports a draft neither this browser nor the server took, and recovers', async () => {
    const fetcher = vi.fn(answer(false));
    const settings = new DurableSettings({ storage: fullStorage(), fetcher, writeDelayMs: () => 0 });
    const heard = vi.fn();
    settings.subscribeKept(heard);
    settings.set('designDraft', 'draft');
    await vi.waitFor(() => expect(settings.isNotKept('designDraft')).toBe(true));
    expect(heard).toHaveBeenCalled();
    fetcher.mockImplementation(answer(true));
    settings.set('designDraft', 'draft 2');
    await vi.waitFor(() => expect(settings.isNotKept('designDraft')).toBe(false));
  });

  it('counts either copy as kept', async () => {
    const localOnly = new DurableSettings({ storage: memoryStorage(), fetcher: answer(false), writeDelayMs: () => 0 });
    localOnly.set('designDraft', 'draft');
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(localOnly.isNotKept('designDraft')).toBe(false);
    const serverOnly = new DurableSettings({ storage: fullStorage(), fetcher: answer(true), writeDelayMs: () => 0 });
    serverOnly.set('designDraft', 'draft');
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(serverOnly.isNotKept('designDraft')).toBe(false);
  });
});
