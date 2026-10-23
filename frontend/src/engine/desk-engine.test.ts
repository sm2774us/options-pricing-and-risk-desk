import {beforeAll, describe, expect, it} from 'vitest';

import {Frame} from '../protocol/codec';
import {DeskEngine} from './desk-engine';

beforeAll(() => {
  // Minimal browser shims; the engine's socket is never started in tests.
  Object.assign(globalThis, {window: globalThis});
});

const frame = (seq: number, tickMs = 1000): Frame => ({
  symbolId: 0,
  seq,
  spot: 100,
  nExpiries: 1,
  nStrikes: 1,
  nFields: 9,
  tickMs,
  pricerRecvMs: tickMs + 1,
  pricerSendMs: tickMs + 3,
  gatewayRecvMs: tickMs + 4,
  expiries: new Float32Array(1),
  strikes: new Float32Array(1),
  data: new Float32Array(9),
});

describe('DeskEngine', () => {
  it('conflates frames, counts sequence gaps and decomposes latency', () => {
    const engine = new DeskEngine('ws://unused', () => 0);
    engine.onMessage({t: 'pong', t0: 0, srv_ms: 0}); // offset 0
    engine.onFrame(frame(1), 1006);
    engine.onFrame(frame(2), 1006);
    engine.onFrame(frame(5), 1006);
    const got = engine.takeFrame()!;
    expect(got.frame.seq).toBe(5);
    expect(engine.takeFrame()).toBeNull();
    const snap = engine.snapshot();
    expect(snap.clientConflated).toBe(2);
    expect(snap.seqGaps).toBe(2);

    engine.recordPaint(got, 1016.5);
    const after = engine.snapshot();
    expect(after.lastE2eMs).toBeCloseTo(16.5);
    expect(after.stages.map(s => Number(s.p50.toFixed(1)))).toEqual([
      1, 2, 1, 2, 10.5,
    ]);
    expect(after.e2e.count).toBe(1);
  });

  it('applies the clock offset to client timestamps', () => {
    const engine = new DeskEngine('ws://unused', () => 10);
    engine.onMessage({t: 'pong', t0: 8, srv_ms: 509}); // offset = 500
    engine.onFrame(frame(1, 1000), 506); // client 506 -> server 1006
    engine.recordPaint(engine.takeFrame()!, 516);
    expect(engine.snapshot().lastE2eMs).toBeCloseTo(16);
  });
});
