import { afterEach, describe, expect, it } from 'vitest';
import type { ResultPayload } from '../results/types';
import { COMBINED_VIEW, resolveResultView, resultViewStore } from './resultView';

function run(channels: Record<string, ResultPayload>, order?: string[]): ResultPayload {
  return { frequencies: [], channel_order: order ?? Object.keys(channels), channels };
}

const driver = (role: string): ResultPayload => ({ frequencies: [], metadata: { role } });
const sum = (members: string[]): ResultPayload => ({
  frequencies: [],
  metadata: { combine: { members, crossovers_hz: [1_000] } },
});

describe('result view store', () => {
  afterEach(() => resultViewStore.resetForTests());

  it('starts on the combined view and remembers what was chosen', () => {
    expect(resultViewStore.getSnapshot()).toBe(COMBINED_VIEW);
    resultViewStore.setView('drive-mf');
    expect(resultViewStore.getSnapshot()).toBe('drive-mf');
    expect(sessionStorage.getItem('wg2.resultView.v1')).toBe('drive-mf');
  });

  it('notifies subscribers once per distinct choice', () => {
    let notifications = 0;
    const stop = resultViewStore.subscribe(() => { notifications += 1; });
    resultViewStore.setView('drive-hf');
    resultViewStore.setView('drive-hf');
    stop();
    expect(notifications).toBe(1);
  });
});

describe('resolveResultView', () => {
  const combined = run({
    'drive-mf': driver('MF'),
    'drive-hf': driver('HF'),
    combined: sum(['drive-mf', 'drive-hf']),
  });

  it('resolves the combined sentinel by its combine record, not by its id', () => {
    const renamed = run({ 'drive-mf': driver('MF'), lr4: sum(['drive-mf', 'drive-hf']) });
    expect(resolveResultView(renamed, COMBINED_VIEW)).toBe('lr4');
    expect(resolveResultView(combined, COMBINED_VIEW)).toBe('combined');
  });

  it('keeps a chosen driver the run has', () => {
    expect(resolveResultView(combined, 'drive-hf')).toBe('drive-hf');
  });

  it('substitutes the sum, then the first channel, for a run without the view', () => {
    expect(resolveResultView(combined, 'drive-lf')).toBe('combined');
    const twoWay = run({ 'drive-mf': driver('MF'), 'drive-hf': driver('HF') }, ['drive-hf', 'drive-mf']);
    expect(resolveResultView(twoWay, 'drive-lf')).toBe('drive-hf');
  });

  it('has no channel to resolve for a parametric run', () => {
    expect(resolveResultView({ frequencies: [] }, COMBINED_VIEW)).toBeNull();
    expect(resolveResultView({ frequencies: [], channels: {} }, 'drive-mf')).toBeNull();
    expect(resolveResultView(undefined, COMBINED_VIEW)).toBeNull();
  });
});
