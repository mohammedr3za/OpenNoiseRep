import asyncio
import struct

import pytest

from noiserep.ble.transport import Band
from noiserep.errors import TransportError

WRITE = "16186f02-0000-1000-8000-00807f9b34fb"
ACK_START = bytes([0, 0, 1, 1])
ACK_END = bytes([0, 0, 1, 0])


class FakeClient:
    def __init__(self):
        self.writes = []            # (uuid, bytes)

    async def write_gatt_char(self, uuid, data, response=False):
        self.writes.append((uuid, bytes(data)))


def test_send_writes_start_frame_then_chunk():
    async def scenario():
        fc = FakeClient()
        band = Band(fc, WRITE)
        task = asyncio.create_task(band.send(b"\x08\x20"))
        await asyncio.sleep(0)                       # let send emit the start frame
        await band._handle(WRITE, ACK_START)         # band grants the transfer
        await task
        return fc.writes

    writes = asyncio.run(scenario())
    assert writes[0][1] == struct.pack("<HBBH", 0, 0, 0, 1)   # 1 chunk
    assert writes[1][1] == struct.pack("<H", 1) + b"\x08\x20"


def test_band_initiated_transfer_reassembles_and_acks():
    async def scenario():
        fc = FakeClient()
        band = Band(fc, WRITE)
        src = "16186f01-0000-1000-8000-00807f9b34fb"
        # band announces a 2-chunk transfer: idx0, type0, 0, numChunks=2
        await band._handle(src, struct.pack("<HBBH", 0, 0, 0, 2))
        await band._handle(src, struct.pack("<H", 1) + b"AAA")
        await band._handle(src, struct.pack("<H", 2) + b"BBB")
        payload = await band.recv(timeout=1.0)
        return payload, fc.writes

    payload, writes = asyncio.run(scenario())
    assert payload == b"AAABBB"
    sent = [w[1] for w in writes]
    assert ACK_START in sent and ACK_END in sent


def test_send_raises_transport_error_on_no_start_ack():
    band = Band(FakeClient(), WRITE)
    with pytest.raises(TransportError):
        asyncio.run(band.send(b"x", timeout=0.05))


def test_send_raises_transport_error_on_nack():
    async def scenario():
        band = Band(FakeClient(), WRITE)
        task = asyncio.create_task(band.send(b"x"))
        await asyncio.sleep(0)
        await band._handle(WRITE, bytes([0, 0, 1, 2]))
        with pytest.raises(TransportError):
            await task

    asyncio.run(scenario())


def test_send_multi_chunk_payload():
    payload = bytes(range(256))[:200] + bytes(100)   # 300 bytes
    assert len(payload) == 300

    async def scenario():
        fc = FakeClient()
        band = Band(fc, WRITE)
        task = asyncio.create_task(band.send(payload))
        await asyncio.sleep(0)
        await band._handle(WRITE, ACK_START)
        await task
        return fc.writes

    writes = [w[1] for w in asyncio.run(scenario())]
    assert len(writes) == 3
    assert writes[0] == struct.pack("<HBBH", 0, 0, 0, 2)
    assert writes[1] == struct.pack("<H", 1) + payload[:242]
    assert writes[2] == struct.pack("<H", 2) + payload[242:]
    assert len(payload[242:]) == 58
