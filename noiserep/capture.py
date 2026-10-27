"""Reassemble a PacketLogger .pklg (iOS little-endian or classic big-endian)
capture of NoiseFit <-> band BLE traffic into per-command raw payloads.

Pipeline: pklg record -> HCI ACL -> L2CAP (CID 0x0004 = ATT) -> ATT write/notify
value -> chunk reassembly (idx0 start frame, idx>=1 data chunks) -> raw bytes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .wire import field

_ACL_SENT, _ACL_RECV = 0x02, 0x03


@dataclass
class Payload:
    cmd_type: int | None
    direction: str
    raw: bytes


def _read_records(path: str):
    with open(path, "rb") as fh:
        blob = fh.read()
    n = len(blob)
    endian = "<"
    if n >= 4:
        le = struct.unpack_from("<I", blob, 0)[0]
        be = struct.unpack_from(">I", blob, 0)[0]
        if not (8 <= le <= n) and (8 <= be <= n):
            endian = ">"
    off = 0
    while off + 12 <= n:
        rlen, _ts_s, _ts_us = struct.unpack_from(endian + "III", blob, off)
        if rlen < 8 or off + 4 + rlen > n:
            break
        payload = blob[off + 12: off + 4 + rlen]
        off += 4 + rlen
        if payload:
            yield payload[0], payload[1:]


class _L2cap:
    def __init__(self):
        self.buf = {}

    def feed(self, acl: bytes):
        if len(acl) < 4:
            return
        hf, tlen = struct.unpack_from("<HH", acl, 0)
        handle, pb_flag = hf & 0x0FFF, (hf >> 12) & 0x3
        data = acl[4:4 + tlen]
        if pb_flag == 0b01 and handle in self.buf:
            cid, ba, total = self.buf[handle]
            ba += data
        else:
            if len(data) < 4:
                return
            l2len, cid = struct.unpack_from("<HH", data, 0)
            ba, total = bytearray(data[4:]), l2len
            self.buf[handle] = (cid, ba, total)
        cid, ba, total = self.buf[handle]
        if len(ba) >= total:
            del self.buf[handle]
            yield cid, bytes(ba[:total])


class _Chunk:
    def __init__(self):
        self.streams = {}
        self.out = []  # (direction, raw)

    def feed(self, handle: int, value: bytes, direction: str):
        if len(value) < 3:
            return
        idx = struct.unpack_from("<H", value, 0)[0]
        if idx == 0:
            if value[2] == 0:
                n = struct.unpack_from("<H", value, 4)[0] if len(value) >= 6 else 0
                self.streams[handle] = {"n": n, "parts": {}, "dir": direction}
            return
        s = self.streams.get(handle)
        if not s:
            return
        s["parts"][idx] = value[2:]
        if s["n"] and len(s["parts"]) >= s["n"]:
            full = b"".join(s["parts"][i] for i in sorted(s["parts"]))
            self.streams.pop(handle, None)
            self.out.append((s["dir"], full))


def reassemble(path: str) -> list[Payload]:
    l2, ch = _L2cap(), _Chunk()
    for typ, acl in _read_records(path):
        if typ not in (_ACL_SENT, _ACL_RECV):
            continue
        direction = "sent" if typ == _ACL_SENT else "recv"
        for cid, pdu in l2.feed(acl):
            if cid != 0x0004 or not pdu:
                continue
            op = pdu[0]
            if op in (0x52, 0x12) and len(pdu) >= 3:      # Write Command / Request
                ch.feed(struct.unpack_from("<H", pdu, 1)[0], pdu[3:], direction)
            elif op in (0x1b, 0x1d) and len(pdu) >= 3:    # Notify / Indicate
                ch.feed(struct.unpack_from("<H", pdu, 1)[0], pdu[3:], direction)
    return [Payload(field(raw, 1), direction, raw) for direction, raw in ch.out]
