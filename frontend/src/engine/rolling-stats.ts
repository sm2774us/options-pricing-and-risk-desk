/** Fixed-capacity ring buffer with percentile snapshots. */

export interface StatSnapshot {
  count: number;
  mean: number;
  p50: number;
  p90: number;
  p99: number;
  max: number;
}

export const EMPTY_STATS: StatSnapshot = {
  count: 0,
  mean: 0,
  p50: 0,
  p90: 0,
  p99: 0,
  max: 0,
};

export class RollingStats {
  private readonly buf: Float64Array;
  private next = 0;
  private size = 0;

  constructor(capacity = 1024) {
    this.buf = new Float64Array(capacity);
  }

  push(value: number): void {
    this.buf[this.next] = value;
    this.next = (this.next + 1) % this.buf.length;
    if (this.size < this.buf.length) this.size++;
  }

  /** Most recent `n` samples, oldest first. */
  recent(n: number): number[] {
    const out: number[] = [];
    const count = Math.min(n, this.size);
    for (let i = count; i > 0; i--) {
      out.push(this.buf[(this.next - i + this.buf.length) % this.buf.length]);
    }
    return out;
  }

  snapshot(): StatSnapshot {
    if (this.size === 0) return EMPTY_STATS;
    const sorted = Float64Array.from(this.buf.subarray(0, this.size)).sort();
    const at = (q: number) =>
      sorted[Math.min(this.size - 1, Math.floor(q * this.size))];
    let sum = 0;
    for (const v of sorted) sum += v;
    return {
      count: this.size,
      mean: sum / this.size,
      p50: at(0.5),
      p90: at(0.9),
      p99: at(0.99),
      max: sorted[this.size - 1],
    };
  }
}
