"""Binary wire formats shared by every service and the browser.

Two fixed-layout little-endian messages are used so that no JSON parsing or
object allocation happens on the hot path:

* Tick (feed -> pricer), 26 bytes.
* Surface frame (pricer -> gateway -> browser): a 64 byte header, followed
  by float32 arrays ``expiries[n_e]``, ``strikes[n_k]`` and
  ``data[n_e][n_k][n_f]``.

The TypeScript twin of this module is ``frontend/src/protocol/codec.ts``;
both are tested against ``shared/golden``.
"""

import struct
from typing import NamedTuple

import numpy as np

TICK_STRUCT = struct.Struct('<HQdQ')
FRAME_MAGIC = 0x4F504458  # 'OPDX'
FRAME_VERSION = 1
HEADER_STRUCT = struct.Struct('<IHHQHHHHdQQQQ')
HEADER_BYTES = HEADER_STRUCT.size
SYMBOL_ID_OFFSET = 6
GATEWAY_RECV_OFFSET = HEADER_BYTES - 8

assert HEADER_BYTES == 64, HEADER_BYTES


class Tick(NamedTuple):
    """One underlying price update.

    Attributes:
      symbol_id: Dense symbol id.
      seq: Per-symbol feed sequence number.
      spot: Last price.
      ts_ns: Feed wall-clock publish time in epoch nanoseconds.
    """

    symbol_id: int
    seq: int
    spot: float
    ts_ns: int


class Frame(NamedTuple):
    """A decoded surface frame (timestamps in epoch nanoseconds)."""

    symbol_id: int
    seq: int
    spot: float
    tick_ts_ns: int
    pricer_recv_ns: int
    pricer_send_ns: int
    gateway_recv_ns: int
    expiries: np.ndarray
    strikes: np.ndarray
    data: np.ndarray


def encode_tick(symbol_id: int, seq: int, spot: float, ts_ns: int) -> bytes:
    """Serialises a tick."""
    return TICK_STRUCT.pack(symbol_id, seq, spot, ts_ns)


def decode_tick(buf: bytes) -> Tick:
    """Parses a tick; raises struct.error on malformed input."""
    return Tick(*TICK_STRUCT.unpack(buf))


def encode_frame(
    *,
    symbol_id: int,
    seq: int,
    spot: float,
    tick_ts_ns: int,
    pricer_recv_ns: int,
    pricer_send_ns: int,
    expiries: np.ndarray,
    strikes: np.ndarray,
    data: np.ndarray,
    gateway_recv_ns: int = 0,
) -> bytes:
    """Serialises a surface frame.

    Args:
      symbol_id: Dense symbol id.
      seq: Sequence number of the tick that triggered this frame.
      spot: Underlying price used for pricing.
      tick_ts_ns: Feed publish time of the triggering tick.
      pricer_recv_ns: Time the pricer received that tick.
      pricer_send_ns: Time the pricer finished computing.
      expiries: Years to each expiry, shape (n_e,).
      strikes: Strikes, shape (n_k,).
      data: Pricing output, shape (n_e, n_k, n_f).
      gateway_recv_ns: Left zero; stamped later by the gateway.

    Returns:
      The serialised frame.
    """
    n_e, n_k, n_f = data.shape
    header = HEADER_STRUCT.pack(
        FRAME_MAGIC,
        FRAME_VERSION,
        symbol_id,
        seq,
        n_e,
        n_k,
        n_f,
        0,
        spot,
        tick_ts_ns,
        pricer_recv_ns,
        pricer_send_ns,
        gateway_recv_ns,
    )
    return b''.join(
        (
            header,
            np.asarray(expiries, dtype='<f4').tobytes(),
            np.asarray(strikes, dtype='<f4').tobytes(),
            np.ascontiguousarray(data, dtype='<f4').tobytes(),
        )
    )


def decode_frame(buf: bytes) -> Frame:
    """Parses a surface frame.

    Args:
      buf: Serialised frame.

    Returns:
      The decoded Frame; arrays are read-only views into ``buf``.

    Raises:
      ValueError: If the magic, version or length is wrong.
    """
    if len(buf) < HEADER_BYTES:
        raise ValueError('frame shorter than header')
    (magic, version, symbol_id, seq, n_e, n_k, n_f, _, spot, tick_ts,
     recv, send, gw) = HEADER_STRUCT.unpack_from(buf, 0)  # fmt: skip
    if magic != FRAME_MAGIC or version != FRAME_VERSION:
        raise ValueError('bad frame magic or version')
    expected = HEADER_BYTES + 4 * (n_e + n_k + n_e * n_k * n_f)
    if len(buf) != expected:
        raise ValueError(f'frame length {len(buf)} != expected {expected}')
    offset = HEADER_BYTES
    expiries = np.frombuffer(buf, '<f4', n_e, offset)
    offset += 4 * n_e
    strikes = np.frombuffer(buf, '<f4', n_k, offset)
    offset += 4 * n_k
    data = np.frombuffer(buf, '<f4', n_e * n_k * n_f, offset)
    return Frame(
        symbol_id, seq, spot, tick_ts, recv, send, gw, expiries, strikes,
        data.reshape(n_e, n_k, n_f),
    )  # fmt: skip


def peek_symbol_id(buf: bytes) -> int:
    """Reads the symbol id without decoding the whole frame."""
    return struct.unpack_from('<H', buf, SYMBOL_ID_OFFSET)[0]


def stamp_gateway_recv(buf: bytes, ts_ns: int) -> bytes:
    """Returns a copy of ``buf`` with the gateway receive time patched in."""
    patched = bytearray(buf)
    struct.pack_into('<Q', patched, GATEWAY_RECV_OFFSET, ts_ns)
    return bytes(patched)
