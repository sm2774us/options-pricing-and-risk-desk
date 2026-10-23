# Solution Explanation

## 1. Goal

Reproduce, end to end, the workload described in the brief: *a React UI that
cannot lag, cannot drop a tick and cannot lie, sitting on distributed,
event-driven Python services* — and make the one number that matters
(tick-in → pixel-painted) visible and honest.

## 2. Data flow

1. **Feed** (`feed.py`) simulates correlated-free GBM with soft mean reversion
   for 5 underlyings and publishes a 26-byte binary tick per symbol per
   interval on topic `optdesk.ticks`, stamped with the wall-clock publish time.
   The rate is a runtime control (`optdesk.control`) driven from the UI.
2. **Pricer** (`pricer.py`) subscribes to ticks, **conflates per symbol** (only
   the newest pending tick is priced), applies a per-symbol output cap
   (`frame_hz_cap`) and reprices the entire (expiry × strike) grid with one
   vectorised numpy/scipy pass (~0.2 ms for 915 options on a laptop-class
   core). It emits a **surface frame** on `optdesk.surface`.
3. **Gateway** (`gateway.py`) subscribes to frames, patches in its receive
   timestamp, and offers the frame to every client subscribed to that symbol.
4. **Browser** decodes the frame zero-copy (`Float32Array` views), keeps only
   the latest, draws it to canvas in `requestAnimationFrame`, acks, and
   records latency one vsync later.

## 3. Wire format

Two fixed little-endian binary layouts (`protocol.py` ↔ `codec.ts`):

* Tick: `u16 symbol, u64 seq, f64 spot, u64 ts_ns` (26 B).
* Frame: 64-byte header (magic, version, symbol, seq, dims, spot and **four
  u64 ns timestamps**: tick, pricer-recv, pricer-send, gateway-recv) followed by
  `f32 expiries[], f32 strikes[], f32 data[expiry][strike][9 fields]`.

Why binary: no JSON parse, no per-object allocation, ~33 KB per full surface
frame, and the browser reads it as typed-array views. Both sides are verified
against `shared/golden/frame.bin`, so a format drift fails CI in either
language. Timestamps cross the JS boundary as BigInt → µs → ms to avoid the
2^53 precision cliff.

## 4. Backpressure and "cannot lag"

Lag is eliminated structurally by never queueing stale state:

* **Pricer**: latest-wins per symbol + rate cap ⇒ at overload it prices fewer
  ticks rather than falling behind (visible as `conflated`).
* **Bus**: in-memory subscribers drop their oldest message when full; Redis
  pub/sub is fire-and-forget. Slow consumers never block publishers.
* **Gateway → browser** (the "real backpressure"): each client has a **one-slot
  mailbox** (newer frame overwrites older) and a **credit window**
  (`ack_window`, default 4). The browser acks *after painting*, so a slow
  client throttles only itself; memory per client is O(1 frame). If acks stop
  (hidden tab, wedged client) the window resets after 2 s instead of
  deadlocking. Control messages (pong/stats) always go first.
* **Browser**: if several frames arrive within one animation frame, only the
  newest is drawn; skipped ones are counted (`client conflated`).

"Cannot drop a tick" is interpreted as *never silently*: every tick carries a
sequence number; the HUD counts sequence gaps between frames the user actually
saw, and the server reports conflation counts per layer. State shown is always
the newest consistent surface – conflation drops intermediate states, never the
latest one.

"Cannot lie": every number on screen comes from a single frame (spot, prices,
Greeks computed from that spot at that time), and the spot shown is the one
the surface was priced from. Put-call parity of delivered data is asserted in
the integration test.

## 5. Latency measurement (the HUD)

Marks, in server wall-clock milliseconds:
`tick → pricer recv → pricer send → gateway recv → browser recv → painted`.

* Server-side marks come from the frame header (ns resolution).
* Browser marks use `performance.timeOrigin + performance.now()` and are
  shifted into server time using an **NTP-style offset** estimated from
  ping/pong every 2 s (`clock-sync.ts`); the minimum-RTT sample wins because it
  is least polluted by queueing.
* **Painted** = the start timestamp of the *next* `requestAnimationFrame`
  callback after the one that drew the frame: the previous frame has been
  presented by then. This includes decode, draw and vsync wait – deliberately
  conservative.
* Stage p50s feed the stacked bar; e2e p50/p99/last and a sparkline use a
  2048-sample ring buffer.

Caveat (also in the README): feed/pricer/gateway hosts must be time-synced
(NTP/PTP) for cross-host stage numbers to be meaningful; same-host and
browser↔gateway are corrected automatically.

Reference numbers from the build sandbox (single process, loopback, through the
Vite proxy, 30 Hz × 5 symbols, no browser): pricer compute p50 ≈ 0.20 ms /
p99 ≈ 0.57 ms; tick → client-socket p50 ≈ 3.6 ms, p99 ≈ 5.8 ms. Browser
paint adds roughly one display refresh interval. Your numbers will differ.

## 6. Pricing model

`pricing.black_scholes` is a pure vectorised function (inputs broadcast) that
returns price, delta, gamma, vega (per vol point) and theta (per day) for calls
and puts under Black-Scholes-Merton with continuous dividend yield. `PricingGrid`
adds a parametric smile — ATM level + term slope·√T + skew·ln(K/F) +
curvature·ln²(K/F), floored at 3% — evaluated over the whole grid, with time to
expiry decaying in real time. Tests check parity, a textbook reference value,
and every Greek against central finite differences.

## 7. The grid ("holds 60 fps")

`grid-renderer.ts` draws on a single `<canvas>`:

* **Virtualisation**: a scroll container with a sticky canvas; only rows in
  `[scrollTop/ROW_H, +viewport]` are painted, so cost is independent of surface
  size.
* **No React on the hot path**: React renders chrome only. A single rAF loop
  takes the latest frame from `DeskEngine`, diffs it against the previous one
  (typed-array compare) to seed per-cell flash intensity, and paints.
* Flash intensities decay exponentially (τ = 160 ms) and are the only per-cell
  state; the loop idles when nothing changed and nothing is fading.
* Hit testing maps clicks to (expiry, strike, side) arithmetically.

## 8. Distribution and scaling

* **Roles are separate processes** (`feed`, `pricer`, `gateway`) coupled only
  through the bus; `all` mode runs them in one event loop for development.
* **Pricers shard by symbol** (`OPTDESK_PRICER_SYMBOLS`); add shards to scale
  compute. **Gateways are stateless**; add replicas behind nginx/ingress (the
  provided nginx config re-resolves DNS so `--scale gateway=N` round-robins).
* Kubernetes manifests include probes, resource limits, an HPA on gateway CPU
  and Prometheus annotations.
* The bus is an interface: swapping Redis for NATS/Kafka/Aeron means
  implementing `EventBus` (3 methods) – no service code changes.

## 9. Testing strategy

Pyramid: pure-function unit tests (pricing, protocol, stats, risk maths) →
service tests with an in-memory bus and injected clocks (deterministic
conflation/rate-cap assertions) → integration test driving the whole pipeline
over a real WebSocket via Starlette's test client → Playwright E2E against the
built UI and the real backend. The golden-frame fixture ties the Python and
TypeScript implementations together. CI (`.github/workflows/ci.yml`) runs
lint, unit, integration, build, E2E and `docker compose build`.

## 10. Style

Python follows the Google Python Style Guide (4-space indent, 80 columns,
type annotations, Google-format docstrings with Args/Returns/Raises, module
docstrings, imports of modules, `ruff` enforcing formatting/imports).
TypeScript follows the Google TypeScript Style Guide as automated by `gts`
(2-space indent, semicolons, single quotes, no `any`, explicit types on public
APIs, `UpperCamelCase`/`lowerCamelCase`/`CONSTANT_CASE`, no `_` prefixes,
named imports) with prettier configured to the same options.

## 11. Honest limitations and next steps

* The feed is simulated; plug a real market-data gateway in as another
  publisher of `optdesk.ticks`.
* Redis pub/sub adds a network hop and is at-most-once by design; for
  sub-millisecond targets use shared-memory/Aeron, a C++ pricer (the wire format
  is language-neutral) and SBE/FlatBuffers.
* Frames are full snapshots; delta-encoding the surface would cut bandwidth at
  extreme rates.
* The smile is parametric and Black-Scholes is European-style; American
  exercise, discrete dividends and calibrated surfaces (SVI/SSVI) are natural
  extensions behind `PricingGrid`.
* Positions/risk live in the browser for the demo; a production desk would
  keep a server-side position service and entitlements.
* In the build sandbox I ran: backend unit + integration tests, frontend
  typecheck/unit tests/production build, and a live end-to-end probe over
  WebSocket through the Vite proxy. The Playwright E2E suite, Docker images and
  Kubernetes manifests are written to the same contracts but could not be
  executed there (no browser download / Docker daemon) – run `make e2e` and
  `docker compose up --build` on your machine to confirm.
