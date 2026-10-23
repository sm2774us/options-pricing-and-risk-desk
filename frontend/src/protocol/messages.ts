/** JSON control-plane messages exchanged with the gateway. */

export interface SymbolInfo {
  id: number;
  name: string;
  spot: number;
}

export interface GatewayStats {
  clients: number;
  frames_in: number;
  fps_in: number;
  frames_sent: number;
  conflated: number;
  stall_resets: number;
}

export type ServerMessage =
  | {t: 'hello'; symbols: SymbolInfo[]; ack_window: number; server_ms: number}
  | {t: 'subscribed'; symbol: string}
  | {t: 'pong'; t0: number; srv_ms: number}
  | {
      t: 'stats';
      gateway: GatewayStats;
      services: Record<string, Record<string, unknown>>;
    };

export type ClientMessage =
  | {t: 'sub'; symbol: string}
  | {t: 'ack'; n: number}
  | {t: 'ping'; t0: number}
  | {t: 'rate'; hz: number};
