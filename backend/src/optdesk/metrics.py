"""Small, dependency-free rolling latency statistics."""

import collections

import numpy as np


class RollingWindow:
    """Fixed-size window of recent samples with percentile snapshots."""

    def __init__(self, capacity: int = 2048) -> None:
        """Creates an empty window.

        Args:
          capacity: Maximum number of retained samples.
        """
        self._samples: collections.deque[float] = collections.deque(
            maxlen=capacity
        )

    def add(self, value: float) -> None:
        """Records one sample, evicting the oldest when full."""
        self._samples.append(float(value))

    def __len__(self) -> int:
        return len(self._samples)

    def snapshot(self) -> dict[str, float]:
        """Returns count, p50, p90, p99 and max (zeros when empty)."""
        if not self._samples:
            return {'count': 0, 'p50': 0.0, 'p90': 0.0, 'p99': 0.0, 'max': 0.0}
        arr = np.fromiter(self._samples, dtype=np.float64)
        p50, p90, p99 = np.percentile(arr, (50, 90, 99))
        return {
            'count': int(arr.size),
            'p50': float(p50),
            'p90': float(p90),
            'p99': float(p99),
            'max': float(arr.max()),
        }
