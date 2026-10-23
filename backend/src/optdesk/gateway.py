"""WebSocket gateway: fans surface frames out to browsers with backpressure.

Backpressure model (per client):
  1. Each client has a one-frame *slot* for its subscribed symbol; a newer
     frame overwrites an unsent older one (latest-wins conflation).
  2. Credit window: at most ``ack_window`` frames may be un-acknowledged.
     The browser acks after it has *painted*, so a slow GPU/CPU slows only
     itself and never grows a queue anywhere.
  3. If acks stop entirely, the window resets after ``STALL_RESET_S``.
"""

import asyncio
import collections
import contextlib
import json
import logging
import time
from typing import Any

import fastapi
from fastapi import responses

from optdesk import bus as bus_lib
from optdesk import config, feed, pricer, pricing, protocol

STALL_RESET_S = 2.0


class ClientSession:
    """Outbound state and pump for one WebSocket client."""

    def __init__(self, ws: Any, window: int) -> None:
        """Creates a session.

        Args:
          ws: Object with async ``send_bytes`` and ``send_text``.
          window: Max un-acknowledged frames in flight.
        """
        self.ws = ws
        self.window = window
        self.symbol_id: int | None = None
        self.slot: bytes | None = None
        self.inflight = 0
        self.sent = 0
        self.conflated = 0
        self.stall_resets = 0
        self._texts: collections.deque[str] = collections.deque()
        self._wakeup = asyncio.Event()

    def offer(self, frame: bytes) -> None:
        """Stores the newest frame, replacing any unsent one."""
        if self.slot is not None:
            self.conflated += 1
        self.slot = frame
        self._wakeup.set()

    def send_json(self, message: dict[str, Any]) -> None:
        """Queues a control message (sent ahead of any frame)."""
        self._texts.append(json.dumps(message))
        self._wakeup.set()

    def on_ack(self, n: int) -> None:
        """Returns ``n`` credits to the window."""
        self.inflight = max(0, self.inflight - max(0, n))
        self._wakeup.set()

    def subscribe(self, symbol_id: int) -> None:
        """Switches the subscribed symbol and discards the stale slot."""
        self.symbol_id = symbol_id
        self.slot = None
        self.inflight = 0

    async def pump(self) -> None:
        """Sends queued text then frames, honouring the credit window."""
        while True:
            try:
                await asyncio.wait_for(self._wakeup.wait(), STALL_RESET_S)
            except TimeoutError:
                if self.inflight >= self.window:
                    self.inflight = 0
                    self.stall_resets += 1
            self._wakeup.clear()
            while self._texts:
                await self.ws.send_text(self._texts.popleft())
            if self.slot is None or self.inflight >= self.window:
                continue
            frame, self.slot = self.slot, None
            self.inflight += 1
            self.sent += 1
            await self.ws.send_bytes(frame)
            if self.slot is not None:
                self._wakeup.set()


class Hub:
    """Tracks sessions, dispatches frames and aggregates service stats."""

    def __init__(
        self, settings: config.Settings, bus: bus_lib.EventBus
    ) -> None:
        """Creates a hub bound to ``bus``."""
        self.settings = settings
        self.bus = bus
        self.sessions: set[ClientSession] = set()
        self.service_stats: dict[str, dict[str, Any]] = {}
        self.frames_in = 0
        self.frames_sent_closed = 0
        self.conflated_closed = 0
        self._by_name = {s.name: s for s in config.UNIVERSE}
        self._last_frames_in = 0
        self._last_t = time.monotonic()
        self.fps_in = 0.0

    def hello(self) -> dict[str, Any]:
        """Builds the greeting sent on connect."""
        return {
            't': 'hello',
            'version': protocol.FRAME_VERSION,
            'symbols': [
                {'id': s.symbol_id, 'name': s.name, 'spot': s.spot}
                for s in config.UNIVERSE
            ],
            'fields': list(pricing.FIELD_NAMES),
            'ack_window': self.settings.ack_window,
            'server_ms': time.time_ns() / 1e6,
        }

    def dispatch(self, frame: bytes) -> None:
        """Stamps a frame and offers it to every subscribed session."""
        self.frames_in += 1
        sid = protocol.peek_symbol_id(frame)
        targets = [s for s in self.sessions if s.symbol_id == sid]
        if not targets:
            return
        stamped = protocol.stamp_gateway_recv(frame, time.time_ns())
        for session in targets:
            session.offer(stamped)

    async def handle_message(self, session: ClientSession, raw: str) -> None:
        """Handles one client text message (sub, ack, ping, rate)."""
        try:
            msg = json.loads(raw)
            kind = msg['t']
            if kind == 'ack':
                session.on_ack(int(msg['n']))
            elif kind == 'ping':
                session.send_json(
                    {
                        't': 'pong',
                        't0': msg['t0'],
                        'srv_ms': time.time_ns() / 1e6,
                    }
                )
            elif kind == 'sub':
                spec = self._by_name[msg['symbol']]
                session.subscribe(spec.symbol_id)
                session.send_json({'t': 'subscribed', 'symbol': spec.name})
            elif kind == 'rate':
                payload = json.dumps({'tick_hz': float(msg['hz'])}).encode()
                await self.bus.publish(bus_lib.TOPIC_CONTROL, payload)
        except (ValueError, KeyError, TypeError):
            logging.warning('ignoring malformed client message')

    def add(self, session: ClientSession) -> None:
        """Registers a session."""
        self.sessions.add(session)

    def remove(self, session: ClientSession) -> None:
        """Unregisters a session, retaining its totals."""
        if session in self.sessions:
            self.sessions.discard(session)
            self.frames_sent_closed += session.sent
            self.conflated_closed += session.conflated

    def snapshot(self) -> dict[str, Any]:
        """Returns the gateway's own stats."""
        return {
            'service': 'gateway',
            'clients': len(self.sessions),
            'frames_in': self.frames_in,
            'fps_in': round(self.fps_in, 1),
            'frames_sent': self.frames_sent_closed
            + sum(s.sent for s in self.sessions),
            'conflated': self.conflated_closed
            + sum(s.conflated for s in self.sessions),
            'stall_resets': sum(s.stall_resets for s in self.sessions),
        }

    def prometheus(self) -> str:
        """Renders gateway and pricer stats in Prometheus text format."""
        gw = self.snapshot()
        lines = [
            f'optdesk_gateway_clients {gw["clients"]}',
            f'optdesk_gateway_frames_in_total {gw["frames_in"]}',
            f'optdesk_gateway_frames_sent_total {gw["frames_sent"]}',
            f'optdesk_gateway_conflated_total {gw["conflated"]}',
        ]
        compute = self.service_stats.get('pricer', {}).get('compute_us', {})
        for q in ('p50', 'p99'):
            if q in compute:
                lines.append(f'optdesk_pricer_compute_us_{q} {compute[q]}')
        return '\n'.join(lines) + '\n'

    async def run_surface_loop(self) -> None:
        """Consumes surface frames from the bus forever."""
        sub = await self.bus.subscribe(bus_lib.TOPIC_SURFACE)
        try:
            async for frame in sub:
                self.dispatch(frame)
        finally:
            await sub.close()

    async def run_stats_loop(self) -> None:
        """Collects other services' stats and broadcasts a merged view."""
        sub = await self.bus.subscribe(bus_lib.TOPIC_STATS)

        async def collect() -> None:
            async for payload in sub:
                with contextlib.suppress(ValueError, KeyError):
                    stats = json.loads(payload)
                    self.service_stats[stats['service']] = stats

        collector = asyncio.create_task(collect())
        try:
            while True:
                await asyncio.sleep(self.settings.stats_interval_s)
                now = time.monotonic()
                self.fps_in = (self.frames_in - self._last_frames_in) / max(
                    now - self._last_t, 1e-9
                )
                self._last_frames_in, self._last_t = self.frames_in, now
                message = {
                    't': 'stats',
                    'gateway': self.snapshot(),
                    'services': self.service_stats,
                }
                for session in tuple(self.sessions):
                    session.send_json(message)
        finally:
            collector.cancel()
            await sub.close()


def create_app(
    settings: config.Settings,
    bus: bus_lib.EventBus | None = None,
    embedded: bool = False,
) -> fastapi.FastAPI:
    """Builds the gateway ASGI application.

    Args:
      settings: Runtime settings.
      bus: Event bus; created from ``settings.bus_url`` when omitted.
      embedded: Also run the feed and a pricer inside this process
        (single-process development mode).

    Returns:
      A FastAPI app exposing ``/ws``, ``/healthz``, ``/api/symbols`` and
      ``/metrics``.
    """
    bus = bus or bus_lib.make_bus(settings.bus_url)
    hub = Hub(settings, bus)

    @contextlib.asynccontextmanager
    async def lifespan(_: fastapi.FastAPI):
        stop = asyncio.Event()
        tasks = [
            asyncio.create_task(hub.run_surface_loop()),
            asyncio.create_task(hub.run_stats_loop()),
        ]
        await asyncio.sleep(0)  # Let subscriptions register first.
        if embedded:
            tasks.append(asyncio.create_task(pricer.PricerService(
                bus, settings).run(stop)))  # fmt: skip
            await asyncio.sleep(0)
            tasks.append(asyncio.create_task(feed.FeedService(
                bus, settings).run(stop)))  # fmt: skip
        try:
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await bus.close()

    app = fastapi.FastAPI(title='optdesk gateway', lifespan=lifespan)
    app.state.hub = hub

    @app.get('/healthz')
    async def healthz() -> dict[str, str]:
        return {'status': 'ok'}

    @app.get('/api/symbols')
    async def symbols() -> list[dict[str, Any]]:
        return hub.hello()['symbols']

    @app.get('/metrics', response_class=responses.PlainTextResponse)
    async def metrics_endpoint() -> str:
        return hub.prometheus()

    @app.websocket('/ws')
    async def ws_endpoint(ws: fastapi.WebSocket) -> None:
        await ws.accept()
        session = ClientSession(ws, settings.ack_window)
        hub.add(session)
        session.send_json(hub.hello())
        pump = asyncio.create_task(session.pump())
        try:
            while True:
                await hub.handle_message(session, await ws.receive_text())
        except fastapi.WebSocketDisconnect:
            pass
        finally:
            pump.cancel()
            hub.remove(session)

    return app
