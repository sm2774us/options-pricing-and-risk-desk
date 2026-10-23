"""Pricer service: ticks in, fully repriced surface frames out.

Design notes:
  * Ticks are *conflated* per symbol: only the newest pending tick is priced,
    so a slow pricer never builds a backlog and latency stays bounded.
  * Output rate per symbol is capped at ``frame_hz_cap``.
  * Shards own disjoint symbol sets (``pricer_symbols``) to scale out.
"""

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable, Sequence

from optdesk import bus as bus_lib
from optdesk import config, metrics, pricing, protocol


class PricerService:
    """Recomputes prices and Greeks across the whole strike/expiry grid."""

    def __init__(
        self,
        bus: bus_lib.EventBus,
        settings: config.Settings,
        universe: Sequence[config.SymbolSpec] = config.UNIVERSE,
        clock_ns: Callable[[], int] = time.time_ns,
        wall_s: Callable[[], float] = time.time,
    ) -> None:
        """Creates a pricer shard.

        Args:
          bus: Event bus for ticks (in) and surfaces (out).
          settings: Runtime settings.
          universe: All symbols; the shard filter is applied here.
          clock_ns: Wall-clock source in epoch nanoseconds.
          wall_s: Wall-clock source in epoch seconds (for time decay).
        """
        self._bus = bus
        self._clock_ns = clock_ns
        self._wall_s = wall_s
        self._stats_interval = settings.stats_interval_s
        self._min_interval = 1.0 / settings.frame_hz_cap
        owned = [
            s
            for s in universe
            if not settings.pricer_symbols or s.name in settings.pricer_symbols
        ]
        start = wall_s()
        self._grids = {
            s.symbol_id: pricing.PricingGrid(s, settings, start) for s in owned
        }
        self._pending: dict[int, tuple[protocol.Tick, int]] = {}
        self._last_pub = {sid: -math.inf for sid in self._grids}
        self._wake = asyncio.Event()
        self.compute_us = metrics.RollingWindow()
        self.ticks_in = 0
        self.frames_out = 0
        self.conflated = 0

    @property
    def symbol_ids(self) -> tuple[int, ...]:
        """Ids of the symbols this shard prices."""
        return tuple(self._grids)

    def on_tick(self, tick: protocol.Tick, recv_ns: int) -> None:
        """Registers a tick, replacing any older pending tick (conflation)."""
        if tick.symbol_id not in self._grids:
            return
        self.ticks_in += 1
        if tick.symbol_id in self._pending:
            self.conflated += 1
        self._pending[tick.symbol_id] = (tick, recv_ns)
        self._wake.set()

    def _price(self, tick: protocol.Tick, recv_ns: int) -> bytes:
        grid = self._grids[tick.symbol_id]
        started = time.perf_counter_ns()
        data = grid.price(tick.spot, self._wall_s())
        expiries = grid.time_to_expiry(self._wall_s())
        send_ns = self._clock_ns()
        frame = protocol.encode_frame(
            symbol_id=tick.symbol_id,
            seq=tick.seq,
            spot=tick.spot,
            tick_ts_ns=tick.ts_ns,
            pricer_recv_ns=recv_ns,
            pricer_send_ns=send_ns,
            expiries=expiries,
            strikes=grid.strikes,
            data=data,
        )
        self.compute_us.add((time.perf_counter_ns() - started) / 1e3)
        return frame

    def process_due(self, now_mono: float) -> tuple[list[bytes], float | None]:
        """Prices every pending symbol whose rate cap allows it.

        Args:
          now_mono: Monotonic time in seconds.

        Returns:
          Frames to publish and the seconds until the next capped symbol
          becomes due (None if nothing is waiting on the cap).
        """
        frames: list[bytes] = []
        wait: float | None = None
        for sid in list(self._pending):
            due_in = self._last_pub[sid] + self._min_interval - now_mono
            if due_in > 0:
                wait = due_in if wait is None else min(wait, due_in)
                continue
            tick, recv_ns = self._pending.pop(sid)
            frames.append(self._price(tick, recv_ns))
            self._last_pub[sid] = now_mono
        self.frames_out += len(frames)
        return frames, wait

    async def _consume(self, sub: bus_lib.Subscription) -> None:
        async for payload in sub:
            try:
                tick = protocol.decode_tick(payload)
            except Exception:  # noqa: BLE001 - never die on a bad message.
                logging.warning('dropping malformed tick')
                continue
            self.on_tick(tick, self._clock_ns())

    async def _publish_stats(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(self._stats_interval)
            stats = {
                'service': 'pricer',
                'symbols': list(self._grids),
                'ticks_in': self.ticks_in,
                'frames_out': self.frames_out,
                'conflated': self.conflated,
                'compute_us': self.compute_us.snapshot(),
            }
            await self._bus.publish(
                bus_lib.TOPIC_STATS, json.dumps(stats).encode()
            )

    async def run(self, stop: asyncio.Event) -> None:
        """Runs until ``stop`` is set."""
        sub = await self._bus.subscribe(bus_lib.TOPIC_TICKS)
        tasks = [
            asyncio.create_task(self._consume(sub)),
            asyncio.create_task(self._publish_stats(stop)),
        ]
        logging.info('pricer started: symbols %s', self.symbol_ids)
        try:
            while not stop.is_set():
                if not self._pending:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), 0.2)
                    except TimeoutError:
                        continue
                frames, wait = self.process_due(time.monotonic())
                for frame in frames:
                    await self._bus.publish(bus_lib.TOPIC_SURFACE, frame)
                await asyncio.sleep(0 if wait is None else max(wait, 5e-4))
        finally:
            for task in tasks:
                task.cancel()
            await sub.close()
