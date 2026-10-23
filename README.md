# Options Pricing & Risk Desk

A real-time, distributed options desk: a live underlying feed streams onto an
event bus, a Python service reprices **every strike × expiry** (prices and
Greeks) on each tick, a gateway pushes binary frames to a React UI over
WebSockets with **credit-based backpressure**, and the chain is drawn in a
**virtualised canvas grid** that keeps repainting at display refresh rate.
A **latency HUD** shows *tick-in → pixel-painted* in milliseconds, broken down
per stage.

```
 ┌────────┐ ticks  ┌───────────┐ surface frames ┌──────────┐  WS (binary)  ┌─────────────────────┐
 │  feed  │───────▶│           │◀───────────────│  pricer  │  + acks       │ React + canvas grid │
 │ (GBM)  │        │ event bus │  (1..N shards) └──────────┘◀─────────────▶│ latency HUD, risk   │
 └────────┘        │ in-memory │ ───────────────▶┌──────────┐──────────────▶└─────────────────────┘
      ▲ control    │ or Redis  │  surface        │ gateway  │ (N replicas, stateless)
      └────────────┴───────────┘                 └──────────┘
```

| Layer | Tech |
|---|---|
| Pricer / feed / gateway | Python 3.11+, numpy + scipy (vectorised Black-Scholes), FastAPI + uvicorn |
| Bus | Redis pub/sub (distributed) or in-process (dev/tests), same `EventBus` interface |
| Frontend | React 18 + TypeScript + Vite, canvas renderer, no state library on the hot path |
| Quality | pytest (unit + integration), Vitest (unit), Playwright (E2E), ruff, prettier, GitHub Actions |
| Ops | Dockerfiles, docker-compose (sharded pricers, scalable gateways), Kubernetes manifests + HPA, Prometheus `/metrics` |

## Quick start

### Option A – no Docker, one process (fastest)

Requires Python ≥ 3.11 and Node ≥ 20.

```bash
# terminal 1: feed + pricer + gateway in one process on :8000
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=src python -m optdesk all

# terminal 2: UI on http://localhost:5173 (proxies /ws to :8000)
cd frontend
npm ci
npm run dev
```

(or `make setup && make dev`).

### Option B – Docker Compose, fully distributed

```bash
docker compose up --build            # UI at http://localhost:8080
docker compose up --build --scale gateway=3   # three gateway replicas
```

Containers: `redis`, `feed`, `pricer-a` (SPY, QQQ), `pricer-b` (AAPL, NVDA,
TSLA), `gateway`, `web` (nginx serving the UI and proxying `/ws`).

### Option C – Kubernetes

Build and push the two images, edit the image names, then
`kubectl apply -f deploy/k8s/options-desk.yaml`. The gateway Deployment has an
HPA (3–20 replicas) and Prometheus scrape annotations.

## Using the UI

* **Symbol tabs** switch the subscription (SPY, QQQ, AAPL, NVDA, TSLA).
* The chain shows 15 expiries × 61 strikes (915 rows, ~11k cells) but paints
  only the visible rows. Cells flash green/red when they tick up/down.
  "Jump to expiry" chips scroll the grid.
* **Click** a call/put price to buy 1 contract, **shift-click or right-click**
  to sell 1. The **Risk** panel shows live P&L, Δ, $Δ, Γ, vega, θ and a
  delta-gamma scenario ladder.
* **Latency HUD**: p50 / p99 / last tick→pixel ms, a sparkline, a per-stage
  stacked bar (feed→pricer, compute, pricer→gateway, gateway→browser,
  decode+paint), paint fps, conflation counters, WebSocket RTT and clock
  offset, pricer compute time. The **Feed rate** buttons (10 – 1500 Hz per
  symbol) stress the pipeline; watch conflation counters rise while latency
  stays bounded.

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `OPTDESK_BUS_URL` | `memory://` | `redis://host:6379/0` for multi-process mode |
| `OPTDESK_TICK_HZ` | `30` | ticks/s per symbol (changeable live from the UI) |
| `OPTDESK_FRAME_HZ_CAP` | `60` | max surface frames/s per symbol |
| `OPTDESK_N_STRIKES` | `61` | strikes per expiry |
| `OPTDESK_EXPIRY_DAYS` | 15 expiries 1d…365d | days to expiry at start-up |
| `OPTDESK_PRICER_SYMBOLS` | all | shard filter, e.g. `SPY,QQQ` |
| `OPTDESK_ACK_WINDOW` | `4` | un-acked frames allowed per client |
| `OPTDESK_SEED` | random | deterministic feed |

Roles: `python -m optdesk {feed|pricer|gateway|all}`. Only `all` works with
`memory://`; the others need Redis.

## Tests

```bash
make test           # backend pytest + frontend typecheck + Vitest
make lint           # ruff + prettier + tsc
make e2e            # Playwright (first: cd frontend && npx playwright install chromium)
```

| Suite | What it proves |
|---|---|
| `backend/tests/test_pricing.py` | put-call parity, textbook reference price, delta/gamma/vega/theta vs finite differences, smile, time decay |
| `test_protocol.py` | binary round-trip, corruption rejection, **golden frame** shared with the TypeScript decoder |
| `test_bus.py` | fan-out, drop-oldest, Redis bus (fakeredis) |
| `test_services.py` | feed determinism/control, pricer conflation + rate cap + sharding, pricer over bus |
| `test_gateway.py` | credit window, latest-wins conflation, stall recovery; **full pipeline integration over a real WebSocket** (ordering of timestamps, parity of delivered prices) |
| `frontend/src/**/*.test.ts` | codec vs golden frame, clock sync, percentiles, latency decomposition, position/risk maths |
| `frontend/e2e/desk.spec.ts` | real browser: connects, spot ticks, HUD shows latency, canvas painted, click opens position, symbol switch |

Regenerate the golden frame (only if the wire format changes):
`cd backend && PYTHONPATH=src python scripts/make_golden.py`.

## Repository layout

```
backend/src/optdesk/   config, pricing, protocol, bus, feed, pricer, gateway, main, metrics
backend/tests/         unit + integration tests
frontend/src/          protocol/ net/ engine/ grid/ hud/ risk/ + App
frontend/e2e/          Playwright specs
shared/golden/         cross-language wire-format fixtures
deploy/k8s/            Kubernetes manifests
docker-compose.yml     distributed local topology
```

## Observability

`GET /metrics` (Prometheus text) exposes clients, frames in/sent, conflation
and pricer compute p50/p99; `GET /healthz` for probes; the UI shows the same
numbers live.

## Troubleshooting

* **HUD latency looks negative/odd across machines** – browser and servers
  must share a reasonably synchronised clock (the HUD corrects browser↔gateway
  offset via ping/pong, but feed/pricer/gateway hosts need NTP/PTP).
* **`feed needs a shared bus`** – you launched a non-`all` role with
  `memory://`; set `OPTDESK_BUS_URL=redis://…`.
* **Blank grid** – check the status LED (top right) and that the backend is on
  `:8000`; for another host use `OPTDESK_GATEWAY=http://host:8000 npm run dev`
  or `?ws=ws://host:8000/ws` in the URL.
