import {describe, expect, it} from 'vitest';

import {ClockSync, sampleOffset} from './clock-sync';
import {RollingStats} from './rolling-stats';

describe('ClockSync', () => {
  it('recovers a known offset using the lowest-RTT sample', () => {
    const sync = new ClockSync();
    const trueOffset = 5000; // server is 5 s ahead
    // Noisy sample: asymmetric delay biases the estimate.
    sync.add({t0: 1000, srvMs: 1000 + trueOffset + 40, t1: 1100});
    // Clean sample: symmetric 1 ms legs.
    sync.add({t0: 2000, srvMs: 2001 + trueOffset, t1: 2002});
    expect(sync.rttMs).toBe(2);
    expect(sync.offsetMs).toBeCloseTo(trueOffset, 6);
    expect(sampleOffset({t0: 0, srvMs: 10, t1: 4})).toBe(8);
  });

  it('evicts old samples and defaults to zero', () => {
    const sync = new ClockSync(2);
    expect(sync.offsetMs).toBe(0);
    sync.add({t0: 0, srvMs: 0, t1: 1});
    sync.add({t0: 0, srvMs: 0, t1: 5});
    sync.add({t0: 0, srvMs: 0, t1: 9});
    expect(sync.rttMs).toBe(5);
  });
});

describe('RollingStats', () => {
  it('computes percentiles and wraps around', () => {
    const s = new RollingStats(100);
    for (let i = 1; i <= 150; i++) s.push(i);
    const snap = s.snapshot();
    expect(snap.count).toBe(100);
    expect(snap.max).toBe(150);
    expect(snap.p50).toBe(101);
    expect(s.recent(3)).toEqual([148, 149, 150]);
  });

  it('is safe when empty', () => {
    expect(new RollingStats().snapshot().count).toBe(0);
    expect(new RollingStats().recent(5)).toEqual([]);
  });
});
