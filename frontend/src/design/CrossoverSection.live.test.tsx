import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle } from '../api/cadlink';
import type { JobResults } from '../api/results';
import { expandLegacy, toWire } from '../results/crossoverSpec';
import { latestCombine, type ShownCombine } from '../results/latestCombine';
import type { CombineMetadata } from '../results/types';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore } from '../stores/document';
import { SETTINGS_NAMESPACES } from '../stores/durableSettings';
import { CadCrossover } from './CrossoverSection';

const recombineMocks = vi.hoisted(() => ({ recombine: vi.fn() }));
vi.mock('../api/results', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  recombineJobResults: recombineMocks.recombine,
}));

const bundle = {
  name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: '2026-08-11T00:00:00Z', readable: true,
  documentName: 'Speaker', requestId: null, sourceCount: 2, instanceCount: 1,
  sources: [
    { id: 'source-mf', role: 'MF', required: true, suggestedResolutionMm: 8, defaultDriveChannelId: 'drive-mf' },
    { id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 3, defaultDriveChannelId: 'drive-hf' },
  ],
} satisfies CadReturnBundle;

/** The resolved payload of a run combined as LR4 at 1 kHz, auto everything.
 * `resolved` overrides what the solver reported per member, which is how the
 * automatic and maximum readouts get something to read. */
function shownCombineOf(
  resolved: Record<string, Record<string, unknown>> = {},
): CombineMetadata {
  const wire = toWire(expandLegacy(['drive-mf', 'drive-hf'], [1_000]));
  return {
    members: ['drive-mf', 'drive-hf'],
    member_roles: ['MF', 'HF'],
    reference: 'drive-hf',
    crossovers_hz: [1_000],
    channels: Object.fromEntries(['drive-mf', 'drive-hf'].map((member) => [member, {
      ...wire.channels[member],
      gain_db: 0, gain_mode: 'auto', gain_auto_db: 0,
      delay_ms: 0, delay_mode: 'auto', delay_auto_ms: 0,
      inverted: false, invert_mode: 'auto',
      ...(resolved[member] ?? {}),
    }])),
  } as unknown as CombineMetadata;
}

describe('live recombine from the rail', () => {
  let host: HTMLDivElement;
  let root: Root;
  let onApplied: ReturnType<typeof vi.fn<(jobId: string, updated: JobResults) => void>>;

  const publishShown = (overrides: Partial<ShownCombine> = {}) => act(() => latestCombine.publish({
    jobId: 'job-1', channelId: 'combined', combine: shownCombineOf(), canApply: true,
    blockedReason: null, recall: null, onApplied, ...overrides,
  }));

  const render = () => act(() => root.render(<CadCrossover/>));

  const setSlope = (value: string) => act(() => {
    const slope = host.querySelector<HTMLSelectElement>('[aria-label="MF → HF slope"]')!;
    slope.value = value;
    slope.dispatchEvent(new Event('change', { bubbles: true }));
  });

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
    latestCombine.reset();
    recombineMocks.recombine.mockReset();
    onApplied = vi.fn<(jobId: string, updated: JobResults) => void>();
    useCadReturnStore.setState({
      selectedBundle: bundle,
      driveChannels: [
        { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
      ],
      combineEnabled: null,
    });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    latestCombine.reset();
    vi.useRealTimers();
    vi.restoreAllMocks();
    host.remove();
  });

  it('applies an edit to the shown run after the debounce, through the bridge', async () => {
    const updated = { channels: {} } as unknown as JobResults;
    recombineMocks.recombine.mockResolvedValue(updated);
    render();
    publishShown();
    expect(host.textContent).toContain('Changes apply to the shown combined result immediately');

    setSlope('2');
    expect(recombineMocks.recombine).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(450); await Promise.resolve(); });

    expect(recombineMocks.recombine).toHaveBeenCalledTimes(1);
    const [jobId, wire] = recombineMocks.recombine.mock.calls[0] as [string, { id: string; channels: Record<string, { lp: unknown }> }];
    expect(jobId).toBe('job-1');
    expect(wire.id).toBe('combined');
    expect(wire.channels['drive-mf'].lp).toEqual({ family: 'lr', order: 2, fc_hz: 1_000 });
    expect(onApplied).toHaveBeenCalledWith('job-1', updated);
  });

  it('stays quiet while the specs already agree, or the run cannot be applied to', async () => {
    render();
    publishShown();
    await act(async () => { vi.advanceTimersByTime(1_000); await Promise.resolve(); });
    expect(recombineMocks.recombine).not.toHaveBeenCalled();

    publishShown({ canApply: false, blockedReason: 'The shown run belongs to another ingestion.' });
    setSlope('2');
    await act(async () => { vi.advanceTimersByTime(1_000); await Promise.resolve(); });
    expect(recombineMocks.recombine).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain('Changes apply to the shown combined result immediately');
  });

  it('says why an edit is not being applied, and offers the way out', async () => {
    const recall = vi.fn();
    render();
    publishShown({
      canApply: false,
      blockedReason: 'The shown run was solved from an ingestion this session has not loaded.',
      recall,
    });
    expect(host.textContent).toContain('this session has not loaded');

    const button = [...host.querySelectorAll<HTMLButtonElement>('button.crossover-recall')][0];
    expect(button).not.toBeUndefined();
    act(() => button.click());
    expect(recall).toHaveBeenCalledTimes(1);
  });

  it('says a run combined from other channels is not this crossover', async () => {
    render();
    publishShown({ combine: { ...shownCombineOf(), members: ['drive-lf', 'drive-hf'] } as CombineMetadata });
    expect(host.textContent).toContain('drive-lf + drive-hf');
    expect(host.textContent).not.toContain('Changes apply to the shown combined result immediately');
  });

  it('never touches a run combined from different channels', async () => {
    render();
    publishShown({ combine: { ...shownCombineOf(), members: ['drive-lf', 'drive-hf'] } as CombineMetadata });
    setSlope('2');
    await act(async () => { vi.advanceTimersByTime(1_000); await Promise.resolve(); });
    expect(recombineMocks.recombine).not.toHaveBeenCalled();
  });

  it('renders the Advanced editor inline and remembers the chosen view durably', () => {
    render();
    // Basic is the default face: pair fields present, per-channel editor not.
    expect(host.querySelector('[aria-label="MF → HF slope"]')).not.toBeNull();
    expect(host.querySelector('.crossover-advanced-inline')).toBeNull();

    const setView = (label: string) => act(() => {
      [...host.querySelectorAll<HTMLButtonElement>('[aria-label="Crossover view"] button')]
        .find((button) => button.textContent === label)!.click();
    });
    setView('Advanced');
    const panel = host.querySelector('.crossover-advanced-inline');
    expect(panel).not.toBeNull();
    // Inline in the section, not a body portal.
    expect(host.contains(panel)).toBe(true);
    expect(panel!.textContent).toContain('Relink pairs');
    // The basic fields give way rather than stacking under the editor.
    expect(host.querySelector('[aria-label="MF → HF slope"]')).toBeNull();
    // The choice lands in the durable namespace, one value per namespace.
    expect(localStorage.getItem(SETTINGS_NAMESPACES.crossoverView)).toBe('advanced');

    // A fresh mount reads it back: the view survives a browser restart.
    act(() => root.unmount());
    host.remove();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    render();
    expect(host.querySelector('.crossover-advanced-inline')).not.toBeNull();

    setView('Basic');
    expect(localStorage.getItem(SETTINGS_NAMESPACES.crossoverView)).toBe('basic');
    expect(host.querySelector('.crossover-advanced-inline')).toBeNull();
    expect(host.querySelector('[aria-label="MF → HF slope"]')).not.toBeNull();
  });


  const setView = (label: string) => act(() => {
    [...host.querySelectorAll<HTMLButtonElement>('[aria-label="Crossover view"] button')]
      .find((button) => button.textContent === label)!.click();
  });

  /** A run whose MF is level-matched down and delayed, and whose drivers have
   * room left: the numbers the rail is supposed to read back. */
  const resolvedRun = () => shownCombineOf({
    'drive-mf': {
      gain_auto_db: -12.11, delay_auto_ms: -0.53,
      gain_max_db: 8.4, max_limit: 'xmax', max_limit_hz: 120,
    },
    'drive-hf': {
      gain_auto_db: 21.48, delay_auto_ms: 0,
      gain_max_db: 14.2, max_limit: 'power', max_limit_hz: 2_500,
    },
  });

  it('reads back the gain and delay auto chose, without opening Advanced', () => {
    render();
    publishShown({ combine: resolvedRun() });

    // Basic states both, per member, beside the mode they belong to.
    expect(host.textContent).toContain('MF -12.11 dB');
    expect(host.textContent).toContain('HF +21.48 dB');
    expect(host.textContent).toContain('MF -0.53 ms');
    expect(host.textContent).toContain('HF +0.00 ms');
  });

  it('offers Max, and says what stops the channel and where', () => {
    render();
    publishShown({ combine: resolvedRun() });
    setView('Advanced');

    const modes = host.querySelectorAll('[aria-label="Gain mode"]');
    expect(modes.length).toBe(2);
    const buttons = [...modes[0].querySelectorAll('button')].map((button) => button.textContent);
    expect(buttons).toEqual(['Auto', 'Manual', 'Max']);
    // The ceiling is stated whether or not the gain is set to it.
    expect(host.textContent).toContain('Xmax at 120 Hz');
    expect(host.textContent).toContain('max +8.40 dB');

    act(() => {
      [...modes[0].querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Max')!.click();
    });
    const spec = useCadReturnStore.getState().combineSpec!;
    expect(spec.channels['drive-mf'].gain).toEqual({ mode: 'max' });
  });

  it('reads a rating as the two numbers it came from, not just a percentage', () => {
    render();
    publishShown({
      combine: {
        ...resolvedRun(),
        max_output: {
          frequencies: [1_000],
          members: {
            'drive-mf': {
              spl_max_db: [120], headroom_db: [8], limit: ['power'],
              excursion_fraction: 0.31, xmax_mm: 7,
              power_fraction: 0.31, rated_power_w: 400,
              voltage_fraction: null, max_voltage_v: null,
            },
          },
          combined: { spl_max_db: [120], headroom_db: [8], limit: ['power'], limiting_member: ['drive-mf'] },
          unlimited_members: [],
        },
      } as unknown as CombineMetadata,
    });
    setView('Advanced');
    expect(host.textContent).toContain('Xmax 31% (2.17 of 7 mm)');
    expect(host.textContent).toContain('power 31% (124 of 400 W)');
    // An unset amplifier limit is not reported as 0 V of nothing.
    expect(host.textContent).not.toContain('amplifier');
  });

  it('offers no Max on a channel with no ceiling to reach', () => {
    render();
    publishShown({ combine: shownCombineOf() });
    setView('Advanced');
    const max = [...host.querySelectorAll<HTMLButtonElement>('[aria-label="Gain mode"] button')]
      .filter((button) => button.textContent === 'Max');
    expect(max.length).toBe(2);
    expect(max.every((button) => button.disabled)).toBe(true);
  });

  it('reads the gain in volts and watts, and remembers which', () => {
    useCadReturnStore.setState({
      driveVoltageV: 2.83,
      channelDrivers: {
        'drive-mf': { fields: { re_ohm: 6.4, z_nom_ohm: 8 }, preset: null },
        'drive-hf': { fields: { re_ohm: 6.4, z_nom_ohm: 8 }, preset: null },
      },
    });
    render();
    publishShown({ combine: resolvedRun() });
    setView('Advanced');

    const selectUnit = (value: string) => act(() => {
      const select = host.querySelector<HTMLSelectElement>('.crossover-unit-select')!;
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    // Auto is -12.11 dB on 2.83 V, which is 0.7 V into 8 ohm: 0.061 W.
    selectUnit('v');
    expect(host.textContent).toContain('0.702 V');
    selectUnit('w');
    expect(host.textContent).toContain('0.0616 W');
    expect(localStorage.getItem(SETTINGS_NAMESPACES.crossoverGainUnit)).toBe('w');

    // A fresh mount reads it back, like the view does.
    act(() => root.unmount());
    host.remove();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    render();
    publishShown({ combine: resolvedRun() });
    expect(host.querySelector<HTMLSelectElement>('.crossover-unit-select')!.value).toBe('w');
  });

  it('falls back to dB when the run cannot form the chosen unit', () => {
    // No driver on the channels, so there is no impedance to divide by.
    render();
    publishShown({ combine: resolvedRun() });
    setView('Advanced');
    act(() => {
      const select = host.querySelector<HTMLSelectElement>('.crossover-unit-select')!;
      select.value = 'w';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    // The control still reads, in the unit it can form, rather than dashing out.
    expect(host.textContent).toContain('-12.11 dB');
  });

  it('shows a failed recombine instead of pretending it applied', async () => {
    recombineMocks.recombine.mockRejectedValue(new Error('solved band refused the crossover'));
    render();
    publishShown();
    setSlope('2');
    await act(async () => { vi.advanceTimersByTime(450); await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain('solved band refused the crossover');
    expect(onApplied).not.toHaveBeenCalled();
  });
});
