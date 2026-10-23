"""Unit tests for config, metrics, feed and pricer logic."""

import asyncio
import json

import numpy as np

from optdesk import bus as bus_lib
from optdesk import config, feed, metrics, pricer, protocol


def test_settings_from_env_parsing():
    s = config.Settings.from_env(
        {
            'OPTDESK_TICK_HZ': '120',
            'OPTDESK_EXPIRY_DAYS': '1, 7,30',
            'OPTDESK_PRICER_SYMBOLS': 'SPY, QQQ',
            'OPTDESK_BUS_URL': 'redis://r:6379/0',
        }
    )
    assert s.tick_hz == 120.0
    assert s.expiry_days == (1.0, 7.0, 30.0)
    assert s.pricer_symbols == ('SPY', 'QQQ')
    assert s.bus_url.startswith('redis://')
    assert config.Settings.from_env({}) == config.Settings()


def test_rolling_window_percentiles():
    w = metrics.RollingWindow(capacity=100)
    assert w.snapshot()['count'] == 0
    for v in range(1, 201):
        w.add(v)
    snap = w.snapshot()
    assert snap['count'] == 100 and snap['max'] == 200.0
    assert 150 <= snap['p50'] <= 152


def test_simulator_is_deterministic_and_bounded():
    a = feed.MarketSimulator(config.UNIVERSE, seed=1)
    b = feed.MarketSimulator(config.UNIVERSE, seed=1)
    for _ in range(2000):
        sa, sb = a.step(0.01), b.step(0.01)
    np.testing.assert_array_equal(sa, sb)
    anchors = np.array([s.spot for s in config.UNIVERSE])
    assert np.all(np.abs(sa / anchors - 1) <= 0.15 + 1e-9)


async def test_feed_publishes_monotonic_ticks_and_obeys_control(small_settings):
    bus = bus_lib.InMemoryBus()
    service = feed.FeedService(bus, small_settings, clock_ns=lambda: 42)
    sub = await bus.subscribe(bus_lib.TOPIC_TICKS)
    await service.publish_once(0.01)
    await service.publish_once(0.01)
    ticks = [protocol.decode_tick(await sub.__anext__()) for _ in range(10)]
    assert [t.symbol_id for t in ticks[:5]] == [0, 1, 2, 3, 4]
    assert [t.seq for t in ticks if t.symbol_id == 0] == [1, 2]
    assert all(t.ts_ns == 42 for t in ticks)
    service.apply_control(json.dumps({'tick_hz': 1e9}).encode())
    assert service.tick_hz == feed.MAX_TICK_HZ
    service.apply_control(b'not json')
    assert service.tick_hz == feed.MAX_TICK_HZ


def test_pricer_conflates_and_caps_rate(small_settings):
    clock = iter(range(1_000, 10_000))
    p = pricer.PricerService(
        bus_lib.InMemoryBus(),
        small_settings,
        clock_ns=lambda: next(clock),
        wall_s=lambda: 5_000.0,
    )
    p.on_tick(protocol.Tick(0, 1, 585.0, 1), recv_ns=10)
    p.on_tick(protocol.Tick(0, 2, 586.0, 2), recv_ns=11)
    assert p.conflated == 1
    frames, wait = p.process_due(100.0)
    assert wait is None and len(frames) == 1
    frame = protocol.decode_frame(frames[0])
    assert (frame.seq, frame.spot, frame.pricer_recv_ns) == (2, 586.0, 11)
    assert frame.pricer_send_ns >= 1_000
    # Immediately after publishing, the cap defers the next tick.
    p.on_tick(protocol.Tick(0, 3, 587.0, 3), recv_ns=12)
    frames, wait = p.process_due(100.0001)
    assert frames == [] and wait is not None and 0 < wait <= 1 / 200
    frames, wait = p.process_due(100.01)
    assert len(frames) == 1


def test_pricer_shard_ignores_foreign_symbols(small_settings):
    import dataclasses

    shard = dataclasses.replace(small_settings, pricer_symbols=('AAPL',))
    p = pricer.PricerService(bus_lib.InMemoryBus(), shard)
    assert p.symbol_ids == (2,)
    p.on_tick(protocol.Tick(0, 1, 585.0, 1), recv_ns=1)
    assert p.ticks_in == 0


async def test_pricer_run_end_to_end_over_bus(small_settings):
    bus = bus_lib.InMemoryBus()
    p = pricer.PricerService(bus, small_settings)
    out = await bus.subscribe(bus_lib.TOPIC_SURFACE)
    stop = asyncio.Event()
    task = asyncio.create_task(p.run(stop))
    await asyncio.sleep(0.01)
    await bus.publish(bus_lib.TOPIC_TICKS, protocol.encode_tick(1, 1, 505.0, 1))
    frame = protocol.decode_frame(await asyncio.wait_for(out.__anext__(), 2))
    assert frame.symbol_id == 1 and frame.data.shape == (3, 11, 9)
    stop.set()
    await asyncio.wait_for(task, 2)
