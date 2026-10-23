"""Unit tests for Black-Scholes maths and the vol surface grid."""

import numpy as np
import pytest

from optdesk import config, pricing

R, Q = 0.045, 0.012


def _bs(spot, strike, t, vol):
    return pricing.black_scholes(spot, np.array(strike), np.array(t), vol, R, Q)


def test_put_call_parity():
    strikes = np.linspace(80, 120, 9)
    out = pricing.black_scholes(100.0, strikes, 0.5, 0.25, R, Q)
    lhs = out[..., pricing.CALL_PX] - out[..., pricing.PUT_PX]
    rhs = 100.0 * np.exp(-Q * 0.5) - strikes * np.exp(-R * 0.5)
    np.testing.assert_allclose(lhs, rhs, atol=1e-9)


def test_known_reference_price():
    # Hull-style check: S=K=100, T=1, vol=20%, r=5%, q=0.
    out = pricing.black_scholes(100.0, 100.0, 1.0, 0.2, 0.05, 0.0)
    assert out[pricing.CALL_PX] == pytest.approx(10.4506, abs=1e-3)
    assert out[pricing.PUT_PX] == pytest.approx(5.5735, abs=1e-3)


def test_delta_gamma_vega_theta_match_finite_differences():
    s, k, t, vol, h = 100.0, 105.0, 0.4, 0.3, 1e-3
    base = _bs(s, k, t, vol)
    up = pricing.black_scholes(s + h, np.array(k), np.array(t), vol, R, Q)
    dn = pricing.black_scholes(s - h, np.array(k), np.array(t), vol, R, Q)
    fd_delta = (up[pricing.CALL_PX] - dn[pricing.CALL_PX]) / (2 * h)
    fd_gamma = (up[pricing.CALL_PX] - 2 * base[pricing.CALL_PX]
                + dn[pricing.CALL_PX]) / h**2  # fmt: skip
    assert base[pricing.CALL_DELTA] == pytest.approx(fd_delta, rel=1e-5)
    assert base[pricing.GAMMA] == pytest.approx(fd_gamma, rel=1e-3)

    v_up = _bs(s, k, t, vol + 1e-4)[pricing.CALL_PX]
    v_dn = _bs(s, k, t, vol - 1e-4)[pricing.CALL_PX]
    assert base[pricing.VEGA] == pytest.approx(
        (v_up - v_dn) / 2e-4 / 100, rel=1e-5
    )

    dt = 1e-5
    t_up = _bs(s, k, t + dt, vol)[pricing.CALL_PX]
    t_dn = _bs(s, k, t - dt, vol)[pricing.CALL_PX]
    fd_theta_day = -(t_up - t_dn) / (2 * dt) / 365.0
    assert base[pricing.CALL_THETA] == pytest.approx(fd_theta_day, rel=1e-4)
    assert base[pricing.PUT_DELTA] == pytest.approx(
        base[pricing.CALL_DELTA] - np.exp(-Q * t)
    )


@pytest.fixture
def grid(small_settings):
    return pricing.PricingGrid(config.UNIVERSE[0], small_settings, 1_000.0)


def test_grid_shape_dtype_and_strikes(grid):
    out = grid.price(585.0, 1_000.0 + 10)
    assert out.shape == (3, 11, pricing.N_FIELDS)
    assert out.dtype == np.float32
    assert np.all(np.diff(grid.strikes) == 5.0)
    assert grid.strikes[5] == 585.0
    assert np.isfinite(out).all()


def test_smile_has_put_skew_and_floor(grid):
    t = grid.time_to_expiry(1_000.0)[:, None]
    vol = grid.implied_vols(585.0, t)
    assert vol[1, 2] > vol[1, 8]  # low strike richer than high strike
    assert vol.min() >= pricing.MIN_VOL


def test_time_decay_and_floor(grid):
    early = grid.time_to_expiry(1_000.0)
    later = grid.time_to_expiry(1_000.0 + 86_400.0)
    assert np.all(later < early)
    past = grid.time_to_expiry(1_000.0 + 400 * 86_400.0)
    assert np.all(past == pricing.MIN_T_YEARS)
    assert np.isfinite(grid.price(585.0, 1_000.0 + 400 * 86_400.0)).all()


def test_monotone_call_price_in_strike(grid):
    out = grid.price(585.0, 1_000.0)
    assert np.all(np.diff(out[..., pricing.CALL_PX], axis=1) < 0)
