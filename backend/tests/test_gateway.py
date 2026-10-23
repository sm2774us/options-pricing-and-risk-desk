"""Gateway unit tests (backpressure) and full-pipeline integration test."""

import asyncio

import numpy as np
import pytest
from starlette import testclient

from optdesk import config, gateway, pricing, protocol


class FakeWebSocket:
    """Records outbound messages."""

    def __init__(self):
        self.frames, self.texts = [], []

    async def send_bytes(self, data):
        self.frames.append(data)

    async def send_text(self, data):
        self.texts.append(data)


async def _settle():
    await asyncio.sleep(0.02)


async def test_session_window_conflation_and_ack():
    ws = FakeWebSocket()
    session = gateway.ClientSession(ws, window=2)
    pump = asyncio.create_task(session.pump())
    for i in range(10):
        session.offer(bytes([i]))
        await _settle()
    assert ws.frames == [b'\x00', b'\x01']  # window of 2 exhausted
    assert session.slot == b'\x09' and session.conflated == 7
    session.on_ack(1)
    await _settle()
    assert ws.frames[-1] == b'\x09'  # latest wins, stale frames never sent
    session.on_ack(5)
    assert session.inflight == 0
    pump.cancel()


async def test_session_stall_recovery(monkeypatch):
    monkeypatch.setattr(gateway, 'STALL_RESET_S', 0.05)
    ws = FakeWebSocket()
    session = gateway.ClientSession(ws, window=1)
    pump = asyncio.create_task(session.pump())
    session.offer(b'a')
    await _settle()
    session.offer(b'b')
    await asyncio.sleep(0.15)  # no acks -> window resets, b is released
    assert ws.frames == [b'a', b'b'] and session.stall_resets >= 1
    pump.cancel()


async def test_text_messages_precede_frames():
    ws = FakeWebSocket()
    session = gateway.ClientSession(ws, window=1)
    session.send_json({'t': 'x'})
    session.offer(b'f')
    pump = asyncio.create_task(session.pump())
    await _settle()
    assert ws.texts == ['{"t": "x"}'] and ws.frames == [b'f']
    pump.cancel()


@pytest.mark.integration
def test_full_pipeline_over_websocket():
    settings = config.Settings(
        tick_hz=100.0, seed=3, frame_hz_cap=100.0, expiry_days=(7.0, 30.0),
        n_strikes=9, stats_interval_s=0.2, ack_window=2,
    )  # fmt: skip
    app = gateway.create_app(settings, embedded=True)
    with testclient.TestClient(app) as client:
        assert client.get('/healthz').json() == {'status': 'ok'}
        assert len(client.get('/api/symbols').json()) == 5
        with client.websocket_connect('/ws') as ws:
            hello = ws.receive_json()
            assert hello['t'] == 'hello' and hello['ack_window'] == 2
            assert hello['fields'] == list(pricing.FIELD_NAMES)
            ws.send_json({'t': 'ping', 't0': 123.0})
            ws.send_json({'t': 'sub', 'symbol': 'NVDA'})
            frames, seen = [], set()
            while len(frames) < 6:
                msg = ws.receive()
                if msg.get('bytes') is not None:
                    frames.append(protocol.decode_frame(msg['bytes']))
                    ws.send_json({'t': 'ack', 'n': 1})
                else:
                    seen.add(__import__('json').loads(msg['text'])['t'])
            assert {'pong', 'subscribed'} <= seen
            ws.send_json({'t': 'rate', 'hz': 50})

        for f in frames:
            assert f.symbol_id == 3 and f.data.shape == (2, 9, 9)
            assert (f.tick_ts_ns <= f.pricer_recv_ns <= f.pricer_send_ns
                    <= f.gateway_recv_ns)  # fmt: skip
        assert [f.seq for f in frames] == sorted({f.seq for f in frames})
        f = frames[-1]
        lhs = f.data[..., pricing.CALL_PX] - f.data[..., pricing.PUT_PX]
        t = f.expiries[:, None]
        rhs = f.spot * np.exp(-settings.dividend_yield * t) - f.strikes[
            None, :] * np.exp(-settings.rate * t)  # fmt: skip
        np.testing.assert_allclose(lhs, rhs, rtol=2e-3, atol=2e-3)
        assert 'optdesk_gateway_frames_in_total' in client.get('/metrics').text
