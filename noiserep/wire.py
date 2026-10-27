"""Minimal protobuf-style wire reader. The Noise REP band reuses the Xiaomi
Command envelope but overloads/extends its nested fields, so decoding is done
by raw field-walking rather than a fixed schema."""
from __future__ import annotations

from typing import Iterable


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def parse(buf: bytes) -> list[tuple[int, int, object]]:
    out: list[tuple[int, int, object]] = []
    pos = 0
    while pos < len(buf):
        tag, pos = read_varint(buf, pos)
        fno, wt = tag >> 3, tag & 7
        if wt == 0:
            v, pos = read_varint(buf, pos)
        elif wt == 2:
            ln, pos = read_varint(buf, pos)
            if pos + ln > len(buf):
                raise ValueError("truncated length-delimited field")
            v = buf[pos:pos + ln]
            pos += ln
        elif wt == 5:
            v = int.from_bytes(buf[pos:pos + 4], "little")
            pos += 4
        elif wt == 1:
            v = int.from_bytes(buf[pos:pos + 8], "little")
            pos += 8
        else:
            raise ValueError(f"unsupported wire type {wt}")
        out.append((fno, wt, v))
    return out


def field(buf: bytes, fno: int, default=None):
    for f, _, v in parse(buf):
        if f == fno:
            return v
    return default


def field_all(buf: bytes, fno: int) -> list:
    return [v for f, _, v in parse(buf) if f == fno]


def sub(buf: bytes, *path: int) -> bytes | None:
    cur: object = buf
    for fno in path:
        if not isinstance(cur, (bytes, bytearray)):
            return None
        cur = field(cur, fno)
        if cur is None:
            return None
    return cur if isinstance(cur, (bytes, bytearray)) else None


def write_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint cannot encode a negative value")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode(fields: Iterable[tuple[int, "int | bytes"]]) -> bytes:
    out = bytearray()
    for fno, val in fields:
        if isinstance(val, bool):
            raise TypeError("bool is not a valid field value")
        if isinstance(val, int):
            out += write_varint((fno << 3) | 0)
            out += write_varint(val)
        elif isinstance(val, (bytes, bytearray)):
            out += write_varint((fno << 3) | 2)
            out += write_varint(len(val))
            out += bytes(val)
        else:
            raise TypeError(f"field {fno}: unsupported value type {type(val).__name__}")
    return bytes(out)
