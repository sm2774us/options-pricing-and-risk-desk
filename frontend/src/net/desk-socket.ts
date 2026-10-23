/** Resilient WebSocket client for the gateway (reconnect, ping, acks). */

import {decodeFrame, Frame} from '../protocol/codec';
import {ClientMessage, ServerMessage} from '../protocol/messages';

export type Status = 'connecting' | 'open' | 'closed';

export interface SocketHandlers {
  onStatus(status: Status): void;
  onMessage(message: ServerMessage): void;
  /** Called per frame with the arrival time in client epoch ms. */
  onFrame(frame: Frame, recvEpochMs: number): void;
}

/** Client epoch milliseconds with sub-millisecond resolution. */
export function epochNowMs(): number {
  return performance.timeOrigin + performance.now();
}

export class DeskSocket {
  private ws: WebSocket | null = null;
  private pingTimer = 0;
  private retryTimer = 0;
  private retryMs = 250;
  private stopped = false;

  constructor(
    private readonly url: string,
    private readonly handlers: SocketHandlers
  ) {}

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    window.clearInterval(this.pingTimer);
    window.clearTimeout(this.retryTimer);
    this.ws?.close();
  }

  send(message: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  private connect(): void {
    this.handlers.onStatus('connecting');
    const ws = new WebSocket(this.url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.onopen = () => {
      this.retryMs = 250;
      this.handlers.onStatus('open');
      this.ping();
      window.clearInterval(this.pingTimer);
      this.pingTimer = window.setInterval(() => this.ping(), 2000);
    };
    ws.onmessage = ev => {
      if (typeof ev.data === 'string') {
        this.handlers.onMessage(JSON.parse(ev.data) as ServerMessage);
        return;
      }
      const recv = epochNowMs();
      try {
        this.handlers.onFrame(decodeFrame(ev.data as ArrayBuffer), recv);
      } catch (err) {
        console.warn('dropping undecodable frame', err);
      }
    };
    ws.onclose = () => {
      window.clearInterval(this.pingTimer);
      this.handlers.onStatus('closed');
      if (this.stopped) return;
      this.retryTimer = window.setTimeout(() => this.connect(), this.retryMs);
      this.retryMs = Math.min(this.retryMs * 2, 5000);
    };
  }

  private ping(): void {
    this.send({t: 'ping', t0: epochNowMs()});
  }
}
