"""Unit tests for the binary wire protocol, incl. the golden frame."""

import json
import pathlib

import numpy as np
import pytest

from optdesk import protocol

GOLDEN = pathlib.Path(__file__).resolve().parents[2] / 'shared' / 'golden'


def test_tick_roundtrip():
    raw = protocol.encode_tick(4, 99, 123.45, 1_700_000_000_000_000_000)
    assert len(raw) == 26
    assert protocol.decode_tick(raw) == (
        4,
        99,
        123.45,
        1_700_000_000_000_000_000,
    )


def test_frame_roundtrip_and_stamp():
    data = np.random.default_rng(0).random((2, 3, 9)).astype(np.float32)
    raw = protocol.encode_frame(
        symbol_id=1, seq=5, spot=10.5, tick_ts_ns=1, pricer_recv_ns=2,
        pricer_send_ns=3, expiries=np.array([0.1, 0.2]),
        strikes=np.array([1.0, 2.0, 3.0]), data=data,
    )  # fmt: skip
    assert len(raw) == 64 + 4 * (2 + 3 + 54)
    assert protocol.peek_symbol_id(raw) == 1
    stamped = protocol.stamp_gateway_recv(raw, 777)
    frame = protocol.decode_frame(stamped)
    assert (frame.gateway_recv_ns, frame.pricer_send_ns) == (777, 3)
    np.testing.assert_array_equal(frame.data, data)
    assert protocol.decode_frame(raw).gateway_recv_ns == 0


@pytest.mark.parametrize('mutate', [lambda b: b[:10], lambda b: b[:-4],
                                    lambda b: b'\0' * 4 + b[4:]])  # fmt: skip
def test_decode_rejects_corrupt_frames(mutate):
    raw = (GOLDEN / 'frame.bin').read_bytes()
    with pytest.raises(ValueError):
        protocol.decode_frame(mutate(raw))


def test_golden_frame_matches_committed_json_and_generator():
    import importlib.util

    raw = (GOLDEN / 'frame.bin').read_bytes()
    expected = json.loads((GOLDEN / 'frame.json').read_text())
    frame = protocol.decode_frame(raw)
    assert frame.symbol_id == expected['symbolId']
    assert frame.seq == expected['seq']
    assert frame.tick_ts_ns == int(expected['tickNs'])
    assert frame.gateway_recv_ns == int(expected['gatewayRecvNs'])
    np.testing.assert_allclose(frame.data.ravel(), expected['data'])
    spec = importlib.util.spec_from_file_location(
        'make_golden',
        pathlib.Path(__file__).resolve().parents[1]
        / 'scripts'
        / 'make_golden.py',
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build()[0] == raw
