"""Vectorised Black-Scholes pricing, Greeks and a parametric vol surface.

All heavy maths is done with numpy broadcasting over the whole
(expiry x strike) grid in one pass, so repricing a full surface costs
a few hundred microseconds.
"""

import numpy as np
from scipy import special

from optdesk import config

SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0
MIN_T_YEARS = 1e-6
MIN_VOL = 0.03
_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)

FIELD_NAMES = (
    'call_px',
    'put_px',
    'call_delta',
    'put_delta',
    'gamma',
    'vega',
    'call_theta',
    'put_theta',
    'iv',
)
N_FIELDS = len(FIELD_NAMES)
(
    CALL_PX,
    PUT_PX,
    CALL_DELTA,
    PUT_DELTA,
    GAMMA,
    VEGA,
    CALL_THETA,
    PUT_THETA,
    IV,
) = range(N_FIELDS)


def black_scholes(
    spot: float,
    strikes: np.ndarray,
    t_years: np.ndarray,
    vol: np.ndarray,
    rate: float,
    div: float,
) -> np.ndarray:
    """Prices European options and Greeks under Black-Scholes-Merton.

    Inputs broadcast against each other following numpy rules.

    Args:
      spot: Underlying price.
      strikes: Strike prices.
      t_years: Time to expiry in years (must be > 0).
      vol: Annualised implied volatility (must be > 0).
      rate: Continuously compounded risk-free rate.
      div: Continuous dividend yield.

    Returns:
      Array of shape ``broadcast(strikes, t_years, vol).shape + (N_FIELDS,)``
      ordered as ``FIELD_NAMES``. Vega is per 1 vol point, theta is per
      calendar day, delta/gamma are per 1 unit of underlying.
    """
    strikes, t, vol = np.broadcast_arrays(
        np.asarray(strikes, dtype=np.float64),
        np.asarray(t_years, dtype=np.float64),
        np.asarray(vol, dtype=np.float64),
    )
    sqrt_t = np.sqrt(t)
    vol_sqrt_t = vol * sqrt_t
    d1 = (np.log(spot / strikes) + (rate - div + 0.5 * vol * vol) * t) / (
        vol_sqrt_t
    )
    d2 = d1 - vol_sqrt_t
    disc_r = np.exp(-rate * t)
    disc_q = np.exp(-div * t)
    cdf_d1 = special.ndtr(d1)
    cdf_d2 = special.ndtr(d2)
    cdf_md1 = 1.0 - cdf_d1
    cdf_md2 = 1.0 - cdf_d2
    pdf_d1 = _INV_SQRT_2PI * np.exp(-0.5 * d1 * d1)

    call = spot * disc_q * cdf_d1 - strikes * disc_r * cdf_d2
    put = strikes * disc_r * cdf_md2 - spot * disc_q * cdf_md1
    call_delta = disc_q * cdf_d1
    put_delta = call_delta - disc_q
    gamma = disc_q * pdf_d1 / (spot * vol_sqrt_t)
    vega = spot * disc_q * pdf_d1 * sqrt_t / 100.0
    decay = -spot * disc_q * pdf_d1 * vol / (2.0 * sqrt_t)
    call_theta = (
        decay - rate * strikes * disc_r * cdf_d2 + div * spot * disc_q * cdf_d1
    ) / 365.0
    put_theta = (
        decay
        + rate * strikes * disc_r * cdf_md2
        - div * spot * disc_q * cdf_md1
    ) / 365.0
    return np.stack(
        (
            call,
            put,
            call_delta,
            put_delta,
            gamma,
            vega,
            call_theta,
            put_theta,
            vol,
        ),
        axis=-1,
    )


class PricingGrid:
    """Fixed listed (expiry x strike) grid for one underlying.

    The grid is immutable after construction; only spot and wall-clock time
    change between calls to ``price``.
    """

    def __init__(
        self,
        spec: config.SymbolSpec,
        settings: config.Settings,
        start_epoch_s: float,
    ) -> None:
        """Initialises the listed grid around the initial spot.

        Args:
          spec: Underlying definition and smile parameters.
          settings: Rates, expiries and strike count.
          start_epoch_s: Wall-clock time at which expiries are anchored.
        """
        self.spec = spec
        self._rate = settings.rate
        self._div = settings.dividend_yield
        center = round(spec.spot / spec.strike_step) * spec.strike_step
        offsets = np.arange(settings.n_strikes) - settings.n_strikes // 2
        self.strikes = (center + spec.strike_step * offsets).astype(np.float64)
        self.expiry_ts = start_epoch_s + 86400.0 * np.asarray(
            settings.expiry_days, dtype=np.float64
        )
        self._strike_row = self.strikes[None, :]

    @property
    def n_expiries(self) -> int:
        """Number of listed expiries."""
        return int(self.expiry_ts.shape[0])

    @property
    def n_strikes(self) -> int:
        """Number of listed strikes."""
        return int(self.strikes.shape[0])

    def time_to_expiry(self, now_s: float) -> np.ndarray:
        """Returns time to each expiry in years, floored at MIN_T_YEARS."""
        t = (self.expiry_ts - now_s) / SECONDS_PER_YEAR
        return np.maximum(t, MIN_T_YEARS)

    def implied_vols(self, spot: float, t_years: np.ndarray) -> np.ndarray:
        """Evaluates the parametric smile on the whole grid.

        Args:
          spot: Current underlying price.
          t_years: Column vector of shape (n_expiries, 1).

        Returns:
          Implied vols of shape (n_expiries, n_strikes), floored at MIN_VOL.
        """
        fwd = spot * np.exp((self._rate - self._div) * t_years)
        log_m = np.log(self._strike_row / fwd)
        atm = self.spec.atm_vol + self.spec.term_slope * np.sqrt(t_years)
        vol = atm + self.spec.skew * log_m + self.spec.curvature * log_m**2
        return np.maximum(vol, MIN_VOL)

    def price(self, spot: float, now_s: float) -> np.ndarray:
        """Reprices the entire surface.

        Args:
          spot: Current underlying price.
          now_s: Wall-clock epoch seconds.

        Returns:
          float32 array of shape (n_expiries, n_strikes, N_FIELDS).
        """
        t = self.time_to_expiry(now_s)[:, None]
        vol = self.implied_vols(spot, t)
        out = black_scholes(
            spot, self._strike_row, t, vol, self._rate, self._div
        )
        return out.astype(np.float32)
