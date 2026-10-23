"""Regenerates the cross-language golden frame in ``shared/golden``."""

import json
import pathlib

import numpy as np

from optdesk import protocol

GOLDEN_DIR = pathlib.Path(__file__).resolve().parents[2] / 'shared' / 'golden'


def build() -> tuple[bytes, dict]:
    """Builds the golden frame bytes and its expected JSON description."""
    expiries = np.array([0.25, 0.5], dtype=np.float32)
    strikes = np.array([90.0, 100.0, 110.0], dtype=np.float32)
    data = (np.arange(2 * 3 * 9, dtype=np.float32) / 8.0).reshape(2, 3, 9)
    frame = protocol.encode_frame(
        symbol_id=3, seq=424242, spot=101.25,
        tick_ts_ns=1_700_000_000_123_456_789,
        pricer_recv_ns=1_700_000_000_123_556_789,
        pricer_send_ns=1_700_000_000_123_756_789,
        gateway_recv_ns=1_700_000_000_123_956_789,
        expiries=expiries, strikes=strikes, data=data,
    )  # fmt: skip
    expected = {
        'symbolId': 3, 'seq': 424242, 'spot': 101.25,
        'tickNs': '1700000000123456789', 'pricerRecvNs': '1700000000123556789',
        'pricerSendNs': '1700000000123756789',
        'gatewayRecvNs': '1700000000123956789',
        'nExpiries': 2, 'nStrikes': 3, 'nFields': 9,
        'expiries': expiries.tolist(), 'strikes': strikes.tolist(),
        'data': data.ravel().tolist(),
    }  # fmt: skip
    return frame, expected


def main() -> None:
    """Writes frame.bin and frame.json."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    frame, expected = build()
    (GOLDEN_DIR / 'frame.bin').write_bytes(frame)
    (GOLDEN_DIR / 'frame.json').write_text(json.dumps(expected, indent=2))


if __name__ == '__main__':
    main()
