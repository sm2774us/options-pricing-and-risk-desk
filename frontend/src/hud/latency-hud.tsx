import {useEffect, useRef} from 'react';

import {EngineSnapshot, STAGE_NAMES} from '../engine/desk-engine';

const STAGE_COLORS = ['#4aa3ff', '#b36bff', '#ffb23f', '#2ecc71', '#ff5d8f'];
const RATES = [10, 30, 100, 500, 1500];

const ms = (v: number) => (v < 10 ? v.toFixed(2) : v.toFixed(1));

function Sparkline({series}: {series: number[]}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current!;
    const ctx = c.getContext('2d')!;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth;
    const h = c.clientHeight;
    c.width = w * dpr;
    c.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (series.length < 2) return;
    const max = Math.max(...series, 1);
    ctx.strokeStyle = '#4aa3ff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    series.forEach((v, i) => {
      const x = (i / (series.length - 1)) * w;
      const y = h - (v / max) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = '#6f7c8a';
    ctx.font = '10px monospace';
    ctx.fillText(`${max.toFixed(1)} ms`, 4, 10);
  }, [series]);
  return <canvas ref={ref} className="spark" />;
}

interface Props {
  snap: EngineSnapshot;
  onRate(hz: number): void;
}

/** Tick-in to pixel-painted latency heads-up display. */
export function LatencyHud({snap, onRate}: Props) {
  const stageTotal = snap.stages.reduce((a, s) => a + s.p50, 0) || 1;
  const gw = snap.gateway;
  const compute = snap.pricerComputeUs;
  return (
    <section className="panel hud" data-testid="hud">
      <h2>
        Latency HUD <small>tick-in → pixel-painted</small>
      </h2>
      <div className="hud-main">
        <div>
          <div className="big" data-testid="hud-p50">
            {ms(snap.e2e.p50)}
          </div>
          <div className="lbl">p50 ms</div>
        </div>
        <div>
          <div className="big mid">{ms(snap.e2e.p99)}</div>
          <div className="lbl">p99 ms</div>
        </div>
        <div>
          <div className="big mid">{ms(snap.lastE2eMs)}</div>
          <div className="lbl">last ms</div>
        </div>
      </div>
      <Sparkline series={snap.series} />
      <div className="stagebar">
        {snap.stages.map((s, i) => (
          <div
            key={i}
            style={{
              width: `${(s.p50 / stageTotal) * 100}%`,
              background: STAGE_COLORS[i],
            }}
            title={`${STAGE_NAMES[i]} ${ms(s.p50)} ms`}
          />
        ))}
      </div>
      <div className="legend">
        {snap.stages.map((s, i) => (
          <span key={i}>
            <i style={{background: STAGE_COLORS[i]}} />
            {STAGE_NAMES[i]} <b>{ms(s.p50)}</b>
          </span>
        ))}
      </div>
      <div className="tiles">
        <div>
          <b>{snap.paintFps.toFixed(0)}</b>
          <span>paint fps</span>
        </div>
        <div>
          <b>{snap.framesPerSec.toFixed(0)}</b>
          <span>frames/s in</span>
        </div>
        <div>
          <b>{snap.clientConflated}</b>
          <span>client conflated</span>
        </div>
        <div>
          <b>{gw?.conflated ?? 0}</b>
          <span>server conflated</span>
        </div>
        <div>
          <b>{snap.seqGaps}</b>
          <span>tick seq gaps</span>
        </div>
        <div>
          <b>
            {compute
              ? `${compute.p50.toFixed(0)}/${compute.p99.toFixed(0)}`
              : '–'}
          </b>
          <span>compute µs p50/p99</span>
        </div>
        <div>
          <b>{snap.rttMs.toFixed(2)}</b>
          <span>ws rtt ms</span>
        </div>
        <div>
          <b>{snap.offsetMs.toFixed(2)}</b>
          <span>clock offset ms</span>
        </div>
      </div>
      <div className="rates">
        <span>Feed rate / symbol</span>
        {RATES.map(hz => (
          <button
            key={hz}
            className={snap.feedHz === hz ? 'on' : ''}
            onClick={() => onRate(hz)}
          >
            {hz} Hz
          </button>
        ))}
      </div>
    </section>
  );
}
