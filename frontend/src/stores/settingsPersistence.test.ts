import { beforeEach, describe, expect, it, vi } from 'vitest';
import { athPolarOverrides } from './athPolars';
import { DurableSettings, SETTINGS_NAMESPACES } from './durableSettings';
import {
  resetSolveOptionsStore,
  restorePolarUiFromAthBlocks,
  restoreSolveSettingsFromBlocks,
  useSolveOptionsStore,
} from './solveOptions';
import { wgSolveOverrides, withWgSolveBlock, WG_SOLVE_BLOCK } from './wgSolveBlock';
import { wgSolveSettingsFromSolveOptions, wgSolveSettingsFromStore } from './designWire';

const CUSTOM_POLAR = {
  angleStart: 0,
  angleEnd: 90,
  angleStep: 2,
  distance: 3.5,
  normAngle: 10,
  diagonalAngle: 30,
  enabledAxes: ['horizontal'] as const,
  observationOrigin: 'throat' as const,
  sphericalSampling: true,
  fieldPlane: false,
};

function response(body: unknown, ok = true): Response {
  return { ok, json: async () => body } as unknown as Response;
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

interface BackendCall {
  namespace: string;
  method: string;
  writer: string | null;
  seq: number | null;
  ok: boolean;
}

/**
 * A stand-in for `server/settings`: it commits in the order requests reach it
 * and refuses a write its ordering guard finds stale. `ordered: false` is the
 * same contract without the guard -- what an older build behaves like -- so
 * the client's own safety net can be tested separately from the server's.
 */
function settingsBackend({
  ordered = true,
  before,
}: {
  ordered?: boolean;
  before?: (call: { namespace: string; method: string; value: unknown }) => Promise<void> | void;
} = {}) {
  const namespaces = new Map<string, unknown>();
  const accepted = new Map<string, { writer: string; seq: number }>();
  const calls: BackendCall[] = [];
  const envelope = () => ({ schemaVersion: 1, namespaces: Object.fromEntries(namespaces) });
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    if (!init?.method) return response(envelope());
    const url = String(input);
    const namespace = url.slice(url.lastIndexOf('/') + 1);
    const headers = (init.headers ?? {}) as Record<string, string>;
    const writer = headers['X-WG-Settings-Writer'] ?? null;
    const rawSeq = headers['X-WG-Settings-Seq'];
    const seq = rawSeq === undefined ? null : Number(rawSeq);
    const value = init.body === undefined ? null : JSON.parse(String(init.body));
    await before?.({ namespace, method: init.method, value });
    const previous = accepted.get(namespace);
    if (ordered && writer !== null && seq !== null
        && previous !== undefined && previous.writer === writer && seq <= previous.seq) {
      calls.push({ namespace, method: init.method, writer, seq, ok: false });
      return response({ detail: 'A newer settings write is already stored.' }, false);
    }
    if (init.method === 'DELETE') namespaces.delete(namespace);
    else namespaces.set(namespace, value);
    if (writer !== null && seq !== null) accepted.set(namespace, { writer, seq });
    else accepted.delete(namespace);
    calls.push({ namespace, method: init.method, writer, seq, ok: true });
    return response(envelope());
  }) as unknown as typeof fetch;
  return { namespaces, calls, fetcher };
}

describe('opening a design does not discard remembered settings', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('reports nothing to apply when a config carries no directivity blocks', () => {
    expect(athPolarOverrides({})).toBeNull();
    expect(athPolarOverrides({ 'Mesh.Enclosure': { items: { Depth: '0' } } })).toBeNull();
  });

  /**
   * The regression this exists for: every ATH file, and every WG design serialized
   * before WG wrote polar blocks, has no directivity section. Opening one used
   * to reset distance, step, normalization, planes and origin to defaults and
   * overwrite the stored copy, so the settings were gone for good.
   */
  it('keeps the measurement rig when the opened config is silent about it', () => {
    useSolveOptionsStore.setState({ polar: { ...CUSTOM_POLAR, enabledAxes: ['horizontal'] } });

    restorePolarUiFromAthBlocks({ 'Mesh.Enclosure': { items: { Depth: '0' } } });

    expect(useSolveOptionsStore.getState().polar).toMatchObject({
      distance: 3.5,
      angleStep: 2,
      normAngle: 10,
      observationOrigin: 'throat',
      enabledAxes: ['horizontal'],
    });
  });

  it('applies only the values the config actually states', () => {
    useSolveOptionsStore.setState({ polar: { ...CUSTOM_POLAR, enabledAxes: ['horizontal'] } });

    restorePolarUiFromAthBlocks({
      'ABEC.Polars:SPL_H': { items: { MapAngleRange: '0,180,37', Distance: '1.8' } },
    });

    const { polar } = useSolveOptionsStore.getState();
    expect(polar.distance).toBe(1.8);
    expect(polar.angleEnd).toBe(180);
    // Untouched by the file, so still the user's own choices.
    expect(polar.normAngle).toBe(10);
    expect(polar.observationOrigin).toBe('throat');
    expect(polar.sphericalSampling).toBe(true);
    expect(polar.fieldPlane).toBe(false);
  });
});

describe('WG.Solve config block', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('round-trips portable solve settings without replacing machine execution choices', () => {
    useSolveOptionsStore.setState({
      engine: 'metal',
      solverMode: 'circsym',
      symmetry: 'half_xz',
      meshValidationMode: 'strict',
      verbose: true,
      frequencySpacing: 'linear',
      frequencyMode: 'list',
      frequencyListText: '500, 1000, 2000',
      polar: { ...CUSTOM_POLAR, enabledAxes: ['horizontal'] },
    });
    const blocks = withWgSolveBlock({}, wgSolveSettingsFromStore());
    blocks[WG_SOLVE_BLOCK].items.Engine = 'metal'; // legacy author hint

    resetSolveOptionsStore();
    useSolveOptionsStore.setState({ engine: 'bempp', solverMode: 'full_3d' });
    restoreSolveSettingsFromBlocks(blocks);

    const state = useSolveOptionsStore.getState();
    expect(state).toMatchObject({
      engine: 'bempp',
      solverMode: 'full_3d',
      symmetry: 'half_xz',
      meshValidationMode: 'strict',
      verbose: true,
      frequencySpacing: 'linear',
      frequencyMode: 'list',
      frequencyListText: '500, 1000, 2000',
    });
    expect(state.polar.observationOrigin).toBe('throat');
    expect(state.polar.sphericalSampling).toBe(true);
    expect(state.polar.fieldPlane).toBe(false);
  });

  it('leaves other blocks alone so imported ATH passthrough survives a save', () => {
    const blocks = withWgSolveBlock(
      { Report: { items: { Title: 'kept' }, lines: [], comments: [], entries: [] } },
      wgSolveSettingsFromStore(),
    );
    expect(blocks.Report.items).toEqual({ Title: 'kept' });
    expect(blocks[WG_SOLVE_BLOCK].items.Engine).toBeUndefined();
  });

  it('drops values it cannot read rather than guessing at a different solve', () => {
    const overrides = wgSolveOverrides({
      [WG_SOLVE_BLOCK]: {
        items: {
          Engine: 'metal',
          MeshValidation: 'nonsense',
          SweepSpacing: 'octave',
          Verbose: 'maybe',
          ObservationOrigin: 'ear',
        },
      },
    });
    expect(overrides).toEqual({});
  });

  it('will not restore list mode without a list that actually parses', () => {
    const overrides = wgSolveOverrides({
      [WG_SOLVE_BLOCK]: { items: { SweepPoints: 'list', Frequencies: '2000, 500' } },
    });
    expect(overrides?.frequencyMode).toBeUndefined();
    expect(overrides?.frequencyListText).toBeUndefined();
  });

  it('describes the run being exported, not the draft on screen', () => {
    const settings = wgSolveSettingsFromSolveOptions({
      engine: 'bempp',
      mesh_validation_mode: 'off',
      frequency_spacing: 'linear',
      frequencies_hz: [400, 800],
      polar_config: { observation_origin: 'throat', spherical_sampling: true, field_plane: false },
    });
    expect(settings).toMatchObject({
      meshValidationMode: 'off',
      frequencySpacing: 'linear',
      frequencyMode: 'list',
      frequencyListText: '400, 800',
      observationOrigin: 'throat',
      sphericalSampling: true,
      fieldPlane: false,
    });
    expect(wgSolveSettingsFromSolveOptions(undefined)).toBeNull();
  });
});

describe('every remembered setting has a durable home', () => {
  /**
   * The move server-side is only complete if nothing still writes straight to
   * the browser. A store that kept its own `localStorage` key would keep the
   * original bug for whatever it holds, and would do it silently.
   */
  it('leaves no store writing to localStorage directly', async () => {
    const sources = import.meta.glob('../**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
    // A glob that quietly matched nothing would make this pass forever. Anchor
    // it on the source count and on a file known to contain the pattern.
    expect(Object.keys(sources).length).toBeGreaterThan(100);
    expect(sources['../design/ParamPanel.tsx']).toMatch(/localStorage\.getItem/);

    const offenders = Object.entries(sources)
      .filter(([path]) => !path.includes('.test.') && !path.endsWith('durableSettings.ts'))
      .filter(([, text]) => /localStorage\.(setItem|removeItem)\s*\(/.test(text))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });
});

describe('durable settings', () => {
  beforeEach(() => { localStorage.clear(); });

  function harness(remote: Record<string, unknown>) {
    const calls: Array<{ url: string; method?: string; body?: unknown }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method, body: init?.body });
      if (String(input).endsWith('/api/settings') && !init?.method) {
        return response({ schemaVersion: 1, namespaces: remote });
      }
      return response({});
    }) as unknown as typeof fetch;
    const settings = new DurableSettings({ fetcher, writeDelayMs: () => 0 });
    return { calls, settings };
  }

  it('prefers the stored copy over whatever this browser happens to hold', async () => {
    localStorage.setItem(SETTINGS_NAMESPACES.theme, 'dark');
    const { settings } = harness({ theme: 'light' });

    const seen: Array<string | null> = [];
    settings.subscribe('theme', (raw) => seen.push(raw));
    await settings.hydrate();

    expect(settings.get('theme')).toBe('light');
    expect(localStorage.getItem(SETTINGS_NAMESPACES.theme)).toBe('light');
    expect(seen).toEqual(['light']);
  });

  /** An existing installation's settings must survive the move server-side. */
  it('publishes this browser\'s settings the first time the server has none', async () => {
    localStorage.setItem(SETTINGS_NAMESPACES.theme, 'light');
    const { calls, settings } = harness({});

    await settings.hydrate();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const upload = calls.find((call) => call.url.endsWith('/api/settings/theme'));
    expect(upload?.method).toBe('PUT');
    expect(upload?.body).toBe(JSON.stringify('light'));
  });

  it('does not report a value it never received', async () => {
    localStorage.setItem(SETTINGS_NAMESPACES.theme, 'light');
    const settings = new DurableSettings({
      fetcher: (async () => { throw new Error('offline'); }) as unknown as typeof fetch,
      writeDelayMs: () => 0,
    });
    const seen: Array<string | null> = [];
    settings.subscribe('theme', (raw) => seen.push(raw));

    await settings.hydrate();

    expect(seen).toEqual([]);
    expect(settings.get('theme')).toBe('light');
  });

  /**
   * A backend that is still warming up must cost a moment of cached settings,
   * not a whole session of them. The timeout bounds how long startup waits,
   * never whether a late answer is used.
   */
  it('still adopts an answer that arrives after startup gave up waiting', async () => {
    localStorage.setItem(SETTINGS_NAMESPACES.theme, 'dark');
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const settings = new DurableSettings({
      writeDelayMs: () => 0,
      fetcher: (async () => {
        await gate;
        return response({ schemaVersion: 1, namespaces: { theme: 'light' } });
      }) as unknown as typeof fetch,
    });
    const seen: Array<string | null> = [];
    settings.subscribe('theme', (raw) => seen.push(raw));

    await settings.hydrate({ timeoutMs: 0 });
    expect(settings.get('theme')).toBe('dark');

    release?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(settings.get('theme')).toBe('light');
    expect(seen).toEqual(['light']);
  });

  it('does not let a late answer overwrite a setting edited while hydration was in flight', async () => {
    localStorage.setItem(SETTINGS_NAMESPACES.theme, 'dark');
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const settings = new DurableSettings({
      writeDelayMs: () => 0,
      fetcher: (async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method) return response({});
        await gate;
        return response({ schemaVersion: 1, namespaces: { theme: 'light' } });
      }) as unknown as typeof fetch,
    });
    const seen: Array<string | null> = [];
    settings.subscribe('theme', (raw) => seen.push(raw));

    await settings.hydrate({ timeoutMs: 0 });
    settings.set('theme', 'system');

    release?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(settings.get('theme')).toBe('system');
    expect(seen).toEqual([]);
  });

  it('preserves and retries a local setting after a non-OK upload and restart', async () => {
    const failedUpload = vi.fn(async () => response({}, false)) as unknown as typeof fetch;
    const first = new DurableSettings({ fetcher: failedUpload, writeDelayMs: () => 0 });

    first.set('theme', 'local-newer');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(failedUpload).toHaveBeenCalledWith('/api/settings/theme', expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify('local-newer'),
    }));

    // A new instance represents the next launch. Its GET sees the older server
    // copy, but the persistent local-newer marker keeps the cache authoritative
    // and makes hydration retry the failed PUT.
    const retryCalls: Array<{ url: string; method?: string; body?: unknown }> = [];
    const restarted = new DurableSettings({
      writeDelayMs: () => 0,
      fetcher: (async (input: RequestInfo | URL, init?: RequestInit) => {
        retryCalls.push({ url: String(input), method: init?.method, body: init?.body });
        if (!init?.method) {
          return response({ schemaVersion: 1, namespaces: { theme: 'server-older' } });
        }
        return response({});
      }) as unknown as typeof fetch,
    });
    const seen: Array<string | null> = [];
    restarted.subscribe('theme', (raw) => seen.push(raw));

    await restarted.hydrate();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(restarted.get('theme')).toBe('local-newer');
    expect(seen).toEqual([]);
    expect(retryCalls).toContainEqual({
      url: '/api/settings/theme',
      method: 'PUT',
      body: JSON.stringify('local-newer'),
    });

    // The successful retry clears the marker, so a later launch can adopt a
    // genuinely newer server copy normally.
    const afterRetry = new DurableSettings({
      writeDelayMs: () => 0,
      fetcher: (async () => response({
        schemaVersion: 1,
        namespaces: { theme: 'server-newer' },
      })) as unknown as typeof fetch,
    });
    await afterRetry.hydrate();
    expect(afterRetry.get('theme')).toBe('server-newer');
  });

  it.each([
    { cached: 'old-draft', next: 'new-draft' },
    { cached: null, next: 'first-draft' },
    { cached: 'old-draft', next: null },
  ])('uploads the latest draft when cache writes fail: $cached → $next', async ({ cached, next }) => {
    const key = SETTINGS_NAMESPACES.designDraft;
    if (cached !== null) localStorage.setItem(key, cached);
    let failWrites = true;
    const storage = {
      getItem: (name: string) => localStorage.getItem(name),
      setItem: (name: string, value: string) => {
        if (failWrites) throw new DOMException('Quota exceeded', 'QuotaExceededError');
        localStorage.setItem(name, value);
      },
      removeItem: (name: string) => {
        if (failWrites) throw new Error('Storage is read-only');
        localStorage.removeItem(name);
      },
    } as Storage;
    const fetcher = vi.fn(async () => response({}));
    const settings = new DurableSettings({ storage, fetcher, writeDelayMs: () => 0 });
    settings.set('designDraft', next);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(settings.get('designDraft')).toBe(next);
    expect(fetcher).toHaveBeenCalledWith('/api/settings/designDraft', expect.objectContaining({
      method: next === null ? 'DELETE' : 'PUT',
      body: next === null ? undefined : JSON.stringify(next),
    }));
    // A later successful cache write restores ordinary shared-cache reads.
    failWrites = false;
    settings.set('designDraft', 'recovered');
    await new Promise((resolve) => setTimeout(resolve, 0));
    localStorage.setItem(key, 'external-change');
    expect(settings.get('designDraft')).toBe('external-change');
  });

  it('keeps working when the browser refuses to store anything', async () => {
    const settings = new DurableSettings({ storage: null, writeDelayMs: () => 0, fetcher: (async () => response({})) as unknown as typeof fetch });
    settings.set('theme', 'light');
    expect(settings.get('theme')).toBe('light');
  });

  /**
   * Closing the window sends the pending value immediately and deliberately
   * ahead of the request queue, so an upload already on the wire can reach the
   * server after it. Whichever arrives last, the value the user last chose is
   * the one that must survive the next launch.
   */
  async function flushOvertakesAnUploadInFlight(ordered: boolean) {
    const release = new Map<string, () => void>();
    const held = new Map<string, Promise<void>>();
    for (const value of ['A', 'B']) {
      held.set(value, new Promise<void>((resolve) => release.set(value, resolve)));
    }
    const backend = settingsBackend({
      ordered,
      before: async ({ value }) => { await held.get(String(value)); },
    });

    let delay = 0;
    const settings = new DurableSettings({ fetcher: backend.fetcher, writeDelayMs: () => delay });
    settings.set('theme', 'A');
    await tick(); // A is on the wire, and the server has not committed it yet.
    delay = 50;
    settings.set('theme', 'B'); // Debounced: for now only this tab holds B.
    settings.flush(); // pagehide: keepalive, ahead of the queue.
    await tick();

    release.get('B')?.();
    await tick();
    release.get('A')?.(); // The older request commits last.
    await tick();
    await tick();

    return { backend, settings };
  }

  it('lets the server refuse the stale half of an unload-flush race', async () => {
    const { backend } = await flushOvertakesAnUploadInFlight(true);

    expect(backend.namespaces.get('theme')).toBe('B');
    const writes = backend.calls.filter((call) => call.namespace === 'theme');
    expect(writes.map((call) => call.ok)).toEqual([true, false]);
    // One writer, increasing sequence numbers: that pair is the whole basis on
    // which the server can tell the stale request from the fresh one. The
    // refused request is the one carrying the lower number.
    expect(typeof writes[0]?.writer).toBe('string');
    expect(new Set(writes.map((call) => call.writer)).size).toBe(1);
    expect(writes[1]?.seq as number).toBeLessThan(writes[0]?.seq as number);
  });

  it('keeps the retry marker when a server without the guard commits the stale write last', async () => {
    const { backend } = await flushOvertakesAnUploadInFlight(false);

    // Nothing on the client can retract a request the server already accepted,
    // so the evidence that the browser holds the newer copy has to survive.
    expect(backend.namespaces.get('theme')).toBe('A');
    expect(localStorage.getItem(`${SETTINGS_NAMESPACES.theme}.local-newer`)).toBe('1');
    expect(localStorage.getItem(SETTINGS_NAMESPACES.theme)).toBe('B');

    const restarted = new DurableSettings({ fetcher: backend.fetcher, writeDelayMs: () => 0 });
    const seen: Array<string | null> = [];
    restarted.subscribe('theme', (raw) => seen.push(raw));
    await restarted.hydrate();
    await tick();

    expect(restarted.get('theme')).toBe('B');
    expect(backend.namespaces.get('theme')).toBe('B');
    expect(seen).toEqual([]);
  });

  it('still seeds a namespace the server has never heard of', async () => {
    localStorage.setItem(SETTINGS_NAMESPACES.theme, 'dark');
    const backend = settingsBackend();
    const settings = new DurableSettings({ fetcher: backend.fetcher, writeDelayMs: () => 0 });

    await settings.hydrate();
    await tick();

    expect(backend.namespaces.get('theme')).toBe('dark');
  });
});
