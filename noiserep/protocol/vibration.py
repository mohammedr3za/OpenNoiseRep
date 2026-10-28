"""Experimental, opt-in vibration-motor commands for the Noise REP band.

This module is deliberately pure: it only builds protobuf payloads and has no
BLE dependency.  Sending one of these frames is a **write to the band**, so it
must only happen behind an explicit opt-in (the planned ``buzz --yes`` CLI
subcommand), never as part of a sync, export, GUI refresh, or other automatic
path.

Status: **hardware-unverified on the Noise REP.**  The frames below are built
from the Xiaomi wear command set shared by Gadgetbridge and the NoiseFit app's
vendor SDK.  This particular band's captured sync uses different, flat command
type values (see ``docs/VIBRATION.md``), so the first live test must confirm
whether the standard envelope is accepted.  ``envelope="flat"`` is provided as
an explicit experiment for the alternate layout observed in the capture.

Confidence summary
------------------
* ``FIND_WEAR`` (type 2, subtype 18, ``System.findDevice`` = 0/1): the vendor
  SDK and Gadgetbridge independently build exactly these bytes.  High
  confidence for the protocol family, unverified for this firmware.
* ``VIBRATION_TEST`` (type 2, subtype 59, ``System.vibrationTestCustom``): the
  message exists in ``xiaomi.proto``, but Gadgetbridge never calls it and no
  capture of the vendor app using it has been seen.  Not a reliable primitive
  until hardware-tested.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import wire

# Safety caps.  These are intentionally conservative: a stuck vibration command
# would drain the battery and be unpleasant to wear.
MAX_DURATION_MS = 10_000
MAX_SEGMENTS = 16
MAX_REPETITIONS = 5
MIN_INTERVAL_S = 1.0

# Command envelope values.
SYSTEM_TYPE = 2
FIND_WEAR_SUBTYPE = 18
VIBRATION_TEST_SUBTYPE = 59
# The flat "system" service type seen in this band's own sync capture.  Only
# used by the explicit ``envelope="flat"`` experiment, never by default.
FLAT_SYSTEM_TYPE = 0x20

# System sub-message fields (``Command.system`` is field 4).
SYSTEM_FIELD = 4
FIND_DEVICE_FIELD = 5
VIBRATION_TEST_FIELD = 41


@dataclass(frozen=True)
class VibrationRequest:
    """A documented command shape, including how much to trust it.

    ``system_field`` is the field number inside the ``System`` sub-message that
    carries the payload.  ``confidence`` is deliberately prose: it records that
    these bytes are derived from a reference implementation / decompiled vendor
    app, not from a successful write against the Noise REP.
    """

    name: str
    type: int
    subtype: int
    system_field: int
    confidence: str
    hardware_verified: bool = False
    notes: str = ""

    def build(self, payload: bytes, envelope: str = "vendor") -> bytes:
        """Wrap ``payload`` in a ``Command`` envelope.

        ``envelope`` is one of:

        * ``"vendor"`` (default): the documented Xiaomi/vendor shape,
          ``Command{type, subtype, system{<field>: payload}}``.
        * ``"flat"``: the flat shape observed in this band's sync capture,
          ``Command{type=0x20, system{<field>: payload}}`` with no subtype.
          This is an experiment, not a recommendation.
        """
        system = wire.encode([(self.system_field, payload)])
        if envelope == "vendor":
            return wire.encode([
                (1, self.type),
                (2, self.subtype),
                (SYSTEM_FIELD, system),
            ])
        if envelope == "flat":
            return wire.encode([
                (1, FLAT_SYSTEM_TYPE),
                (SYSTEM_FIELD, system),
            ])
        raise ValueError(f"unknown envelope {envelope!r}; expected 'vendor' or 'flat'")


FIND_WEAR = VibrationRequest(
    name="find_band",
    type=SYSTEM_TYPE,
    subtype=FIND_WEAR_SUBTYPE,
    system_field=FIND_DEVICE_FIELD,
    confidence="high for the protocol family (Gadgetbridge + vendor SDK agree); "
               "unverified on this band",
    notes="0 starts continuous vibration/ringing, 1 stops it.",
)

VIBRATION_TEST = VibrationRequest(
    name="vibration_test",
    type=SYSTEM_TYPE,
    subtype=VIBRATION_TEST_SUBTYPE,
    system_field=VIBRATION_TEST_FIELD,
    confidence="low-to-medium: declared in xiaomi.proto, never used by "
               "Gadgetbridge, no vendor capture observed",
    notes="Payload is VibrationTest{ repeated Vibration{vibrate, ms, strength} }.",
)

# Named patterns, as (on, milliseconds) segments.  These are only used with the
# VIBRATION_TEST primitive.
PRESETS: dict[str, tuple[tuple[bool, int], ...]] = {
    "short": ((True, 200),),
    "long": ((True, 1000),),
    "double": ((True, 120), (False, 120), (True, 120)),
    "triple": ((True, 120), (False, 100), (True, 120), (False, 100), (True, 120)),
}

# Patterns handled by the find-wear primitive instead of VibrationTest.
FIND_PATTERNS = frozenset({"find-band", "find_band", "find", "stop", "off"})
STOP_PATTERNS = frozenset({"stop", "off"})


def _check_ms(ms: int) -> int:
    if isinstance(ms, bool) or not isinstance(ms, int):
        raise TypeError("durations must be integer milliseconds")
    if ms <= 0:
        raise ValueError("duration must be positive milliseconds")
    if ms > MAX_DURATION_MS:
        raise ValueError(
            f"duration {ms} ms exceeds the {MAX_DURATION_MS} ms safety cap"
        )
    return ms


def _check_strength(strength: int) -> int:
    if isinstance(strength, bool) or not isinstance(strength, int):
        raise TypeError("strength must be an integer")
    if not 1 <= strength <= 100:
        raise ValueError("strength must be between 1 and 100")
    return strength


def find_band(start: bool = True, envelope: str = "vendor") -> bytes:
    """Build a find-wear frame (continuous vibration/ringing).

    ``System.findDevice`` is a varint field, so this wraps it directly rather
    than going through the length-delimited ``VibrationRequest.build`` helper.

    The caller MUST send the stop frame (``find_band(False)``) after the
    requested duration, including on errors and cancellation.
    """
    system = wire.encode([(FIND_DEVICE_FIELD, 0 if start else 1)])
    if envelope == "vendor":
        return wire.encode([
            (1, FIND_WEAR.type),
            (2, FIND_WEAR.subtype),
            (SYSTEM_FIELD, system),
        ])
    if envelope == "flat":
        return wire.encode([
            (1, FLAT_SYSTEM_TYPE),
            (SYSTEM_FIELD, system),
        ])
    raise ValueError(f"unknown envelope {envelope!r}; expected 'vendor' or 'flat'")


def find_band_start(envelope: str = "vendor") -> bytes:
    return find_band(True, envelope=envelope)


def find_band_stop(envelope: str = "vendor") -> bytes:
    return find_band(False, envelope=envelope)


def vibration_test(segments, envelope: str = "vendor") -> bytes:
    """Build a ``VibrationTest`` frame from ``(on, ms, strength)`` segments."""
    if not segments:
        raise ValueError("at least one vibration segment is required")
    if len(segments) > MAX_SEGMENTS:
        raise ValueError(f"at most {MAX_SEGMENTS} segments are allowed")
    encoded = []
    total_on = 0
    for seg in segments:
        if len(seg) == 2:
            on, ms = seg
            strength = 100
        elif len(seg) == 3:
            on, ms, strength = seg
        else:
            raise ValueError("segments must be (on, ms) or (on, ms, strength)")
        if not isinstance(on, bool):
            raise TypeError("segment 'on' must be a bool")
        ms = _check_ms(ms)
        if on:
            total_on += ms
        fields = [(1, 1 if on else 0), (2, ms)]
        if on:
            fields.append((3, _check_strength(strength)))
        encoded.append(wire.encode(fields))
    if total_on > MAX_DURATION_MS:
        raise ValueError(
            f"total vibration {total_on} ms exceeds the {MAX_DURATION_MS} ms cap"
        )
    payload = wire.encode([(1, vib) for vib in encoded])
    return VIBRATION_TEST.build(payload, envelope=envelope)


def vibration(
    pattern: str = "short",
    duration: int | None = None,
    strength: int = 100,
    repetitions: int = 1,
    envelope: str = "vendor",
    *,
    duration_ms: int | None = None,
) -> bytes:
    """Build one vibration command frame (no I/O, no sleeping).

    ``duration`` is milliseconds and only valid for single-pulse presets
    (``short``/``long``); it overrides that preset's on-time.  ``duration_ms``
    is an alias so callers can be explicit.

    ``pattern`` is a key of :data:`PRESETS`, or one of ``find-band`` / ``stop``.
    For ``find-band`` this returns the START frame only; the caller must send
    :func:`find_band_stop` after the desired time.  ``duration`` is not used for
    ``find-band`` (raise instead of silently ignoring it).
    """
    if duration_ms is not None:
        if duration is not None:
            raise ValueError("pass only one of duration and duration_ms")
        duration = duration_ms

    key = (pattern or "").strip().lower()
    if key in FIND_PATTERNS:
        if duration is not None:
            raise ValueError(
                "find-band needs a start and a stop frame; the caller controls "
                "the duration and must call find_band_stop()"
            )
        return find_band(key not in STOP_PATTERNS, envelope=envelope)

    if key not in PRESETS:
        raise ValueError(
            f"unknown pattern {pattern!r}; choose from "
            f"{', '.join(sorted(set(PRESETS) | FIND_PATTERNS))}"
        )
    if isinstance(repetitions, bool) or not isinstance(repetitions, int):
        raise TypeError("repetitions must be an integer")
    if not 1 <= repetitions <= MAX_REPETITIONS:
        raise ValueError(f"repetitions must be between 1 and {MAX_REPETITIONS}")

    segments = list(PRESETS[key])
    if duration is not None:
        if len(segments) != 1 or not segments[0][0]:
            raise ValueError(
                f"duration only applies to single-pulse presets, not {key!r}"
            )
        segments = [(True, _check_ms(duration))]

    expanded = segments * repetitions
    if len(expanded) > MAX_SEGMENTS:
        raise ValueError(f"at most {MAX_SEGMENTS} segments are allowed")

    total_on = sum(ms for on, ms in expanded if on)
    if total_on > MAX_DURATION_MS:
        raise ValueError(
            f"total vibration {total_on} ms exceeds the {MAX_DURATION_MS} ms cap"
        )

    return vibration_test(
        [(on, ms, strength) for on, ms in expanded],
        envelope=envelope,
    )
