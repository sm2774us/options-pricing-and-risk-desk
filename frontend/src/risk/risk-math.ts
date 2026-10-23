/** Position book and portfolio Greeks, computed from the live frame. */

import {cellOffset, Field, Frame} from '../protocol/codec';

export const CONTRACT_MULTIPLIER = 100;

export type Side = 'C' | 'P';

export interface Position {
  expiryIdx: number;
  strikeIdx: number;
  side: Side;
  /** Signed contracts: positive long, negative short. */
  qty: number;
  /** Theoretical price per share when the position was opened. */
  entryPx: number;
}

export interface RiskSummary {
  delta: number;
  dollarDelta: number;
  gamma1pct: number;
  vega: number;
  theta: number;
  pnl: number;
  scenarios: {movePct: number; pnl: number}[];
}

type PositionId = Pick<Position, 'expiryIdx' | 'strikeIdx' | 'side'>;

export const positionKey = (p: PositionId): string =>
  `${p.expiryIdx}:${p.strikeIdx}:${p.side}`;

/** Theoretical price per share of the given option in `frame`. */
export function optionPx(
  frame: Frame,
  e: number,
  k: number,
  side: Side
): number {
  const o = cellOffset(frame, e, k);
  return frame.data[o + (side === 'C' ? Field.CallPx : Field.PutPx)];
}

/**
 * Adds `qty` contracts, merging into an existing position (weighted-average
 * entry when increasing) and removing it when flat.
 */
export function addPosition(
  book: Map<string, Position>,
  frame: Frame,
  e: number,
  k: number,
  side: Side,
  qty: number
): void {
  const px = optionPx(frame, e, k, side);
  const key = positionKey({expiryIdx: e, strikeIdx: k, side});
  const prev = book.get(key);
  if (!prev) {
    book.set(key, {expiryIdx: e, strikeIdx: k, side, qty, entryPx: px});
    return;
  }
  const total = prev.qty + qty;
  if (total === 0) {
    book.delete(key);
    return;
  }
  const increasing = Math.sign(qty) === Math.sign(prev.qty);
  const entryPx = increasing
    ? (prev.entryPx * Math.abs(prev.qty) + px * Math.abs(qty)) / Math.abs(total)
    : prev.entryPx;
  book.set(key, {...prev, qty: total, entryPx});
}

/**
 * Aggregates Greeks (100x multiplier) and a delta-gamma scenario ladder.
 * `gamma1pct` is the change in delta (in shares) for a 1% spot move.
 */
export function computeRisk(
  frame: Frame,
  book: Iterable<Position>
): RiskSummary {
  let delta = 0;
  let gammaSum = 0;
  let vega = 0;
  let theta = 0;
  let pnl = 0;
  for (const p of book) {
    const o = cellOffset(frame, p.expiryIdx, p.strikeIdx);
    const m = p.qty * CONTRACT_MULTIPLIER;
    const call = p.side === 'C';
    delta += m * frame.data[o + (call ? Field.CallDelta : Field.PutDelta)];
    gammaSum += m * frame.data[o + Field.Gamma];
    vega += m * frame.data[o + Field.Vega];
    theta += m * frame.data[o + (call ? Field.CallTheta : Field.PutTheta)];
    pnl += m * (optionPx(frame, p.expiryIdx, p.strikeIdx, p.side) - p.entryPx);
  }
  const s = frame.spot;
  const scenarios = [-5, -2, 2, 5].map(movePct => {
    const dS = (s * movePct) / 100;
    return {movePct, pnl: delta * dS + 0.5 * gammaSum * dS * dS};
  });
  return {
    delta,
    dollarDelta: delta * s,
    gamma1pct: gammaSum * 0.01 * s,
    vega,
    theta,
    pnl,
    scenarios,
  };
}
