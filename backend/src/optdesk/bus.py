"""Event bus abstraction with in-memory and Redis implementations.

The bus is a lossy, low-latency pub/sub fabric: slow consumers never block
publishers (in-memory subscribers drop their oldest message when full;
Redis pub/sub is fire-and-forget by design). Services conflate on top.
"""

import abc
import asyncio
import collections
from typing import Any

TOPIC_TICKS = 'optdesk.ticks'
TOPIC_SURFACE = 'optdesk.surface'
TOPIC_CONTROL = 'optdesk.control'
TOPIC_STATS = 'optdesk.stats'


class Subscription(abc.ABC):
    """An async iterator over the payloads of one topic."""

    def __aiter__(self) -> 'Subscription':
        return self

    @abc.abstractmethod
    async def __anext__(self) -> bytes:
        """Returns the next payload or raises StopAsyncIteration."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Unsubscribes and releases resources."""


class EventBus(abc.ABC):
    """Publish/subscribe transport used by all services."""

    @abc.abstractmethod
    async def publish(self, topic: str, payload: bytes) -> None:
        """Publishes ``payload`` to every current subscriber of ``topic``."""

    @abc.abstractmethod
    async def subscribe(self, topic: str) -> Subscription:
        """Registers a subscriber; messages flow as soon as this returns."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Releases bus resources."""


class _MemorySubscription(Subscription):
    """Bounded queue subscriber that drops its oldest message on overflow."""

    def __init__(self, bus: 'InMemoryBus', topic: str, maxlen: int) -> None:
        self._bus = bus
        self._topic = topic
        self._items: collections.deque[bytes] = collections.deque()
        self._maxlen = maxlen
        self._wake = asyncio.Event()
        self._closed = False
        self.dropped = 0

    def offer(self, payload: bytes) -> None:
        """Enqueues a payload without ever blocking the publisher."""
        if len(self._items) >= self._maxlen:
            self._items.popleft()
            self.dropped += 1
        self._items.append(payload)
        self._wake.set()

    async def __anext__(self) -> bytes:
        while not self._items:
            if self._closed:
                raise StopAsyncIteration
            self._wake.clear()
            await self._wake.wait()
        return self._items.popleft()

    async def close(self) -> None:
        self._closed = True
        self._bus.remove(self._topic, self)
        self._wake.set()


class InMemoryBus(EventBus):
    """Single-process bus for development, tests and ``all`` mode."""

    def __init__(self, max_queue: int = 4096) -> None:
        """Creates an empty bus.

        Args:
          max_queue: Per-subscriber queue bound before oldest-drop kicks in.
        """
        self._max_queue = max_queue
        self._subs: dict[str, set[_MemorySubscription]] = (
            collections.defaultdict(set)
        )

    async def publish(self, topic: str, payload: bytes) -> None:
        for sub in tuple(self._subs.get(topic, ())):
            sub.offer(payload)

    async def subscribe(self, topic: str) -> Subscription:
        sub = _MemorySubscription(self, topic, self._max_queue)
        self._subs[topic].add(sub)
        return sub

    def remove(self, topic: str, sub: _MemorySubscription) -> None:
        """Detaches a subscription (called by the subscription itself)."""
        self._subs[topic].discard(sub)

    async def close(self) -> None:
        for subs in self._subs.values():
            for sub in tuple(subs):
                await sub.close()


class _RedisSubscription(Subscription):
    """Subscriber backed by a redis-py asyncio PubSub object."""

    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    async def __anext__(self) -> bytes:
        while True:
            try:
                msg = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=None
                )
            except (RuntimeError, ConnectionError) as exc:
                raise StopAsyncIteration from exc
            if msg is not None and msg.get('type') == 'message':
                return msg['data']

    async def close(self) -> None:
        await self._pubsub.aclose()


class RedisBus(EventBus):
    """Distributed bus over Redis pub/sub (lowest-latency Redis primitive)."""

    def __init__(self, client: Any) -> None:
        """Wraps an ``redis.asyncio.Redis`` compatible client."""
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> 'RedisBus':
        """Connects lazily to ``url`` (e.g. ``redis://redis:6379/0``)."""
        import redis.asyncio as aioredis

        return cls(aioredis.from_url(url, health_check_interval=15))

    async def publish(self, topic: str, payload: bytes) -> None:
        await self._client.publish(topic, payload)

    async def subscribe(self, topic: str) -> Subscription:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(topic)
        return _RedisSubscription(pubsub)

    async def close(self) -> None:
        await self._client.aclose()


def make_bus(url: str) -> EventBus:
    """Builds a bus from a URL.

    Args:
      url: ``memory://`` or ``redis://...``.

    Returns:
      A new EventBus instance.

    Raises:
      ValueError: If the URL scheme is unsupported.
    """
    if url.startswith('memory://'):
        return InMemoryBus()
    if url.startswith(('redis://', 'rediss://')):
        return RedisBus.from_url(url)
    raise ValueError(f'unsupported bus url: {url!r}')
