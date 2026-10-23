"""Shared fixtures."""

import pytest

from optdesk import config


@pytest.fixture
def small_settings() -> config.Settings:
    """A tiny, fast, deterministic configuration."""
    return config.Settings(
        tick_hz=100.0,
        seed=7,
        frame_hz_cap=200.0,
        expiry_days=(7.0, 30.0, 90.0),
        n_strikes=11,
        stats_interval_s=0.2,
        ack_window=2,
    )
