import { afterEach, expect, it, vi } from 'vitest';
import { preferencesStore, resolveChartTheme } from './prefs/preferences';

const startup = vi.hoisted(() => ({ render: vi.fn(), hydrate: vi.fn().mockResolvedValue(undefined) }));
vi.mock('react-dom/client', () => ({ createRoot: () => ({ render: startup.render }) }));
vi.mock('./App', () => ({ default: () => null }));
vi.mock('./shell/clientErrors', () => ({ startClientErrorReporting: () => () => undefined }));
vi.mock('./stores/durableSettings', async (importOriginal) => ({
  ...await importOriginal<typeof import('./stores/durableSettings')>(),
  durableSettings: { get: () => null, subscribe: () => () => undefined, hydrate: startup.hydrate },
  startDurableSettings: () => undefined,
}));
vi.mock('./stores/autosave', () => ({
  restoreAutosaveWithLateRetry: () => () => undefined,
  startAutosave: () => ({ dispose: () => undefined }),
}));
afterEach(() => { vi.unstubAllGlobals(); });

it('waits for the server theme before rendering charts and export controls', async () => {
  preferencesStore.resetForTests();
  let respond!: (response: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>((resolve) => { respond = resolve; }));
  vi.stubGlobal('fetch', fetcher);
  await import('./main');
  await vi.waitFor(() => { expect(fetcher).toHaveBeenCalledWith('/api/themes', expect.anything()); });
  expect(startup.hydrate).toHaveBeenCalledWith({ timeoutMs: 1_500 });
  expect(startup.render).not.toHaveBeenCalled();
  respond(new Response(JSON.stringify({ themes: ['console', 'vellum'], default: 'vellum' })));
  await vi.waitFor(() => { expect(startup.render).toHaveBeenCalledTimes(1); });
  expect(resolveChartTheme(preferencesStore.getSnapshot().chartTheme)).toBe('vellum');
});
