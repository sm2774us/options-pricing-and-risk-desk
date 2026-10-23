"""Simulated live underlying feed publishing ticks onto the event bus."""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Sequence

import numpy as np

from optdesk import bus as bus_lib
from optdesk import config, protocol

_TRADING_SECONDS_PER_YEAR = 252.0 * 6.5 * 3600.0
_MEAN_REVERSION = 20.0
_MAX_DEVIATION = 0.15
MIN_TICK_HZ = 1.0
MAX_TICK_HZ = 2000.0


class MarketSimulator:
    """Correlation-free geometric Brownian motion with soft mean reversion.

    Prices are pulled gently back to their anchor so that spot always stays
    inside the listed strike grid during long-running demos.
    """

    def __init__(
        self,
        universe: Sequence[config.SymbolSpec],
        seed: int | None = None,
        time_scale: float = 25.0,
    ) -> None:
        """Initialises spots at each symbol's anchor price.

        Args:
          universe: Symbols to simulate, in symbol-id order.
          seed: RNG seed (None for non-deterministic).
          time_scale: Simulated seconds per real second.
        """
        self._rng = np.random.default_rng(seed)
        self._anchor = np.array([s.spot for s in universe], dtype=np.float64)
        self._vol = np.array([s.sim_vol for s in universe], dtype=np.float64)
        self._time_scale = time_scale
        self.spots = self._anchor.copy()

    def step(self, dt_s: float) -> np.ndarray:
        """Advances the simulation by ``dt_s`` real seconds.

        Args:
          dt_s: Real elapsed seconds represented by this step.

        Returns:
          The new spot vector, rounded to the cent.
        """
        dt_y = dt_s * self._time_scale / _TRADING_SECONDS_PER_YEAR
        z = self._rng.standard_normal(self.spots.shape)
        log_ret = (
            -0.5 * self._vol**2 * dt_y
            + self._vol * np.sqrt(dt_y) * z
            + _MEAN_REVERSION * np.log(self._anchor / self.spots) * dt_y
        )
        spots = self.spots * np.exp(log_ret)
        lo = self._anchor * (1.0 - _MAX_DEVIATION)
        hi = self._anchor * (1.0 + _MAX_DEVIATION)
        self.spots = np.round(np.clip(spots, lo, hi), 2)
        return self.spots


class FeedService:
    """Publishes one tick per symbol every ``1 / tick_hz`` seconds."""

    def __init__(
        self,
        bus: bus_lib.EventBus,
        settings: config.Settings,
        universe: Sequence[config.SymbolSpec] = config.UNIVERSE,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        """Creates the service.

        Args:
          bus: Event bus to publish ticks on.
          settings: Runtime settings.
          universe: Symbols to simulate.
          clock_ns: Wall-clock source in epoch nanoseconds.
        """
        self._bus = bus
        self._universe = tuple(universe)
        self._clock_ns = clock_ns
        self._sim = MarketSimulator(
            self._universe, settings.seed, settings.time_scale
        )
        self._stats_interval = settings.stats_interval_s
        self._seq = [0] * len(self._universe)
        self.tick_hz = self._clamp(settings.tick_hz)
        self.ticks_out = 0

    @staticmethod
    def _clamp(hz: float) -> float:
        return float(min(max(hz, MIN_TICK_HZ), MAX_TICK_HZ))

    def apply_control(self, payload: bytes) -> None:
        """Applies a control message such as ``{"tick_hz": 200}``."""
        try:
            msg = json.loads(payload)
            if 'tick_hz' in msg:
                self.tick_hz = self._clamp(float(msg['tick_hz']))
                logging.info('feed rate set to %.0f Hz', self.tick_hz)
        except (ValueError, TypeError):
            logging.warning('ignoring malformed control message')

    async def publish_once(self, dt_s: float) -> None:
        """Advances the simulation one step and publishes every symbol."""
        spots = self._sim.step(dt_s)
        ts_ns = self._clock_ns()
        for i, spec in enumerate(self._universe):
            self._seq[i] += 1
            tick = protocol.encode_tick(
                spec.symbol_id, self._seq[i], float(spots[i]), ts_ns
            )
            await self._bus.publish(bus_lib.TOPIC_TICKS, tick)
        self.ticks_out += len(self._universe)

    async def _listen_control(self) -> None:
        sub = await self._bus.subscribe(bus_lib.TOPIC_CONTROL)
        try:
            async for payload in sub:
                self.apply_control(payload)
        finally:
            await sub.close()

    async def _publish_stats(self) -> None:
        stats = {
            'service': 'feed',
            'tick_hz': self.tick_hz,
            'ticks_out': self.ticks_out,
        }
        await self._bus.publish(bus_lib.TOPIC_STATS, json.dumps(stats).encode())

    async def run(self, stop: asyncio.Event) -> None:
        """Runs until ``stop`` is set, pacing ticks with a drift-free clock."""
        control = asyncio.create_task(self._listen_control())
        next_t = time.perf_counter()
        next_stats = next_t + self._stats_interval
        logging.info('feed started: %d symbols', len(self._universe))
        try:
            while not stop.is_set():
                interval = 1.0 / self.tick_hz
                await self.publish_once(interval)
                now = time.perf_counter()
                if now >= next_stats:
                    await self._publish_stats()
                    next_stats = now + self._stats_interval
                next_t += interval
                delay = next_t - time.perf_counter()
                if delay <= 0:  # Overloaded: re-anchor instead of bursting.
                    next_t = time.perf_counter()
                    delay = 0
                await asyncio.sleep(delay)
        finally:
            control.cancel()
