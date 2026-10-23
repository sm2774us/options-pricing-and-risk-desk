"""Static universe definition and environment-driven runtime settings."""

import dataclasses
import os
from collections.abc import Mapping


@dataclasses.dataclass(frozen=True)
class SymbolSpec:
    """Static description of one underlying and its volatility surface.

    Attributes:
      symbol_id: Dense integer id used on the wire.
      name: Ticker symbol.
      spot: Initial spot price in dollars.
      strike_step: Spacing of the listed strike grid in dollars.
      atm_vol: At-the-money annualised implied volatility at T -> 0.
      skew: Slope of implied vol versus log-moneyness (negative = put skew).
      curvature: Smile curvature (vol per unit of log-moneyness squared).
      term_slope: Additional ATM vol per square-root year of maturity.
      sim_vol: Annualised volatility of the simulated underlying.
    """

    symbol_id: int
    name: str
    spot: float
    strike_step: float
    atm_vol: float
    skew: float
    curvature: float
    term_slope: float
    sim_vol: float


UNIVERSE: tuple[SymbolSpec, ...] = (
    SymbolSpec(0, 'SPY', 585.0, 5.0, 0.14, -0.30, 0.90, 0.02, 0.16),
    SymbolSpec(1, 'QQQ', 505.0, 5.0, 0.19, -0.34, 1.00, 0.02, 0.22),
    SymbolSpec(2, 'AAPL', 225.0, 2.5, 0.24, -0.25, 0.80, 0.03, 0.26),
    SymbolSpec(3, 'NVDA', 135.0, 2.5, 0.48, -0.20, 0.70, 0.04, 0.50),
    SymbolSpec(4, 'TSLA', 250.0, 5.0, 0.55, -0.12, 0.60, 0.04, 0.58),
)

DEFAULT_EXPIRY_DAYS: tuple[float, ...] = (
    1, 2, 3, 5, 7, 14, 21, 30, 45, 60, 90, 120, 180, 270, 365,
)  # fmt: skip


def _parse_tuple(raw: str, cast):
    """Parses a comma separated string into a tuple, skipping blanks."""
    return tuple(cast(part) for part in raw.split(',') if part.strip())


@dataclasses.dataclass(frozen=True)
class Settings:
    """Runtime settings shared by every service.

    Attributes:
      bus_url: ``memory://`` (single process) or ``redis://host:port/db``.
      tick_hz: Initial underlying tick rate per symbol.
      time_scale: Simulation clock multiplier (moves prices faster than
        real time so the UI is lively).
      seed: RNG seed for the simulator; None means non-deterministic.
      frame_hz_cap: Max surface frames per second per symbol (conflation).
      expiry_days: Days-to-expiry of each listed expiry at start-up.
      n_strikes: Number of listed strikes per expiry.
      rate: Continuously compounded risk-free rate.
      dividend_yield: Continuous dividend yield.
      pricer_symbols: Tickers owned by this pricer shard (empty = all).
      host: Gateway bind host.
      port: Gateway bind port.
      ack_window: Max unacknowledged frames in flight per client.
      stats_interval_s: Period of stats publication.
      log_level: Python logging level name.
    """

    bus_url: str = 'memory://'
    tick_hz: float = 30.0
    time_scale: float = 25.0
    seed: int | None = None
    frame_hz_cap: float = 60.0
    expiry_days: tuple[float, ...] = DEFAULT_EXPIRY_DAYS
    n_strikes: int = 61
    rate: float = 0.045
    dividend_yield: float = 0.012
    pricer_symbols: tuple[str, ...] = ()
    host: str = '0.0.0.0'
    port: int = 8000
    ack_window: int = 4
    stats_interval_s: float = 1.0
    log_level: str = 'INFO'

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> 'Settings':
        """Builds settings from ``OPTDESK_*`` environment variables.

        Args:
          env: Mapping to read from; defaults to ``os.environ``.

        Returns:
          A populated, immutable Settings instance.
        """
        env = os.environ if env is None else env
        base = cls()

        def get(name: str, default, cast=str):
            raw = env.get(f'OPTDESK_{name}')
            return default if raw in (None, '') else cast(raw)

        return cls(
            bus_url=get('BUS_URL', base.bus_url),
            tick_hz=get('TICK_HZ', base.tick_hz, float),
            time_scale=get('TIME_SCALE', base.time_scale, float),
            seed=get('SEED', base.seed, int),
            frame_hz_cap=get('FRAME_HZ_CAP', base.frame_hz_cap, float),
            expiry_days=get(
                'EXPIRY_DAYS',
                base.expiry_days,
                lambda r: _parse_tuple(r, float),
            ),
            n_strikes=get('N_STRIKES', base.n_strikes, int),
            rate=get('RATE', base.rate, float),
            dividend_yield=get('DIV_YIELD', base.dividend_yield, float),
            pricer_symbols=get(
                'PRICER_SYMBOLS',
                base.pricer_symbols,
                lambda r: _parse_tuple(r, str.strip),
            ),
            host=get('HOST', base.host),
            port=get('PORT', base.port, int),
            ack_window=get('ACK_WINDOW', base.ack_window, int),
            stats_interval_s=get(
                'STATS_INTERVAL_S', base.stats_interval_s, float
            ),
            log_level=get('LOG_LEVEL', base.log_level),
        )
