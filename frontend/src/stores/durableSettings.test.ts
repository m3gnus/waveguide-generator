import { describe, expect, it, vi } from 'vitest';
import { DurableSettings } from './durableSettings';

const fullStorage = () => ({ getItem: () => null, setItem: () => { throw new Error('quota'); }, removeItem: () => {} }) as unknown as Storage;
const memoryStorage = () => { const values = new Map<string, string>(); return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } } as unknown as Storage; };
const answer = (ok: boolean) => vi.fn(async () => new Response('{}', { status: ok ? 200 : 500 })) as unknown as typeof fetch;

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
