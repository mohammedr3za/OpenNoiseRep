"""Typed errors for the live (Phase 1) layer. Decode errors live in
noiserep.protocol.decode.DecodeError; these cover transport and sync."""
from __future__ import annotations


class BandNotFound(Exception):
    """BLE scan did not find the band (often: it is asleep off-charger)."""


class TransportError(Exception):
    """Chunk framing / ack failure, or the band NACKed a frame."""


class SyncTimeout(Exception):
    """Expected data did not arrive within the allotted time."""
