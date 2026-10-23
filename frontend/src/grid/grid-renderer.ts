/**
 * Canvas renderer for the virtualised option chain.
 *
 * Only the rows intersecting the viewport are painted, so cost is O(visible
 * cells) regardless of how many expiries/strikes the surface contains. Cell
 * "flash" intensities decay exponentially and are the only per-cell state.
 */

import {cellOffset, Field, Frame} from '../protocol/codec';
import {Position, positionKey, Side} from '../risk/risk-math';

export const ROW_H = 22;
export const HEADER_H = 44;
const FLASH_TAU_MS = 160;
const FONT = '12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

type Group = 'call' | 'put' | 'mid';
type Kind = 'expiry' | 'strike' | 'field';

interface Column {
  label: string;
  group: Group;
  weight: number;
  kind: Kind;
  field?: Field;
  digits: number;
  scale?: number;
}

export const COLUMNS: readonly Column[] = [
  {label: 'Expiry', group: 'mid', weight: 0.9, kind: 'expiry', digits: 0},
  {
    label: 'Δ',
    group: 'call',
    weight: 1,
    kind: 'field',
    field: Field.CallDelta,
    digits: 3,
  },
  {
    label: 'Θ/day',
    group: 'call',
    weight: 1,
    kind: 'field',
    field: Field.CallTheta,
    digits: 3,
  },
  {
    label: 'Vega',
    group: 'call',
    weight: 1,
    kind: 'field',
    field: Field.Vega,
    digits: 3,
  },
  {
    label: 'Price',
    group: 'call',
    weight: 1.1,
    kind: 'field',
    field: Field.CallPx,
    digits: 2,
  },
  {label: 'Strike', group: 'mid', weight: 1.1, kind: 'strike', digits: 2},
  {
    label: 'IV %',
    group: 'mid',
    weight: 0.9,
    kind: 'field',
    field: Field.Iv,
    digits: 1,
    scale: 100,
  },
  {
    label: 'Γ',
    group: 'mid',
    weight: 1,
    kind: 'field',
    field: Field.Gamma,
    digits: 4,
  },
  {
    label: 'Price',
    group: 'put',
    weight: 1.1,
    kind: 'field',
    field: Field.PutPx,
    digits: 2,
  },
  {
    label: 'Vega',
    group: 'put',
    weight: 1,
    kind: 'field',
    field: Field.Vega,
    digits: 3,
  },
  {
    label: 'Θ/day',
    group: 'put',
    weight: 1,
    kind: 'field',
    field: Field.PutTheta,
    digits: 3,
  },
  {
    label: 'Δ',
    group: 'put',
    weight: 1,
    kind: 'field',
    field: Field.PutDelta,
    digits: 3,
  },
];

const PX_CALL_COL = 4;
const PX_PUT_COL = 8;

const COLORS = {
  bg: '#0b0f14',
  band: '#0e141b',
  header: '#121a23',
  grid: '#1b2530',
  text: '#d6dde6',
  dim: '#6f7c8a',
  itmCall: 'rgba(46,160,110,0.10)',
  itmPut: 'rgba(220,90,90,0.10)',
  atm: 'rgba(240,185,60,0.16)',
  up: '46,204,113',
  down: '255,84,84',
  long: '#2ecc71',
  short: '#ff5454',
  callHead: '#2ea06e',
  putHead: '#dc5a5a',
};

export class GridRenderer {
  private readonly ctx: CanvasRenderingContext2D;
  private width = 0;
  private height = 0;
  private colX: number[] = [];
  private colW: number[] = [];
  private flash = new Float32Array(0);
  private prev = new Float32Array(0);
  private shapeKey = '';
  private lastMs = 0;
  /** True while any cell is still fading, so the caller keeps redrawing. */
  animating = false;

  constructor(private readonly canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext('2d', {alpha: false});
    if (!ctx) throw new Error('2D canvas unavailable');
    this.ctx = ctx;
  }

  /** Resizes the backing store for the device pixel ratio. */
  resize(cssWidth: number, cssHeight: number, dpr: number): void {
    this.width = cssWidth;
    this.height = cssHeight;
    this.canvas.width = Math.max(1, Math.floor(cssWidth * dpr));
    this.canvas.height = Math.max(1, Math.floor(cssHeight * dpr));
    this.canvas.style.width = `${cssWidth}px`;
    this.canvas.style.height = `${cssHeight}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const total = COLUMNS.reduce((a, c) => a + c.weight, 0);
    let x = 0;
    this.colX = [];
    this.colW = [];
    for (const c of COLUMNS) {
      const w = (c.weight / total) * cssWidth;
      this.colX.push(x);
      this.colW.push(w);
      x += w;
    }
  }

  /** Diffs a new frame against the previous one to seed cell flashes. */
  ingest(frame: Frame): void {
    const key = `${frame.symbolId}:${frame.data.length}`;
    const rows = frame.nExpiries * frame.nStrikes;
    if (key !== this.shapeKey) {
      this.shapeKey = key;
      this.flash = new Float32Array(rows * COLUMNS.length);
      this.prev = new Float32Array(frame.data);
      return;
    }
    const nCols = COLUMNS.length;
    const data = frame.data;
    for (let r = 0; r < rows; r++) {
      const o = r * frame.nFields;
      for (let c = 0; c < nCols; c++) {
        const f = COLUMNS[c].field;
        if (f === undefined) continue;
        const d = data[o + f] - this.prev[o + f];
        if (d > 1e-7) this.flash[r * nCols + c] = 1;
        else if (d < -1e-7) this.flash[r * nCols + c] = -1;
      }
    }
    this.prev.set(data);
  }

  /** Paints the visible window of the chain plus the sticky header. */
  draw(
    frame: Frame,
    scrollTop: number,
    nowMs: number,
    book: ReadonlyMap<string, Position>
  ): void {
    const ctx = this.ctx;
    const nCols = COLUMNS.length;
    const nK = frame.nStrikes;
    const rows = frame.nExpiries * nK;
    const dt = this.lastMs ? Math.min(100, nowMs - this.lastMs) : 16;
    this.lastMs = nowMs;
    const decay = Math.exp(-dt / FLASH_TAU_MS);
    let maxFlash = 0;
    for (let i = 0; i < this.flash.length; i++) {
      const v = this.flash[i] * decay;
      this.flash[i] = Math.abs(v) < 0.02 ? 0 : v;
      if (Math.abs(this.flash[i]) > maxFlash)
        maxFlash = Math.abs(this.flash[i]);
    }
    this.animating = maxFlash > 0;

    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, this.width, this.height);
    ctx.font = FONT;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'right';

    const step = nK > 1 ? frame.strikes[1] - frame.strikes[0] : 1;
    const atmK = Math.min(
      nK - 1,
      Math.max(0, Math.round((frame.spot - frame.strikes[0]) / step))
    );
    const first = Math.max(0, Math.floor(scrollTop / ROW_H));
    const last = Math.min(
      rows,
      first + Math.ceil((this.height - HEADER_H) / ROW_H) + 2
    );
    for (let r = first; r < last; r++) {
      const e = Math.floor(r / nK);
      const k = r - e * nK;
      const y = HEADER_H + r * ROW_H - scrollTop;
      const strike = frame.strikes[k];
      if (e % 2 === 1) {
        ctx.fillStyle = COLORS.band;
        ctx.fillRect(0, y, this.width, ROW_H);
      }
      if (k === atmK) {
        ctx.fillStyle = COLORS.atm;
        ctx.fillRect(0, y, this.width, ROW_H);
      }
      if (strike < frame.spot) {
        ctx.fillStyle = COLORS.itmCall;
        ctx.fillRect(this.colX[1], y, this.colX[5] - this.colX[1], ROW_H);
      } else {
        ctx.fillStyle = COLORS.itmPut;
        ctx.fillRect(
          this.colX[PX_PUT_COL],
          y,
          this.width - this.colX[PX_PUT_COL],
          ROW_H
        );
      }
      if (k === 0) {
        ctx.fillStyle = COLORS.grid;
        ctx.fillRect(0, y, this.width, 1);
      }
      const o = cellOffset(frame, e, k);
      for (let c = 0; c < nCols; c++) {
        const col = COLUMNS[c];
        const x = this.colX[c];
        const w = this.colW[c];
        const fl = this.flash[r * nCols + c];
        if (fl !== 0) {
          ctx.fillStyle = `rgba(${fl > 0 ? COLORS.up : COLORS.down},${Math.abs(fl) * 0.45})`;
          ctx.fillRect(x, y, w, ROW_H);
        }
        let text: string;
        if (col.kind === 'expiry') {
          text = `${Math.round(frame.expiries[e] * 365)}d`;
          ctx.fillStyle = COLORS.dim;
        } else if (col.kind === 'strike') {
          text = strike.toFixed(col.digits);
          ctx.fillStyle = k === atmK ? '#f0b93c' : COLORS.text;
        } else {
          text = (
            frame.data[o + (col.field as number)] * (col.scale ?? 1)
          ).toFixed(col.digits);
          ctx.fillStyle = COLORS.text;
        }
        ctx.fillText(text, x + w - 8, y + ROW_H / 2);
      }
      this.drawPosition(ctx, book, e, k, 'C', PX_CALL_COL, y);
      this.drawPosition(ctx, book, e, k, 'P', PX_PUT_COL, y);
    }
    this.drawHeader();
  }

  private drawPosition(
    ctx: CanvasRenderingContext2D,
    book: ReadonlyMap<string, Position>,
    e: number,
    k: number,
    side: Side,
    col: number,
    y: number
  ): void {
    const pos = book.get(positionKey({expiryIdx: e, strikeIdx: k, side}));
    if (!pos) return;
    const x = this.colX[col];
    const color = pos.qty > 0 ? COLORS.long : COLORS.short;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x + 1, y + 1, this.colW[col] - 2, ROW_H - 2);
    ctx.fillStyle = color;
    ctx.textAlign = 'left';
    ctx.fillText(`${pos.qty > 0 ? '+' : ''}${pos.qty}`, x + 5, y + ROW_H / 2);
    ctx.textAlign = 'right';
  }

  private drawHeader(): void {
    const ctx = this.ctx;
    ctx.fillStyle = COLORS.header;
    ctx.fillRect(0, 0, this.width, HEADER_H);
    ctx.fillStyle = COLORS.grid;
    ctx.fillRect(0, HEADER_H - 1, this.width, 1);
    ctx.textAlign = 'center';
    ctx.font = `bold ${FONT}`;
    ctx.fillStyle = COLORS.callHead;
    ctx.fillText('CALLS', (this.colX[1] + this.colX[5]) / 2, 12);
    ctx.fillStyle = COLORS.putHead;
    ctx.fillText('PUTS', (this.colX[PX_PUT_COL] + this.width) / 2, 12);
    ctx.fillStyle = COLORS.dim;
    COLUMNS.forEach((col, c) => {
      ctx.fillText(col.label, this.colX[c] + this.colW[c] / 2, 31);
    });
    ctx.font = FONT;
    ctx.textAlign = 'right';
  }

  /**
   * Maps a canvas-local click to an option.
   *
   * @return The option, or null if the click is outside any price column.
   */
  hitTest(
    x: number,
    y: number,
    scrollTop: number,
    frame: Frame
  ): {e: number; k: number; side: Side} | null {
    if (y < HEADER_H) return null;
    const r = Math.floor((y - HEADER_H + scrollTop) / ROW_H);
    if (r < 0 || r >= frame.nExpiries * frame.nStrikes) return null;
    let c = this.colX.length - 1;
    while (c > 0 && x < this.colX[c]) c--;
    let side: Side | null = null;
    if (c >= 1 && c <= PX_CALL_COL) side = 'C';
    else if (c >= PX_PUT_COL) side = 'P';
    if (!side) return null;
    const e = Math.floor(r / frame.nStrikes);
    return {e, k: r - e * frame.nStrikes, side};
  }
}
