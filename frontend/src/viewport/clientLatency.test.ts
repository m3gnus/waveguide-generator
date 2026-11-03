import { describe, expect, it } from 'vitest';
import { ClientLatencyClock, formatClientLatency } from './clientLatency';

describe('client latency', () => {
  it('formats finite request-to-render timings and caps the chrome readout', () => {
    expect(formatClientLatency(null)).toBe('—');
    expect(formatClientLatency(12.34)).toBe('12.3 ms');
    expect(formatClientLatency(-4)).toBe('0.0 ms');
    expect(formatClientLatency(999)).toBe('999.0 ms');
    expect(formatClientLatency(1_000)).toBe('1.0 s');
  });

  it('isolates repeated server epochs and sequences by connection generation', () => {
    const clock = new ClientLatencyClock();
    clock.beginConnection(1);
    clock.recordRequest(1, 2, 120);
    expect(clock.requestStartedAt(1, 2)).toBe(120);
    clock.beginConnection(2);
    clock.recordRequest(1, 2, 120);
    expect(clock.requestStartedAt(1, 2)).toBeNull();
    expect(clock.requestStartedAt(2, 2)).toBeNull();
    clock.recordRequest(2, 2, 1_000);
    expect(clock.requestStartedAt(2, 2)).toBe(1_000);
  });

  it('prunes timestamps when frames never arrive', () => {
    const clock = new ClientLatencyClock(2);
    clock.beginConnection(3);
    for (let seq = 1; seq <= 3; seq += 1) clock.recordRequest(3, seq, 100 + seq);
    expect(clock.requestStartedAt(3, 1)).toBeNull();
    expect(clock.requestStartedAt(3, 2)).toBe(102);
    expect(clock.requestStartedAt(3, 3)).toBe(103);
  });
});
