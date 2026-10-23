import {readFileSync} from 'node:fs';
import {describe, expect, it} from 'vitest';

import {cellOffset, decodeFrame, Field, HEADER_BYTES} from './codec';

const golden = (name: string) =>
  readFileSync(new URL(`../../../shared/golden/${name}`, import.meta.url));

function goldenBuffer(): ArrayBuffer {
  const b = golden('frame.bin');
  return b.buffer.slice(
    b.byteOffset,
    b.byteOffset + b.byteLength
  ) as ArrayBuffer;
}

describe('decodeFrame (cross-language golden frame)', () => {
  const expected = JSON.parse(golden('frame.json').toString());
  const frame = decodeFrame(goldenBuffer());

  it('decodes the header', () => {
    expect(frame.symbolId).toBe(expected.symbolId);
    expect(frame.seq).toBe(expected.seq);
    expect(frame.spot).toBe(expected.spot);
    expect([frame.nExpiries, frame.nStrikes, frame.nFields]).toEqual([2, 3, 9]);
  });

  it('converts u64 ns timestamps to ms without losing sub-ms precision', () => {
    const ms = (ns: string) => Number(BigInt(ns) / 1000n) / 1000;
    expect(frame.tickMs).toBe(ms(expected.tickNs));
    expect(frame.gatewayRecvMs - frame.tickMs).toBeCloseTo(0.5, 3);
  });

  it('decodes arrays and indexes cells', () => {
    expect(Array.from(frame.expiries)).toEqual(expected.expiries);
    expect(Array.from(frame.strikes)).toEqual(expected.strikes);
    expect(Array.from(frame.data)).toEqual(expected.data);
    const o = cellOffset(frame, 1, 2);
    expect(frame.data[o + Field.Iv]).toBe(expected.data[(1 * 3 + 2) * 9 + 8]);
  });

  it('rejects corrupt input', () => {
    const buf = goldenBuffer();
    expect(() => decodeFrame(buf.slice(0, HEADER_BYTES - 1))).toThrow();
    expect(() => decodeFrame(buf.slice(0, buf.byteLength - 4))).toThrow();
    const bad = buf.slice(0);
    new DataView(bad).setUint32(0, 0, true);
    expect(() => decodeFrame(bad)).toThrow();
  });
});
