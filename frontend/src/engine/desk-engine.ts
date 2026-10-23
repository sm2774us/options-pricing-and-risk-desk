/**
 * Non-React core of the desk: owns the socket, the latest-frame slot,
 * latency accounting and ack bookkeeping. The UI polls `snapshot()`.
 *
 * Latency pipeline (all stages in ms, measured on the server clock):
 *   tick -> pricer recv -> pricer send -> gateway recv -> browser recv
 *   -> painted (start of the *next* animation frame, i.e. after vsync).
 */

import {DeskSocket, epochNowMs, Status} from '../net/desk-socket';
import {Frame} from '../protocol/codec';
import {GatewayStats, ServerMessage, SymbolInfo} from '../protocol/messages';
import {ClockSync} from './clock-sync';
import {EMPTY_STATS, RollingStats, StatSnapshot} from './rolling-stats';

export const STAGE_NAMES = [
  'feed→pricer',
  'compute',
  'pricer→gw',
  'gw→browser',
  'decode+paint',
] as const;

/** A frame that has been drawn but whose pixels are not yet on screen. */
export interface PendingPaint {
  frame: Frame;
  recvEpochMs: number;
}

export interface EngineSnapshot {
  status: Status;
  symbols: SymbolInfo[];
  symbol: string;
  e2e: StatSnapshot;
  stages: StatSnapshot[];
  series: number[];
  lastE2eMs: number;
  paintFps: number;
  framesPerSec: number;
  clientConflated: number;
  offsetMs: number;
  rttMs: number;
  gateway: GatewayStats | null;
  pricerComputeUs: StatSnapshot | null;
  feedHz: number;
  seqGaps: number;
}

export class DeskEngine {
  readonly socket: DeskSocket;
  private status: Status = 'connecting';
  private symbols: SymbolInfo[] = [];
  private symbol = 'SPY';
  private latest: Frame | null = null;
  private latestRecv = 0;
  private framesSinceAck = 0;
  private readonly clock = new ClockSync();
  private readonly e2e = new RollingStats(2048);
  private readonly stages = STAGE_NAMES.map(() => new RollingStats(1024));
  private lastE2e = 0;
  private clientConflated = 0;
  private seqGaps = 0;
  private lastSeq = -1;
  private paints = 0;
  private recvs = 0;
  private rateWindowStart = performance.now();
  private paintFps = 0;
  private framesPerSec = 0;
  private gateway: GatewayStats | null = null;
  private pricerComputeUs: StatSnapshot | null = null;
  private feedHz = 0;

  constructor(
    url: string,
    private readonly now: () => number = epochNowMs
  ) {
    this.socket = new DeskSocket(url, {
      onStatus: s => this.onStatus(s),
      onMessage: m => this.onMessage(m),
      onFrame: (f, recv) => this.onFrame(f, recv),
    });
  }

  start(): void {
    this.socket.start();
  }

  stop(): void {
    this.socket.stop();
  }

  setSymbol(name: string): void {
    this.symbol = name;
    this.latest = null;
    this.lastSeq = -1;
    this.socket.send({t: 'sub', symbol: name});
  }

  setFeedRate(hz: number): void {
    this.socket.send({t: 'rate', hz});
  }

  /** Returns the newest undrawn frame (older ones were conflated). */
  takeFrame(): PendingPaint | null {
    if (!this.latest) return null;
    const out = {frame: this.latest, recvEpochMs: this.latestRecv};
    this.latest = null;
    return out;
  }

  /** Credits the server for every frame received since the last ack. */
  ackConsumed(): void {
    if (this.framesSinceAck === 0) return;
    this.socket.send({t: 'ack', n: this.framesSinceAck});
    this.framesSinceAck = 0;
  }

  /** Records the latency of a frame that has now reached the screen. */
  recordPaint(p: PendingPaint, paintedEpochMs: number): void {
    const off = this.clock.offsetMs;
    const f = p.frame;
    const marks = [
      f.tickMs,
      f.pricerRecvMs,
      f.pricerSendMs,
      f.gatewayRecvMs,
      p.recvEpochMs + off,
      paintedEpochMs + off,
    ];
    for (let i = 0; i < STAGE_NAMES.length; i++) {
      this.stages[i].push(Math.max(0, marks[i + 1] - marks[i]));
    }
    this.lastE2e = Math.max(0, marks[5] - marks[0]);
    this.e2e.push(this.lastE2e);
    this.paints++;
  }

  snapshot(): EngineSnapshot {
    const t = performance.now();
    const dt = (t - this.rateWindowStart) / 1000;
    if (dt >= 1) {
      this.paintFps = this.paints / dt;
      this.framesPerSec = this.recvs / dt;
      this.paints = 0;
      this.recvs = 0;
      this.rateWindowStart = t;
    }
    return {
      status: this.status,
      symbols: this.symbols,
      symbol: this.symbol,
      e2e: this.e2e.snapshot(),
      stages: this.stages.map(s => s.snapshot()),
      series: this.e2e.recent(160),
      lastE2eMs: this.lastE2e,
      paintFps: this.paintFps,
      framesPerSec: this.framesPerSec,
      clientConflated: this.clientConflated,
      offsetMs: this.clock.offsetMs,
      rttMs: this.clock.rttMs,
      gateway: this.gateway,
      pricerComputeUs: this.pricerComputeUs,
      feedHz: this.feedHz,
      seqGaps: this.seqGaps,
    };
  }

  /** Handles a decoded frame (public for tests). */
  onFrame(frame: Frame, recvEpochMs: number): void {
    this.recvs++;
    this.framesSinceAck++;
    if (this.latest) this.clientConflated++;
    if (this.lastSeq >= 0 && frame.seq > this.lastSeq + 1) {
      this.seqGaps += frame.seq - this.lastSeq - 1;
    }
    this.lastSeq = frame.seq;
    this.latest = frame;
    this.latestRecv = recvEpochMs;
  }

  /** Handles a control message (public for tests). */
  onMessage(msg: ServerMessage): void {
    switch (msg.t) {
      case 'hello':
        this.symbols = msg.symbols;
        this.socket.send({t: 'sub', symbol: this.symbol});
        break;
      case 'pong':
        this.clock.add({t0: msg.t0, srvMs: msg.srv_ms, t1: this.now()});
        break;
      case 'stats': {
        this.gateway = msg.gateway;
        const c = msg.services['pricer']?.['compute_us'] as
          Record<string, number> | undefined;
        this.pricerComputeUs = c ? {...EMPTY_STATS, ...c} : null;
        this.feedHz = Number(msg.services['feed']?.['tick_hz'] ?? 0);
        break;
      }
      default:
        break;
    }
  }

  private onStatus(status: Status): void {
    this.status = status;
    if (status === 'closed') this.framesSinceAck = 0;
  }
}
