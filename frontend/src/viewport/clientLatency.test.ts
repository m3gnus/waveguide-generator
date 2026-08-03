import { describe, expect, it } from 'vitest';
import { ClientLatencyClock, formatClientLatency } from './clientLatency';

describe('client latency', () => {
  it('formats finite request-to-paint timings and caps the chrome readout', () => {
    expect(formatClientLatency(null)).toBe('—');
    expect(formatClientLatency(12.34)).toBe('12.3');
    expect(formatClientLatency(-4)).toBe('0.0');
    expect(formatClientLatency(999)).toBe('999.0');
    expect(formatClientLatency(1_000)).toBe('>999');
  });

  it('ignores request timestamps from the socket epoch before a reconnect', () => {
    const clock = new ClientLatencyClock();
    clock.beginEpoch(7, 12, 100);
    clock.recordRequest(7, 13, 'fine', 120);
    expect(clock.requestStartedAt({ epoch: 7, designRevision: 13, lod: 'fine' })).toBe(120);

    clock.beginEpoch(8, 13, 1_000);
    clock.recordRequest(7, 13, 'fine', 120);
    clock.recordRequest(8, 14, 'fine', 999);
    expect(clock.requestStartedAt({ epoch: 7, designRevision: 13, lod: 'fine' })).toBeNull();
    expect(clock.requestStartedAt({ epoch: 8, designRevision: 13, lod: 'fine' })).toBe(1_000);
    expect(clock.requestStartedAt({ epoch: 8, designRevision: 14, lod: 'fine' })).toBeNull();
  });
});
