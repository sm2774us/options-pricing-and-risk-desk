import {describe, expect, it} from 'vitest';

import {Frame} from '../protocol/codec';
import {addPosition, computeRisk, Position} from './risk-math';

/** One expiry x two strikes frame with hand-picked values. */
function makeFrame(callPx = 5, spot = 100): Frame {
  const data = new Float32Array(2 * 9);
  // [callPx, putPx, callDelta, putDelta, gamma, vega, callTheta, putTheta, iv]
  data.set([callPx, 4, 0.5, -0.5, 0.02, 0.1, -0.05, -0.04, 0.2], 0);
  data.set([2, 7, 0.3, -0.7, 0.01, 0.08, -0.03, -0.02, 0.22], 9);
  return {
    symbolId: 0,
    seq: 1,
    spot,
    nExpiries: 1,
    nStrikes: 2,
    nFields: 9,
    tickMs: 0,
    pricerRecvMs: 0,
    pricerSendMs: 0,
    gatewayRecvMs: 0,
    expiries: new Float32Array([0.1]),
    strikes: new Float32Array([100, 110]),
    data,
  };
}

describe('position book', () => {
  it('averages entry on adds, keeps it on reductions, removes when flat', () => {
    const book = new Map<string, Position>();
    addPosition(book, makeFrame(5), 0, 0, 'C', 1);
    addPosition(book, makeFrame(7), 0, 0, 'C', 1);
    const pos = [...book.values()][0];
    expect(pos.qty).toBe(2);
    expect(pos.entryPx).toBeCloseTo(6);
    addPosition(book, makeFrame(9), 0, 0, 'C', -1);
    expect([...book.values()][0]).toMatchObject({qty: 1, entryPx: 6});
    addPosition(book, makeFrame(9), 0, 0, 'C', -1);
    expect(book.size).toBe(0);
  });
});

describe('computeRisk', () => {
  it('aggregates Greeks with the 100x multiplier and signs', () => {
    const book = new Map<string, Position>();
    addPosition(book, makeFrame(5), 0, 0, 'C', 2); // long 2 calls
    addPosition(book, makeFrame(5), 0, 1, 'P', -1); // short 1 put
    const r = computeRisk(makeFrame(6), book.values());
    expect(r.delta).toBeCloseTo(200 * 0.5 + -100 * -0.7);
    expect(r.vega).toBeCloseTo(200 * 0.1 - 100 * 0.08);
    expect(r.theta).toBeCloseTo(200 * -0.05 - 100 * -0.02);
    expect(r.gamma1pct).toBeCloseTo((200 * 0.02 - 100 * 0.01) * 0.01 * 100);
    expect(r.pnl).toBeCloseTo(200 * (6 - 5) - 100 * (7 - 7));
    expect(r.dollarDelta).toBeCloseTo(r.delta * 100);
  });

  it('builds a delta-gamma scenario ladder', () => {
    const book = new Map<string, Position>();
    addPosition(book, makeFrame(), 0, 0, 'C', 1);
    const r = computeRisk(makeFrame(), book.values());
    const up = r.scenarios.find(s => s.movePct === 5)!;
    expect(up.pnl).toBeCloseTo(50 * 5 + 0.5 * 2 * 25);
    expect(computeRisk(makeFrame(), []).delta).toBe(0);
  });
});
