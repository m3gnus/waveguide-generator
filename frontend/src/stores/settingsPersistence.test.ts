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
};

function response(body: unknown, ok = true): Response {
  return { ok, json: async () => body } as unknown as Response;
}

describe('opening a design does not discard remembered settings', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('reports nothing to apply when a config carries no directivity blocks', () => {
    expect(athPolarOverrides({})).toBeNull();
    expect(athPolarOverrides({ 'Mesh.Enclosure': { items: { Depth: '0' } } })).toBeNull();
  });

  /**
   * The regression this exists for: every ATH file, and every WG design saved
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
  });
});

describe('WG.Solve config block', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('round-trips every solver setting that used to live only in the browser', () => {
    useSolveOptionsStore.setState({
      engine: 'metal',
      symmetry: 'half_xz',
      meshValidationMode: 'strict',
      verbose: true,
      frequencySpacing: 'linear',
      frequencyMode: 'list',
      frequencyListText: '500, 1000, 2000',
      polar: { ...CUSTOM_POLAR, enabledAxes: ['horizontal'] },
    });
    const blocks = withWgSolveBlock({}, wgSolveSettingsFromStore());

    resetSolveOptionsStore();
    restoreSolveSettingsFromBlocks(blocks);

    const state = useSolveOptionsStore.getState();
    expect(state).toMatchObject({
      engine: 'metal',
      symmetry: 'half_xz',
      meshValidationMode: 'strict',
      verbose: true,
      frequencySpacing: 'linear',
      frequencyMode: 'list',
      frequencyListText: '500, 1000, 2000',
    });
    expect(state.polar.observationOrigin).toBe('throat');
    expect(state.polar.sphericalSampling).toBe(true);
  });

  it('leaves other blocks alone so imported ATH passthrough survives a save', () => {
    const blocks = withWgSolveBlock(
      { Report: { items: { Title: 'kept' }, lines: [], comments: [], entries: [] } },
      wgSolveSettingsFromStore(),
    );
    expect(blocks.Report.items).toEqual({ Title: 'kept' });
    expect(blocks[WG_SOLVE_BLOCK].items.Engine).toBe('auto');
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
    expect(overrides).toEqual({ engine: 'metal' });
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
      polar_config: { observation_origin: 'throat', spherical_sampling: true },
    });
    expect(settings).toMatchObject({
      engine: 'bempp',
      meshValidationMode: 'off',
      frequencySpacing: 'linear',
      frequencyMode: 'list',
      frequencyListText: '400, 800',
      observationOrigin: 'throat',
      sphericalSampling: true,
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

  it('keeps working when the browser refuses to store anything', async () => {
    const settings = new DurableSettings({ storage: null, writeDelayMs: () => 0, fetcher: (async () => response({})) as unknown as typeof fetch });
    settings.set('theme', 'light');
    expect(settings.get('theme')).toBe('light');
  });
});
