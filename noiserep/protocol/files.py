"""Decoders for the activity files the band pushes as protocol type 4097.

A pushed file arrives as a protobuf ``Command{type=4097}`` whose field 16 ->
field 1 carries ``{f1 name, f2 data, f3 flag}``.  A single logical file is
split by the band into fixed-size data chunks: ``flag=0`` is the first chunk,
``flag=1`` the middle chunks (in arrival order) and ``flag=2`` the last.  The
chunks share the same ``name``.  ``assemble_activity_files`` joins them.

Two payload families are decoded here:

* RRI (``*_RRI_Data.bin.txt``) - a dense array of little-endian u16 slots, one
  slot per second, where a slot holds the RR interval in milliseconds and
  ``0x0000`` / ``0xFFFF`` mean "no beat recorded".  See ``decode_rri_file``
  and docs/FILES.md for the evidence behind this layout.
* Sleep (``*Sleep*``) - either the "sleep stages" layout or the packetised
  "sleep details" layout documented by Gadgetbridge's ``SleepStagesParser``
  and ``SleepDetailsParser``.  See ``decode_sleep``.

Everything here is pure stdlib and defensive: malformed input yields ``None``
(or an empty result), never an exception.  Datetimes are naive and follow the
store's convention (band wall-clock interpreted as UTC).
"""
from __future__ import annotations

import datetime as dt
import re
import struct
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_EPOCH = 946684800          # 2000-01-01T00:00:00Z
MAX_EPOCH = 4102444800         # 2100-01-01T00:00:00Z

FLAG_FIRST = 0
FLAG_MIDDLE = 1
FLAG_LAST = 2
_FLAG_RANK = {FLAG_FIRST: 0, FLAG_MIDDLE: 1, FLAG_LAST: 2}

#: RR values outside this range are treated as noise and dropped.
RRI_MIN_MS = 250
RRI_MAX_MS = 3000

#: Slot values that mean "no beat in this second".
RRI_GAP_SENTINELS = frozenset((0x0000, 0xFFFF))

#: Assumed unit of one raw RRI slot.  The observed values are consistent with
#: milliseconds; interpreting them as 1/1024 s would scale them by 1000/1024
#: (-2.3%), which is within the band's own quantisation.  See docs/FILES.md.
RRI_MS_PER_UNIT = 1.0

#: Canonical sleep-stage codes (Gadgetbridge's Xiaomi convention).  Keeping
#: these means a stored stage integer reads the same in Gadgetbridge and here.
STAGE_NOT_SLEEP = 0
STAGE_UNKNOWN = 1
STAGE_DEEP = 2
STAGE_LIGHT = 3
STAGE_REM = 4
STAGE_AWAKE = 5

STAGE_NAMES = {
    STAGE_NOT_SLEEP: "not_sleep",
    STAGE_UNKNOWN: "unknown",
    STAGE_DEEP: "deep",
    STAGE_LIGHT: "light",
    STAGE_REM: "rem",
    STAGE_AWAKE: "awake",
}

#: Magic that introduces a packet in the "sleep details" layout.
_SLEEP_PACKET_MAGIC = 0xFFFCFAFB
_SLEEP_MAGIC_BYTES = struct.pack("<I", _SLEEP_PACKET_MAGIC)

# Packet types inside the sleep-details stream (Gadgetbridge names).
_PKT_RR = 1
_PKT_SUMMARY = 16
_PKT_STAGES = 17
_PKT_HEADER_ONLY = frozenset((0x2, 0x3, 0x9, 0xC, 0xD, 0xE, 0xF))

# XiaomiActivityFileId subtypes from Gadgetbridge's XiaomiUuids/FileId.
SUBTYPE_SLEEP_STAGES = 0x03
SUBTYPE_SLEEP = 0x08


class ActivityFileError(ValueError):
    """Raised by :func:`assemble_activity_files` when a gap is fatal."""


# ---------------------------------------------------------------------------
# Activity file id (7-byte header used by standard Xiaomi activity files)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActivityFileId:
    """The optional 7-byte header of a standard Xiaomi activity file.

    Layout (little-endian): u32 timestamp, u8 timezone in 15-minute blocks,
    u8 version, u8 packed flags where bit 7 is type, bits 6..2 subtype and
    bits 1..0 detail type.  Standard files follow it with a single zero
    padding byte, which this decoder consumes when present.
    """

    timestamp: dt.datetime
    timezone: int
    version: int
    type: int
    subtype: int
    detail: int


def _epoch_to_dt(ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(tzinfo=None)


def parse_activity_file_id(data: bytes) -> tuple[ActivityFileId, int] | None:
    """Return ``(file_id, payload_offset)`` if ``data`` starts with a plausible
    7-byte Xiaomi activity-file header, else ``None``.

    "Plausible" is deliberately strict (timestamp 2000..2100, timezone <= 96
    blocks, version <= 32) so an all-zero or bare-sample payload is not
    mistaken for a header.  The observed Noise RRI file has no such header:
    its first four bytes are zero.
    """
    if data is None or len(data) < 7:
        return None
    try:
        ts, tz, version, flags = struct.unpack_from("<IBBB", data, 0)
    except struct.error:
        return None
    if not (MIN_EPOCH <= ts <= MAX_EPOCH):
        return None
    if tz > 96 or version > 32:
        return None
    offset = 7
    if len(data) > 7 and data[7] == 0:
        offset = 8            # the standard zero padding byte
    return (
        ActivityFileId(
            timestamp=_epoch_to_dt(ts),
            timezone=tz,
            version=version,
            type=(flags >> 7) & 1,
            subtype=(flags & 0x7F) >> 2,
            detail=flags & 0x03,
        ),
        offset,
    )


# ---------------------------------------------------------------------------
# Chunk assembly
# ---------------------------------------------------------------------------

def _get(obj, name, default=None):
    """Read ``name`` from a dict or an object (both are accepted)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass
class ActivityFile:
    """One logical file assembled from its type-4097 chunks."""

    name: str
    data: bytes
    n_chunks: int
    complete: bool
    reason: str | None = None


def _base_name(name: str) -> str:
    """Grouping key for chunks.

    The band repeats the identical file name on every chunk, so the name is
    itself the base name.  NUL padding and surrounding whitespace are removed.
    """
    if not isinstance(name, str):
        return ""
    return name.rstrip("\x00").strip()


def _ordered_chunks(chunks: list[tuple[int, int, bytes]]) -> list[tuple[int, int, bytes]]:
    # First chunk, then middles in arrival order, then last chunk.
    return sorted(chunks, key=lambda c: (_FLAG_RANK.get(c[1], 1), c[0]))


def _gap_reasons(chunks: Sequence[tuple[int, int, bytes]]) -> list[str]:
    flags = [c[1] for c in chunks]
    reasons: list[str] = []
    if not flags:
        return ["empty group"]
    if flags[0] != FLAG_FIRST:
        reasons.append("missing first chunk (flag 0)")
    if flags[-1] != FLAG_LAST:
        reasons.append("missing last chunk (flag 2)")
    if flags.count(FLAG_FIRST) > 1:
        reasons.append("duplicate first chunk")
    if flags.count(FLAG_LAST) > 1:
        reasons.append("duplicate last chunk")
    # Every chunk between the boundaries must be a middle chunk.  An unknown
    # flag cannot be placed reliably.
    for flag in flags[1:-1] if len(flags) > 2 else ():
        if flag != FLAG_MIDDLE:
            reasons.append(f"unexpected middle flag {flag}")
            break
    return reasons


def assemble_activity_files(
    files: Iterable,
    *,
    on_gap: str = "skip",
    include_incomplete: bool = False,
) -> list[ActivityFile]:
    """Join type-4097 chunks into logical files.

    ``files`` is an iterable of objects/dicts with ``name``, ``data`` and
    ``flag`` attributes (e.g. :class:`noiserep.protocol.decode.DeviceFile`).
    Chunks are grouped by base name and ordered as first, middles in arrival
    order, last.

    A group is incomplete when the first chunk is not flag 0, the last is not
    flag 2, a boundary is duplicated, or a middle chunk has an unknown flag.
    The type-4097 message carries no chunk index/count, so a missing middle
    chunk cannot be detected; completeness means the boundaries are present.

    ``on_gap="skip"`` (default) omits incomplete groups; ``"raise"`` raises
    :class:`ActivityFileError`.  ``include_incomplete=True`` keeps them with
    ``complete=False`` and a human-readable ``reason`` instead.
    """
    if on_gap not in ("skip", "raise"):
        raise ValueError("on_gap must be 'skip' or 'raise'")

    groups: dict[str, list[tuple[int, int, bytes]]] = {}
    order: list[str] = []
    for arrival, f in enumerate(files):
        name = _base_name(_get(f, "name", ""))
        data = _get(f, "data", b"") or b""
        flag = _get(f, "flag", FLAG_MIDDLE)
        try:
            flag = int(flag)
        except (TypeError, ValueError):
            flag = FLAG_MIDDLE
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append((arrival, flag, bytes(data)))

    out: list[ActivityFile] = []
    for name in order:
        chunks = _ordered_chunks(groups[name])
        reasons = _gap_reasons(chunks)
        blob = b"".join(c[2] for c in chunks)
        if reasons and on_gap == "raise":
            raise ActivityFileError(f"{name or '<unnamed>'}: " + "; ".join(reasons))
        if reasons and not include_incomplete:
            continue
        out.append(
            ActivityFile(
                name=name,
                data=blob,
                n_chunks=len(chunks),
                complete=not reasons,
                reason="; ".join(reasons) if reasons else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# RRI
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RriSample:
    """One RR interval.

    ``index`` is the slot number within the file's sample array (one slot per
    second in the observed layout), ``rr_ms`` the interval in milliseconds and
    ``ts`` the absolute time when the file's start is known.
    """

    index: int
    rr_ms: int
    ts: dt.datetime | None = None


@dataclass
class RriFile:
    """A decoded RRI file.  ``samples`` is empty for an all-zero file."""

    start: dt.datetime | None
    samples: list[RriSample] = field(default_factory=list)
    total_slots: int = 0
    rejected: int = 0
    has_header: bool = False
    version: int | None = None


def decode_rri_file(
    data: bytes,
    start: dt.datetime | None = None,
    *,
    start_is_end: bool = False,
    unit_ms: float = RRI_MS_PER_UNIT,
    min_rr_ms: int = RRI_MIN_MS,
    max_rr_ms: int = RRI_MAX_MS,
) -> RriFile | None:
    """Decode an RRI payload into :class:`RriSample` values.

    Inferred layout (see docs/FILES.md for evidence and confidence):

    * an optional 7-byte Xiaomi activity-file header (plus zero padding), which
      is skipped when present;
    * then little-endian u16 slots, one per second, each holding an RR interval
      in milliseconds;
    * ``0x0000`` and ``0xFFFF`` are "no beat" sentinels and are skipped;
    * values outside ``[min_rr_ms, max_rr_ms]`` are counted in ``rejected``.

    ``start`` is the wall-clock time of slot 0.  If omitted, a parsed 7-byte
    header supplies it; otherwise samples carry ``ts=None``.  (The observed
    band file has no header - it starts directly with samples and its date
    comes from the file name.)

    ``start_is_end=True`` treats ``start`` as the *end* of the window, which is
    how the band names its RRI files: the file holds the preceding day and its
    name is the transfer time.  The effective slot-0 time is then
    ``start - total_slots`` seconds.  This is confirmed by the vendor capture:
    only with the end interpretation does its RR burst land at ~15:15, the
    capture's manual measurement time.

    Returns ``None`` for ``None`` or fewer than two bytes; an all-zero payload
    returns an :class:`RriFile` with an empty ``samples`` list.
    """
    if data is None or len(data) < 2:
        return None
    data = bytes(data)

    parsed = parse_activity_file_id(data)
    filenames = None
    offset = 0
    if parsed is not None:
        file_id, offset = parsed
        filenames = file_id
        if start is None:
            start = file_id.timestamp
            start_is_end = False

    total = (len(data) - offset) // 2
    if start is not None and start_is_end:
        start = start - dt.timedelta(seconds=total)

    samples: list[RriSample] = []
    rejected = 0
    for index, pos in enumerate(range(offset, len(data) - 1, 2)):
        value = struct.unpack_from("<H", data, pos)[0]
        if value in RRI_GAP_SENTINELS:
            continue
        rr_ms = value * unit_ms
        if rr_ms < min_rr_ms or rr_ms > max_rr_ms:
            rejected += 1
            continue
        ts = start + dt.timedelta(seconds=index) if start is not None else None
        samples.append(RriSample(index=index, rr_ms=int(round(rr_ms)), ts=ts))

    return RriFile(
        start=start,
        samples=samples,
        total_slots=total,
        rejected=rejected,
        has_header=filenames is not None,
        version=filenames.version if filenames is not None else None,
    )


# ---------------------------------------------------------------------------
# Sleep
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SleepStage:
    """A stage change.  ``stage`` uses the canonical ``STAGE_*`` codes and
    ``raw_stage`` keeps the value exactly as it appeared in the file (which is
    useful while the mapping for a new layout is unverified)."""

    ts: dt.datetime
    stage: int
    raw_stage: int | None = None


@dataclass
class SleepSession:
    """One sleep session.

    Durations are minutes.  Fields that the payload did not provide stay
    ``None`` - no value is invented.  ``source`` names the layout that produced
    the session (``"sleep_stages"`` or ``"sleep_details"``); ``derived`` is
    true when the durations were summed from the stage list because the file
    carried no summary record.
    """

    bedtime: dt.datetime
    wake: dt.datetime
    total_min: int | None = None
    deep_min: int | None = None
    light_min: int | None = None
    rem_min: int | None = None
    awake_min: int | None = None
    quality: int | None = None
    stages: list[SleepStage] = field(default_factory=list)
    source: str = ""
    version: int | None = None
    derived: bool = False
    latency_min: int | None = None
    efficiency: int | None = None
    time_in_bed_min: int | None = None


def _plausible_epoch(ts: int) -> bool:
    return MIN_EPOCH <= ts <= MAX_EPOCH


def _int_or_none(value) -> int | None:
    return None if value is None else int(value)


def _file_version(file_id: ActivityFileId | None) -> int | None:
    return file_id.version if file_id is not None else None


class _Short(Exception):
    pass


class _Reader:
    """Tiny bounds-checked little-endian reader."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def remaining(self) -> int:
        return len(self.buf) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.remaining() < n:
            raise _Short
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def _unpack(self, fmt: str, size: int):
        return struct.unpack(fmt, self.take(size))[0]

    def u16(self) -> int:
        return self._unpack("<H", 2)

    def u16be(self) -> int:
        return self._unpack(">H", 2)

    def u32(self) -> int:
        return self._unpack("<I", 4)

    def i64(self) -> int:
        return self._unpack("<q", 8)

    def peek_u32(self) -> int | None:
        if self.remaining() < 4:
            return None
        return struct.unpack_from("<I", self.buf, self.pos)[0]


def _decode_sleep_stages(body: bytes, file_id: ActivityFileId | None) -> SleepSession | None:
    """Decode the ``SleepStagesParser`` layout (subtype 0x03, version 2).

    After the 7-byte id + padding: 7 unknown bytes, u16 total sleep minutes,
    u32 bedtime, u32 wake time, 3 unknown bytes, u16 deep/light/rem/awake
    minutes, 1 unknown byte, then repeated (u32 epoch timestamp, u8 stage).
    """
    # A header is required to know where the body starts; callers pass an
    # already offset body.  Version is advisory here (observed devices may
    # differ), but the payload still has to validate structurally.
    try:
        r = _Reader(body)
        r.take(7)                       # unknown
        total = r.u16()
        bed = r.u32()
        wake = r.u32()
        r.take(3)                       # unknown
        deep = r.u16()
        light = r.u16()
        rem = r.u16()
        awake = r.u16()
        r.take(1)                       # unknown
    except _Short:
        return None

    if not (_plausible_epoch(bed) and _plausible_epoch(wake)) or wake <= bed:
        return None
    if total > 24 * 60 + 2 * 60:        # > 26 h is not a sleep session
        return None

    stages: list[SleepStage] = []
    while r.remaining() >= 5:
        try:
            ts = r.u32()
            stage = r.u8()
        except _Short:
            break
        if _plausible_epoch(ts):
            stages.append(SleepStage(_epoch_to_dt(ts), stage, stage))

    # The raw stage byte follows the same code space as Gadgetbridge's
    # decodeStage output (2 deep, 3 light, 4 rem, 5 awake), so it can be
    # stored directly.
    if total == 0 and deep == 0 and light == 0 and rem == 0 and awake == 0 and not stages:
        return None
    return SleepSession(
        bedtime=_epoch_to_dt(bed),
        wake=_epoch_to_dt(wake),
        total_min=total,
        deep_min=deep,
        light_min=light,
        rem_min=rem,
        awake_min=awake,
        quality=None,
        stages=stages,
        source="sleep_stages",
        version=_file_version(file_id),
    )


def _decode_stage_nibble(raw: int) -> int:
    """Map a sleep-details stage nibble to the canonical stage code."""
    return {
        0: STAGE_AWAKE,
        1: STAGE_LIGHT,
        2: STAGE_DEEP,
        3: STAGE_REM,
        4: STAGE_NOT_SLEEP,
    }.get(raw, STAGE_UNKNOWN)


def _durations_from_stages(
    stages: Sequence[SleepStage], wake: dt.datetime
) -> tuple[int, int, int, int]:
    """Sum per-stage minutes from stage-change timestamps (derived values)."""
    deep = light = rem = awake = 0
    ordered = sorted(stages, key=lambda s: s.ts)
    for i, stage in enumerate(ordered):
        end = ordered[i + 1].ts if i + 1 < len(ordered) else wake
        minutes = int(round((end - stage.ts).total_seconds() / 60.0))
        if minutes <= 0:
            continue
        if stage.stage == STAGE_DEEP:
            deep += minutes
        elif stage.stage == STAGE_LIGHT:
            light += minutes
        elif stage.stage == STAGE_REM:
            rem += minutes
        elif stage.stage == STAGE_AWAKE:
            awake += minutes
    return deep, light, rem, awake


def _decode_sleep_details(body: bytes, file_id: ActivityFileId | None) -> SleepSession | None:
    """Decode the packetised ``SleepDetailsParser`` layout (subtype 0x08).

    The version selects the header bitmap size and which optional blocks are
    present.  Stage changes arrive as type-17 packets: at RTC ``ts`` a u16
    whose high nibble is the stage and whose low 12 bits are the minutes until
    the next change; the values are big-endian.  A type-16 packet carries the
    summary durations (also big-endian).  Missing optional blocks are skipped,
    exactly as Gadgetbridge does.
    """
    version = _file_version(file_id)
    if version is None or version not in (1, 2, 3, 4, 5):
        return None
    header_size = 2 if version == 5 else 1

    try:
        r = _Reader(body)
        bitmap = r.take(header_size)

        def valid(index: int) -> bool:
            byte = index // 8
            return byte < len(bitmap) and bool(bitmap[byte] & (1 << (7 - (index % 8))))

        idx = 0
        r.u8()                          # isAwake (not persisted)
        idx += 1
        bed = r.u32()
        idx += 1
        wake = r.u32()
        idx += 1

        quality: int | None = None
        if version >= 4:
            if valid(idx):
                quality = r.u8()
            idx += 1

        if version >= 5:
            r.take(9)                   # unknown
            r.u32()                     # bedTime2 (~30 min earlier)
            r.u32()                     # wakeupTime2
            idx += 5

        # Optional HR, SpO2 and snore blocks.  Each is present only when its
        # bitmap bit is set; a present block is {u16 unit, u16 count,
        # [u32 first record time if version >= 2], count samples}.
        for width in (1, 1, None):
            if width is None:
                if version < 3:
                    continue
                width = 4
            if valid(idx):
                r.u16()                 # unit
                count = r.u16()
                if count:
                    if version >= 2:
                        r.u32()         # first record time
                    r.take(count * width)
            idx += 1
    except _Short:
        return None

    if not (_plausible_epoch(bed) and _plausible_epoch(wake)) or wake <= bed:
        return None

    durations: dict[str, int] | None = None
    stages: list[SleepStage] = []

    while r.remaining() >= 17:
        if r.peek_u32() != _SLEEP_PACKET_MAGIC:
            r.pos += 1                  # scan forward for the magic
            continue
        try:
            r.take(4)                   # magic
            r.u8()                      # header length (17)
            packet_ts = r.i64()
            r.u8()                      # parity
            packet_type = r.u8()
            data_len = r.u16be()
            if packet_type in _PKT_HEADER_ONLY:
                continue                # no data follows these packets
            data = r.take(data_len)
        except _Short:
            break

        if packet_type == _PKT_SUMMARY and len(data) >= 13:
            # data0, sleep, wake, light, rem, deep (big-endian), data1, unknown
            durations = {
                "total": struct.unpack_from(">H", data, 1)[0],
                "awake": struct.unpack_from(">H", data, 3)[0],
                "light": struct.unpack_from(">H", data, 5)[0],
                "rem": struct.unpack_from(">H", data, 7)[0],
                "deep": struct.unpack_from(">H", data, 9)[0],
            }
        elif packet_type == _PKT_STAGES:
            if not _plausible_epoch(packet_ts):
                continue
            current = _epoch_to_dt(packet_ts)
            for off in range(0, len(data) - 1, 2):
                value = struct.unpack_from(">H", data, off)[0]
                raw = value >> 12
                offset_min = value & 0x0FFF
                stages.append(SleepStage(current, _decode_stage_nibble(raw), raw))
                current = current + dt.timedelta(minutes=offset_min)
        # type 1 (RR deltas) carries no session summary; ignored here.

    bedtime = _epoch_to_dt(bed)
    wake_dt = _epoch_to_dt(wake)

    derived = False
    if durations is not None:
        total = durations["total"]
        deep = durations["deep"]
        light = durations["light"]
        rem = durations["rem"]
        awake = durations["awake"]
    elif stages:
        deep, light, rem, awake = _durations_from_stages(stages, wake_dt)
        total = deep + light + rem          # sleep time excludes awake
        derived = True
    else:
        total = deep = light = rem = awake = None

    return SleepSession(
        bedtime=bedtime,
        wake=wake_dt,
        total_min=total,
        deep_min=deep,
        light_min=light,
        rem_min=rem,
        awake_min=awake,
        quality=quality,
        stages=stages,
        source="sleep_details",
        version=version,
        derived=derived,
    )


def _find_magic(body: bytes) -> int:
    return body.find(_SLEEP_MAGIC_BYTES)


def decode_sleep(data: bytes, *, name: str | None = None) -> SleepSession | None:
    """Decode a sleep payload, or return ``None`` if it is not a sleep file.

    Supports both layouts observed in Gadgetbridge for this device family:

    * subtype ``0x03`` / version 2 - the fixed "sleep stages" record;
    * subtype ``0x08`` / versions 1-5 - the packetised "sleep details" record.

    The 7-byte file header is authoritative when present.  Without one, the
    packetised layout is recognised by its ``0x fffc fafb`` magic; otherwise the
    fixed layout is attempted with structural validation.  An all-zero or
    random payload returns ``None`` - no session is fabricated.
    """
    if data is None or len(data) < 15:
        return None
    data = bytes(data)

    parsed = parse_activity_file_id(data)
    if parsed is not None:
        file_id, offset = parsed
        body = data[offset:]
        if file_id.subtype == SUBTYPE_SLEEP:
            return _decode_sleep_details(body, file_id)
        if file_id.subtype == SUBTYPE_SLEEP_STAGES:
            return _decode_sleep_stages(body, file_id)
        # Unknown subtype: use the payload shape.
        if _find_magic(body) >= 0:
            return _decode_sleep_details(body, file_id)
        return _decode_sleep_stages(body, file_id)

    # No header: only the packetised layout is self-identifying, and it needs
    # a version, which lives in the header.  Without either we cannot decode
    # safely, so decline rather than guess.
    return None


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

_NAME_START_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})[-_](?P<h>\d{2})(?P<mi>\d{2})"
)


def parse_name_start(name: str) -> dt.datetime | None:
    """Parse the band's ``YYYY-M-D-HHMM_<kind>`` file-name timestamp.

    RRI payloads carry no header, so this is the only source of their start
    time.  Returns ``None`` for names that do not match or encode an invalid
    date, so callers can skip rather than invent a timestamp.
    """
    match = _NAME_START_RE.match(name or "")
    if match is None:
        return None
    try:
        return dt.datetime(
            int(match["y"]), int(match["mo"]), int(match["d"]),
            int(match["h"]), int(match["mi"]),
        )
    except ValueError:
        return None


def file_kind_from_name(name: str) -> str:
    """Classify a pushed file by its band-provided name.

    Returns ``"rri"``, ``"sleep"`` or ``"file"``.  This is only a hint: the
    decoders still validate the payload.
    """
    low = (name or "").lower()
    if "rri" in low:
        return "rri"
    if "sleep" in low or "slp" in low:
        return "sleep"
    return "file"


def decode_activity_file(
    name: str, data: bytes, start: dt.datetime | None = None
) -> RriFile | SleepSession | None:
    """Dispatch to :func:`decode_rri_file` / :func:`decode_sleep` by name.

    When ``start`` is omitted it is parsed from the band's file name, which
    stamps the *end* of an RRI window; pass an explicit ``start`` only if you
    know it is the first sample's time.
    """
    kind = file_kind_from_name(name)
    if kind == "rri":
        if start is None:
            name_end = parse_name_start(name)
            return decode_rri_file(data, name_end, start_is_end=True)
        return decode_rri_file(data, start)
    if kind == "sleep":
        return decode_sleep(data, name=name)
    return None


__all__ = [
    "ActivityFile",
    "ActivityFileError",
    "ActivityFileId",
    "RriFile",
    "RriSample",
    "SleepSession",
    "SleepStage",
    "STAGE_AWAKE",
    "STAGE_DEEP",
    "STAGE_LIGHT",
    "STAGE_NAMES",
    "STAGE_NOT_SLEEP",
    "STAGE_REM",
    "STAGE_UNKNOWN",
    "assemble_activity_files",
    "decode_activity_file",
    "decode_rri_file",
    "decode_sleep",
    "file_kind_from_name",
    "parse_activity_file_id",
    "parse_name_start",
]
