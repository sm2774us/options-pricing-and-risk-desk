"""Unit tests for the in-memory and Redis (fakeredis) buses."""

import asyncio

import fakeredis
import pytest

from optdesk import bus as bus_lib


async def _next(sub):
    return await asyncio.wait_for(sub.__anext__(), 2.0)


async def test_memory_bus_fanout_and_isolation():
    bus = bus_lib.InMemoryBus()
    a = await bus.subscribe('t1')
    b = await bus.subscribe('t1')
    other = await bus.subscribe('t2')
    await bus.publish('t1', b'x')
    assert await _next(a) == await _next(b) == b'x'
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(other.__anext__(), 0.05)


async def test_memory_bus_drops_oldest_when_full():
    bus = bus_lib.InMemoryBus(max_queue=2)
    sub = await bus.subscribe('t')
    for i in range(5):
        await bus.publish('t', bytes([i]))
    assert [await _next(sub), await _next(sub)] == [b'\x03', b'\x04']


async def test_memory_subscription_close_stops_iteration():
    bus = bus_lib.InMemoryBus()
    sub = await bus.subscribe('t')
    await sub.close()
    with pytest.raises(StopAsyncIteration):
        await sub.__anext__()


async def test_redis_bus_roundtrip_with_fakeredis():
    client = fakeredis.FakeAsyncRedis()
    bus = bus_lib.RedisBus(client)
    sub = await bus.subscribe('topic')
    await bus.publish('topic', b'hello')
    assert await _next(sub) == b'hello'
    await sub.close()
    await bus.close()


def test_make_bus_dispatch():
    assert isinstance(bus_lib.make_bus('memory://'), bus_lib.InMemoryBus)
    with pytest.raises(ValueError):
        bus_lib.make_bus('kafka://x')
