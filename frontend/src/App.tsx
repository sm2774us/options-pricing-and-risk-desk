import {useCallback, useEffect, useMemo, useRef, useState} from 'react';

import {DeskEngine, EngineSnapshot} from './engine/desk-engine';
import {ChainGrid} from './grid/chain-grid';
import {LatencyHud} from './hud/latency-hud';
import {Frame} from './protocol/codec';
import {computeRisk, Position, RiskSummary} from './risk/risk-math';
import {RiskPanel} from './risk/risk-panel';

const DEFAULT_SYMBOLS = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA'];

function gatewayUrl(): string {
  const override = new URLSearchParams(window.location.search).get('ws');
  if (override) return override;
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}/ws`;
}

export function App() {
  const engine = useMemo(() => new DeskEngine(gatewayUrl()), []);
  const frameRef = useRef<Frame | null>(null);
  const books = useRef(new Map<string, Map<string, Position>>());
  const [symbol, setSymbol] = useState('SPY');
  const [snap, setSnap] = useState<EngineSnapshot>(engine.snapshot());
  const [risk, setRisk] = useState<RiskSummary | null>(null);
  const [spot, setSpot] = useState({px: 0, dir: 0});
  const [, setBookVersion] = useState(0);

  const bookFor = (name: string) => {
    let book = books.current.get(name);
    if (!book) books.current.set(name, (book = new Map()));
    return book;
  };
  const book = bookFor(symbol);

  useEffect(() => {
    engine.start();
    return () => engine.stop();
  }, [engine]);

  useEffect(() => {
    engine.setSymbol(symbol);
    // Re-subscribe after every reconnect (hello triggers it as well).
  }, [engine, symbol]);

  useEffect(() => {
    let lastPx = 0;
    const id = window.setInterval(() => {
      setSnap(engine.snapshot());
      const f = frameRef.current;
      if (!f) return;
      setRisk(computeRisk(f, bookFor(engine.snapshot().symbol).values()));
      setSpot({px: f.spot, dir: Math.sign(f.spot - lastPx)});
      lastPx = f.spot;
    }, 250);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  const onBookChange = useCallback(() => setBookVersion(v => v + 1), []);
  const frame = frameRef.current;
  const labels = frame
    ? Array.from(frame.expiries, t => `${Math.round(t * 365)}d`)
    : [];
  const symbols = snap.symbols.length
    ? snap.symbols.map(s => s.name)
    : DEFAULT_SYMBOLS;

  return (
    <div className="app">
      <header>
        <h1>Options Pricing &amp; Risk Desk</h1>
        <nav data-testid="symbols">
          {symbols.map(s => (
            <button
              key={s}
              className={s === symbol ? 'tab on' : 'tab'}
              onClick={() => setSymbol(s)}
            >
              {s}
            </button>
          ))}
        </nav>
        <div
          className={`spot ${spot.dir > 0 ? 'up' : spot.dir < 0 ? 'down' : ''}`}
          data-testid="spot"
        >
          {spot.px ? spot.px.toFixed(2) : '—'}
        </div>
        <div className={`status ${snap.status}`} data-testid="status">
          <i /> {snap.status}
        </div>
      </header>
      <main>
        <ChainGrid
          engine={engine}
          frameRef={frameRef}
          book={book}
          symbol={symbol}
          onBookChange={onBookChange}
        />
        <aside>
          <LatencyHud snap={snap} onRate={hz => engine.setFeedRate(hz)} />
          <RiskPanel
            risk={risk}
            positions={[...book.values()]}
            labels={labels}
            strikes={frame?.strikes ?? null}
            onClear={() => {
              book.clear();
              onBookChange();
            }}
          />
        </aside>
      </main>
    </div>
  );
}
