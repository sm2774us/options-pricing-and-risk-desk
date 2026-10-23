/**
 * NTP-style clock offset estimation between browser and server.
 *
 * offset = serverTime - clientTime, so `clientEpoch + offset` is server time.
 * The sample with the smallest round trip is the least biased by queueing.
 */

export interface ClockSample {
  /** Client epoch ms when the ping left. */
  t0: number;
  /** Server epoch ms when the pong was built. */
  srvMs: number;
  /** Client epoch ms when the pong arrived. */
  t1: number;
}

export function sampleRtt(s: ClockSample): number {
  return s.t1 - s.t0;
}

export function sampleOffset(s: ClockSample): number {
  return s.srvMs - (s.t0 + s.t1) / 2;
}

export class ClockSync {
  private samples: ClockSample[] = [];

  constructor(private readonly maxSamples = 16) {}

  add(sample: ClockSample): void {
    this.samples.push(sample);
    if (this.samples.length > this.maxSamples) this.samples.shift();
  }

  private best(): ClockSample | undefined {
    let best: ClockSample | undefined;
    for (const s of this.samples) {
      if (!best || sampleRtt(s) < sampleRtt(best)) best = s;
    }
    return best;
  }

  /** Server-minus-client offset in ms (0 until the first sample). */
  get offsetMs(): number {
    const b = this.best();
    return b ? sampleOffset(b) : 0;
  }

  /** Smallest observed round trip in ms. */
  get rttMs(): number {
    const b = this.best();
    return b ? sampleRtt(b) : 0;
  }
}
