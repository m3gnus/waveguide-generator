import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { initializeChartThemeDefault, loadChartThemes } from './chartThemes';
import { MATCH_INTERFACE_THEME, preferencesStore, resolveChartTheme } from './preferences';

describe('chart theme startup', () => {
  beforeEach(() => {
    localStorage.clear();
    preferencesStore.resetForTests();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it.each(['top-level', 'theme flag'])('applies the %s server default to exports without mounting Preferences', async (source) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      themes: ['console', { name: 'vellum', label: 'Vellum (paper)', default: source === 'theme flag' }],
      ...(source === 'top-level' ? { default: 'vellum' } : {}),
    }))));
    await initializeChartThemeDefault();
    expect(resolveChartTheme(preferencesStore.getSnapshot().chartTheme)).toBe('vellum');
    expect(localStorage.getItem('waveguide-v2-g3-preferences')).toBeNull();
  });

  it('ignores a default that is absent from the available themes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ themes: ['console'], default: 'missing' }))));
    await initializeChartThemeDefault();
    expect(preferencesStore.getSnapshot().chartTheme).toBe(MATCH_INTERFACE_THEME);
  });

  it.each(['offline', 'non-OK', 'invalid JSON'])('keeps Match interface when startup gets %s', async (failure) => {
    if (failure === 'non-OK') vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 503 })));
    if (failure === 'invalid JSON') vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('invalid')));
    await initializeChartThemeDefault();
    expect(preferencesStore.getSnapshot().chartTheme).toBe(MATCH_INTERFACE_THEME);
    expect(await loadChartThemes()).toEqual({ themes: [] });
  });

  it('bounds startup and ignores a late default', async () => {
    vi.useFakeTimers();
    let respond!: (response: Response) => void;
    let signal!: AbortSignal;
    vi.stubGlobal('fetch', vi.fn((_url, options: RequestInit) => {
      signal = options.signal as AbortSignal;
      return new Promise<Response>((resolve) => { respond = resolve; });
    }));
    const startup = initializeChartThemeDefault();
    await vi.advanceTimersByTimeAsync(1_500);
    await startup;
    expect(signal.aborted).toBe(true);
    expect(preferencesStore.getSnapshot().chartTheme).toBe(MATCH_INTERFACE_THEME);
    respond(new Response(JSON.stringify({ themes: ['vellum'], default: 'vellum' })));
    await vi.advanceTimersByTimeAsync(0);
    expect(preferencesStore.getSnapshot().chartTheme).toBe(MATCH_INTERFACE_THEME);
  });
});
