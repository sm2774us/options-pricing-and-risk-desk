import {MutableRefObject, useEffect, useRef, useState} from 'react';

import {DeskEngine, PendingPaint} from '../engine/desk-engine';
import {Frame} from '../protocol/codec';
import {addPosition, Position} from '../risk/risk-math';
import {GridRenderer, HEADER_H, ROW_H} from './grid-renderer';

interface Props {
  engine: DeskEngine;
  /** Receives the most recently painted frame. */
  frameRef: MutableRefObject<Frame | null>;
  /** Position book of the active symbol. */
  book: Map<string, Position>;
  symbol: string;
  onBookChange(): void;
}

/**
 * Virtualised option chain. React renders only the chrome; the hot path is a
 * single requestAnimationFrame loop that draws straight to a canvas.
 */
export function ChainGrid({
  engine,
  frameRef,
  book,
  symbol,
  onBookChange,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<GridRenderer | null>(null);
  const dirtyRef = useRef(true);
  const bookRef = useRef(book);
  bookRef.current = book;
  const [shape, setShape] = useState({rows: 0, nK: 1, labels: [] as string[]});
  const needsCenter = useRef(true);

  useEffect(() => {
    needsCenter.current = true;
  }, [symbol]);

  useEffect(() => {
    const scroller = scrollRef.current!;
    const canvas = canvasRef.current!;
    const renderer = new GridRenderer(canvas);
    rendererRef.current = renderer;
    const fit = () => {
      renderer.resize(
        scroller.clientWidth,
        scroller.clientHeight,
        window.devicePixelRatio || 1
      );
      dirtyRef.current = true;
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(scroller);

    let pending: PendingPaint | null = null;
    let raf = 0;
    const loop = (ts: number) => {
      if (pending) {
        engine.recordPaint(pending, performance.timeOrigin + ts);
        pending = null;
      }
      const got = engine.takeFrame();
      if (got) {
        const f = got.frame;
        renderer.ingest(f);
        if (
          !frameRef.current ||
          frameRef.current.symbolId !== f.symbolId ||
          frameRef.current.data.length !== f.data.length
        ) {
          setShape({
            rows: f.nExpiries * f.nStrikes,
            nK: f.nStrikes,
            labels: Array.from(f.expiries, t => `${Math.round(t * 365)}d`),
          });
        }
        frameRef.current = f;
        dirtyRef.current = true;
      }
      const frame = frameRef.current;
      if (frame && needsCenter.current) {
        const atm = Math.round(frame.nStrikes / 2);
        scroller.scrollTop = Math.max(
          0,
          atm * ROW_H - scroller.clientHeight / 2 + HEADER_H
        );
        needsCenter.current = false;
      }
      if (frame && (dirtyRef.current || renderer.animating)) {
        renderer.draw(frame, scroller.scrollTop, ts, bookRef.current);
        dirtyRef.current = false;
        if (got) {
          engine.ackConsumed();
          pending = got;
        }
      } else if (got) {
        engine.ackConsumed();
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [engine, frameRef]);

  const click = (ev: React.MouseEvent<HTMLCanvasElement>, sign: number) => {
    ev.preventDefault();
    const frame = frameRef.current;
    const renderer = rendererRef.current;
    if (!frame || !renderer) return;
    const rect = ev.currentTarget.getBoundingClientRect();
    const hit = renderer.hitTest(
      ev.clientX - rect.left,
      ev.clientY - rect.top,
      scrollRef.current!.scrollTop,
      frame
    );
    if (!hit) return;
    addPosition(
      bookRef.current,
      frame,
      hit.e,
      hit.k,
      hit.side,
      sign * (ev.shiftKey ? -1 : 1)
    );
    dirtyRef.current = true;
    onBookChange();
  };

  const jump = (e: number) => {
    scrollRef.current!.scrollTop = e * shape.nK * ROW_H;
  };

  return (
    <div className="grid-wrap">
      <div className="expiry-chips" data-testid="expiry-chips">
        <span className="chip-label">Jump to expiry</span>
        {shape.labels.map((l, e) => (
          <button key={e} className="chip" onClick={() => jump(e)}>
            {l}
          </button>
        ))}
        <span className="hint">
          click = buy 1 · shift-click / right-click = sell 1
        </span>
      </div>
      <div
        className="grid-scroll"
        ref={scrollRef}
        onScroll={() => (dirtyRef.current = true)}
      >
        <div
          style={{height: HEADER_H + shape.rows * ROW_H, position: 'relative'}}
        >
          <canvas
            ref={canvasRef}
            data-testid="chain-canvas"
            style={{position: 'sticky', top: 0, display: 'block'}}
            onClick={ev => click(ev, 1)}
            onContextMenu={ev => click(ev, -1)}
          />
        </div>
      </div>
    </div>
  );
}
