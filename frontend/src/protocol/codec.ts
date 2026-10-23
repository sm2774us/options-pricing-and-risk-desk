/**
 * Binary surface-frame codec. Mirrors backend/src/optdesk/protocol.py; both
 * are verified against shared/golden/frame.bin.
 */

export const FRAME_MAGIC = 0x4f504458;
export const FRAME_VERSION = 1;
export const HEADER_BYTES = 64;

/** Column indices inside the per-option field vector. */
export enum Field {
  CallPx = 0,
  PutPx = 1,
  CallDelta = 2,
  PutDelta = 3,
  Gamma = 4,
  Vega = 5,
  CallTheta = 6,
  PutTheta = 7,
  Iv = 8,
}

/** A decoded surface frame. Timestamps are epoch milliseconds (server clock). */
export interface Frame {
  symbolId: number;
  seq: number;
  spot: number;
  nExpiries: number;
  nStrikes: number;
  nFields: number;
  tickMs: number;
  pricerRecvMs: number;
  pricerSendMs: number;
  gatewayRecvMs: number;
  /** Years to each expiry. */
  expiries: Float32Array;
  strikes: Float32Array;
  /** Row-major [expiry][strike][field]. */
  data: Float32Array;
}

/** Converts a u64 nanosecond BigInt to fractional milliseconds. */
export function nsToMs(ns: bigint): number {
  return Number(ns / 1000n) / 1000;
}

/**
 * Decodes one binary frame.
 *
 * @param buf Raw WebSocket message.
 * @return The decoded frame (arrays are views into `buf`).
 * @throws Error if the magic, version or length is invalid.
 */
export function decodeFrame(buf: ArrayBuffer): Frame {
  if (buf.byteLength < HEADER_BYTES) throw new Error('frame too short');
  const v = new DataView(buf);
  if (
    v.getUint32(0, true) !== FRAME_MAGIC ||
    v.getUint16(4, true) !== FRAME_VERSION
  ) {
    throw new Error('bad frame magic or version');
  }
  const nExpiries = v.getUint16(16, true);
  const nStrikes = v.getUint16(18, true);
  const nFields = v.getUint16(20, true);
  const expected =
    HEADER_BYTES + 4 * (nExpiries + nStrikes + nExpiries * nStrikes * nFields);
  if (buf.byteLength !== expected) throw new Error('bad frame length');
  let offset = HEADER_BYTES;
  const expiries = new Float32Array(buf, offset, nExpiries);
  offset += 4 * nExpiries;
  const strikes = new Float32Array(buf, offset, nStrikes);
  offset += 4 * nStrikes;
  const data = new Float32Array(buf, offset, nExpiries * nStrikes * nFields);
  return {
    symbolId: v.getUint16(6, true),
    seq: Number(v.getBigUint64(8, true)),
    nExpiries,
    nStrikes,
    nFields,
    spot: v.getFloat64(24, true),
    tickMs: nsToMs(v.getBigUint64(32, true)),
    pricerRecvMs: nsToMs(v.getBigUint64(40, true)),
    pricerSendMs: nsToMs(v.getBigUint64(48, true)),
    gatewayRecvMs: nsToMs(v.getBigUint64(56, true)),
    expiries,
    strikes,
    data,
  };
}

/** Index of the first field of option (expiry `e`, strike `k`). */
export function cellOffset(frame: Frame, e: number, k: number): number {
  return (e * frame.nStrikes + k) * frame.nFields;
}
