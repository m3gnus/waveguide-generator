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

/** The resolved payload of a run combined as LR4 at 1 kHz, auto everything. */
function shownCombineOf(): CombineMetadata {
  const wire = toWire(expandLegacy(['drive-mf', 'drive-hf'], [1_000]));
  return {
    members: ['drive-mf', 'drive-hf'],
    reference: 'drive-hf',
    crossovers_hz: [1_000],
    channels: Object.fromEntries(['drive-mf', 'drive-hf'].map((member) => [member, {
      ...wire.channels[member],
      gain_db: 0, gain_mode: 'auto', gain_auto_db: 0,
      delay_ms: 0, delay_mode: 'auto', delay_auto_ms: 0,
      inverted: false, invert_mode: 'auto',
    }])),
  } as unknown as CombineMetadata;
}

describe('live recombine from the rail', () => {
  let host: HTMLDivElement;
  let root: Root;
  let onApplied: ReturnType<typeof vi.fn<(jobId: string, updated: JobResults) => void>>;

  const publishShown = (overrides: Partial<ShownCombine> = {}) => act(() => latestCombine.publish({
    jobId: 'job-1', channelId: 'combined', combine: shownCombineOf(), canApply: true, onApplied, ...overrides,
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
    expect(host.textContent).toContain('Changes apply to the shown combined result immediately.');

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

    publishShown({ canApply: false });
    setSlope('2');
    await act(async () => { vi.advanceTimersByTime(1_000); await Promise.resolve(); });
    expect(recombineMocks.recombine).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain('Changes apply to the shown combined result immediately.');
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
