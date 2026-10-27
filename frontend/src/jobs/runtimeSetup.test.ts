import { describe, expect, it } from 'vitest';
import { expectNoJargon } from '../testing/expectNoJargon';
import { runtimeSetupText } from './runtimeSetup';

const cpu = { name: 'beat-cpu', reason: 'BEAT cpu provisioning failed: offline. Use Retry to prepare it again.' };

describe('BEAT runtime setup words', () => {
  it.each([
    [{ runtime_state: 'unprepared', runtime_preparable: true }, 'picker', 'Not set up: select to set up (about 5 min)'],
    [{ runtime_state: 'unprepared', runtime_preparable: true }, 'status', 'Not set up (about 5 min)'],
    [{ runtime_state: 'preparing', runtime_detail: 'compiling, about 4 min' }, 'picker', 'Setting up… (compiling, about 4 min)'],
    [{ runtime_state: 'preparing', runtime_detail: 'Downloading julia-1.12.7-win64.zip: 120 / 280 MB' }, 'status',
      'Setting up… (Downloading julia-1.12.7-win64.zip: 120 / 280 MB)'],
    [{ runtime_state: 'preparing' }, 'picker', 'Setting up…'],
    [{ runtime_state: 'ready' }, 'picker', 'ready'],
    [{ runtime_state: 'failed', runtime_detail: 'offline' }, 'picker', 'Setup failed: offline'],
  ] as const)('%j in the %s reads %j', (state, where, text) => {
    expect(runtimeSetupText({ ...cpu, ...state }, where)).toBe(text);
    expectNoJargon(text);
  });

  it('falls back to the reason for a failure from an older server', () => {
    expect(runtimeSetupText({ ...cpu, runtime_state: 'failed' })).toBe(`Setup failed: ${cpu.reason}`);
  });

  it('gives a GPU setup no CPU time estimate', () => {
    expect(runtimeSetupText({ name: 'beat-cuda', reason: null, runtime_state: 'unprepared', runtime_preparable: true }))
      .toBe('Not set up: select to set up');
  });

  it('says nothing for engines without a runtime, or one this installation cannot set up', () => {
    expect(runtimeSetupText({ name: 'bempp', reason: null, runtime_state: 'unprepared', runtime_preparable: true })).toBeNull();
    expect(runtimeSetupText({ ...cpu, runtime_state: 'unprepared', runtime_preparable: false })).toBeNull();
    expect(runtimeSetupText({ ...cpu, runtime_state: 'unavailable' })).toBeNull();
  });
});
