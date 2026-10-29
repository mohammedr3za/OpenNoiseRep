"""Noise REP chunked transport: start-frame + per-chunk writes with the
band's start/end acks, and reassembly of band-initiated transfers into an
async queue of raw payload bytes. Ported from the working client.py; carries
no protobuf and sends no handshake."""
from __future__ import annotations

import asyncio
import struct

from ..errors import TransportError

ACK_START = bytes([0, 0, 1, 1])
ACK_END = bytes([0, 0, 1, 0])


class Band:
    def __init__(self, client, write_uuid: str, debug: bool = False):
        self.c = client
        self.write_uuid = write_uuid
        self.debug = debug
        self.ack_start = asyncio.Event()
        self.ack_end = asyncio.Event()
        self._nack = False
        self._streams: dict[object, dict] = {}
        self.msgs: asyncio.Queue = asyncio.Queue()
        self._tasks: set = set()
        self._closing = False

    def on_notify(self, src, data: bytes) -> None:
        if self._closing:
            return
        t = asyncio.create_task(self._handle(src, bytes(data)))
        self._tasks.add(t)
        t.add_done_callback(self._notify_done)

    def _notify_done(self, task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None and not self._closing:
            self.msgs.put_nowait(TransportError(f"BLE notification failed: {error}"))

    async def aclose(self) -> None:
        """Stop notification ACK writers before disposing the GATT client."""
        self._closing = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._streams.clear()

    async def _write(self, uuid, data, timeout=6.0) -> None:
        if self._closing:
            raise TransportError("BLE transport is closing")
        try:
            await asyncio.wait_for(self.c.write_gatt_char(uuid, data, response=False), timeout)
        except asyncio.TimeoutError as exc:
            raise TransportError("BLE write timed out") from exc

    async def _handle(self, src, data: bytes) -> None:
        if self.debug:
            print(f"  <- {data.hex(' ')}")
        if len(data) < 3:
            return
        idx = struct.unpack_from("<H", data, 0)[0]
        if idx == 0:
            kind = data[2]
            if kind == 0:                                   # band starts a transfer
                n = struct.unpack_from("<H", data, 4)[0] if len(data) >= 6 else 0
                self._streams[src] = {"n": n, "parts": {}}
                await self._write(src, ACK_START)
            elif kind == 1:                                 # ack for our transfer
                if len(data) < 4:
                    return
                st = data[3]
                if st == 1:
                    self.ack_start.set()
                elif st == 0:
                    self.ack_end.set()
                elif st == 2:
                    self._nack = True
                    self.ack_start.set()
            return
        stream = self._streams.get(src)
        if not stream:
            return
        stream["parts"][idx] = data[2:]
        if stream["n"] and len(stream["parts"]) >= stream["n"]:
            full = b"".join(stream["parts"][i] for i in sorted(stream["parts"]))
            self._streams.pop(src, None)
            await self._write(src, ACK_END)
            await self.msgs.put(full)

    async def send(self, payload: bytes, size: int = 242, timeout: float = 6.0) -> None:
        n = max(1, -(-len(payload) // size))
        self.ack_start.clear()
        self.ack_end.clear()
        self._nack = False
        await self._write(self.write_uuid, struct.pack("<HBBH", 0, 0, 0, n), timeout)
        try:
            await asyncio.wait_for(self.ack_start.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TransportError("no start ack from band") from exc
        if self._nack:
            raise TransportError("band NACKed the transfer")
        for i in range(n):
            chunk = struct.pack("<H", i + 1) + payload[i * size:(i + 1) * size]
            await self._write(self.write_uuid, chunk, timeout)

    async def recv(self, timeout: float = 10.0) -> bytes:
        message = await asyncio.wait_for(self.msgs.get(), timeout=timeout)
        if isinstance(message, TransportError):
            raise message
        return message
