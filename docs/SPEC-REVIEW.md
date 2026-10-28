# Spec and plan review

Review of the original GUI spec and the Phase 0/1 plans against the code as it
exists on 2026-10-28. The goal is to separate what is **validated** from what is
still assumed, and to record concrete fixes before a public release.

Sources reviewed:

- `docs/superpowers/specs/2026-10-27-noiserep-gui-design.md`
- `docs/superpowers/plans/2026-10-27-noiserep-phase0-decoders.md`
- `docs/superpowers/plans/2026-10-27-noiserep-phase1-live-fetch.md`
- `noiserep/`, `tests/`, `tools/`

Note: `docs/superpowers/` is currently in `.gitignore`. These documents will not
ship with the public repo unless that rule changes, which is the right default
while they contain capture-derived examples.

Severity: **H** = misleading or broken for users; **M** = design/implementation
drift that will cause bugs or support load; **L** = cleanup.

## Summary

| ID | Sev | Finding | Owner |
|---|---|---|---|
| H1 | H | A hard-coded band name makes the tool single-device | CLI, GUI, tools |
| H2 | H | Metric identity is still unverified, but the spec/README implied it was Phase 1 work | Protocol/docs |
| H3 | H | The unset band clock breaks dates, ranges, and `sync --since` | Sync, store, GUI, CLI |
| H4 | H | Committed tests contain a real serial and capture-derived values | Test owners |
| M5 | M | Spec says "send 4097"; the implementation sends 117/123 and receives pushed files | Spec |
| M6 | M | Spec module layout and dataclass names do not match the code | Spec |
| M7 | M | Spec promises battery in the GUI; no battery read exists | Spec, protocol |
| M8 | M | The read-only boundary is not stated precisely enough for control commands | Sync, docs |
| M9 | M | `SyncTimeout` is unused; timeout budget can truncate a slow but valid sync | Sync, errors |
| M10 | M | Blob-only metrics are not stored/exported as metrics; exports lack metadata | Store, export |
| M11 | M | `noiserep gui` drops argv and does not handle missing Qt deps | CLI |
| M12 | M | Spec says git is not initialized and Phase 4 is future; both are stale | Spec |
| L13 | L | Spec links to README headings that no longer exist | Spec |
| L14 | L | Python support story (3.10 vs 3.14) is inconsistent | Spec, pyproject |
| L15 | L | No `--version`; no `menubar` command exists | CLI, docs |
| L16 | L | Packaging lacks signing/notarization and LGPL/notices detail | Release |

---

## H1 — Hard-coded band name

**Evidence.** `noiserep/cli.py:175` defaults `--name` to one specific
`Noise_REP_<suffix>`; `noiserep/gui/app.py:21` sets `DEFAULT_BAND` to the same
value; `tools/mac_release.py:20` repeats it; `tests/test_errors.py:10` uses it as
an example. The suffix is derived from the author's MAC.

**Impact.** Every other user's first sync fails with `BandNotFound`. They must
discover `--name` (or edit the GUI source) before the tool works. This is the
single biggest OSS usability defect.

**Recommendation.** Default to discovery of a `Noise_REP_*` advertised name (the
number is model/firmware-dependent, so do not match on `33E3`), and let
`config.band_name` or `--name` override it. If more than one candidate is
visible, ask the user to choose. Add a first-run picker to the GUI. Keep a
generic placeholder such as `Noise_REP_XXXX` in tests and docs.

**Status.** `noiserep/config.py` now has a `band_name` setting (null =
auto-detect) to support this. The CLI/GUI defaults still need the owner's change.

## H2 — Metric identity is not Phase 1 work

**Evidence.** `noiserep/protocol/metrics.py:19` maps only `2 -> heart_rate` and
labels it "tentative". The Phase 1 plan's self-review says metric identity belongs
to Phase 1, but none of its eight tasks identify a metric; they only fetch and
decode. The old README said mapping happens in Phase 1. The live run did not
establish identities for the other 15 ids.

**Impact.** A user reads `Channel 21` (correct) or, worse, any future guessed name
(incorrect). Health data mislabeled as steps/SpO2/sleep is a trust and safety
problem, not a cosmetic one.

**Recommendation.** Keep the neutral fallback. Move identity validation to a
dedicated phase (ROADMAP item 1) with an evidence protocol: cadence, length,
range, correlation with the vendor app across two days, and a second band or
firmware where possible. Only then update `_KNOWN`. Never call a mapping
"tentative" in a comment while displaying the name.

## H3 — The unset band clock

**Evidence.** `docs/PROTOCOL.md` records that a freshly-unpaired band's index and
file dates are around 2002 until a time-sync is sent. `noiserep/cli.py:87-93`
defaults `--since` to the host's today. `sync.py` passes that date to every
`113`/`115` request. Live, the `113` series returned empty for the requested
window. `store/db.py:34-39` treats the band wall clock as UTC. The GUI's
`Today` title and date pickers use store bounds (`gui/app.py:189`, `442-465`), so
they can show a 2002 day as "Today".

**Impact.**
1. `noiserep sync` with the default `--since` can fetch the index and files but
   no series samples.
2. Charts, ranges, and exports carry the wrong date.
3. Two different days can collide if the clock resets.

**Recommendation.**
- Default the fetch window to the earliest device index (or epoch), not host
  today. Document `--since 2000-01-01` as the current workaround.
- Store the device timestamp and a resolved timestamp separately, plus the offset
  used. Show the offset and an "unset clock" banner in the GUI.
- Do **not** auto-write `type=48`. A local anchor/offset is the read-only fix; an
  opt-in time-sync is a write and must be a separate, explicitly confirmed
  feature validated on redundant hardware.

**Status.** ROADMAP item 2. No code change in this pass (sync/store/CLI owners).

## H4 — Real values in committed tests

**Evidence.**
- `tests/test_gui_smoke.py:29` and `tests/test_store.py:12`: fixtures embed the
  real device serial (and a partial MAC).
- `tests/test_decode_files.py:10`: a real capture filename/date
- `tests/test_decode_index.py:15`: a capture-specific date
- `tests/test_cli.py:9,19`: firmware-specific assertions
- `docs/superpowers/plans/*` and `RE-LOG.md` contain the full MAC, serial and
  sample values; those files are gitignored today.

**Impact.** The serial in those fixtures is a real device identifier. The privacy
rule says tests must assert shape, format, and ranges, not real values. Today
most decoder tests do the right thing; the two store/GUI tests and a few
capture-specific assertions do not.

**Recommendation.** Replace the serial with `SN-0000` or `00000` and the MAC with
`aa:bb:cc:dd:ee:ff`; parameterize capture dates from the fixture instead of
hard-coding them; keep firmware assertions only in capture-dependent tests where
the capture is local. Never unignore `docs/superpowers/` until its examples are
sanitized.

**Status.** Flagged for the store/GUI test owners; this pass did not edit other
agents' tests.

## M5 — Spec says to send 4097

**Evidence.** Spec §5: "for each record: send 113/115 ... send 4097 (named
files)". The implementation (`sync.py:93-112`) sends `123` and `117`, then reads
pushed `4097` frames until a timeout. It never sends `4097` or `4109`.
`docs/PROTOCOL.md` and the Phase 1 plan correctly describe a push.

**Recommendation.** Update spec §5 to the confirmed flow. State that `4109`
(explicit file request) is documented but unused and unvalidated.

## M6 — Spec module layout and names drifted

**Evidence.** Spec §4 lists `protocol/wire.proto` and a generated `wire_pb2.py`,
plus `HrSeries`, `ActivityDay`, `RriFile`. The code has a hand-written raw
protobuf reader (`noiserep/wire.py`), no `.proto`, and uses `MetricSeries` and
`DeviceFile`. This is the intended clean-room design, but the spec reads as if a
schema exists.

**Recommendation.** State explicitly: "decoding is raw field walking; no
generated protobuf and no shipped `.proto`." Remove `wire.proto` from the layout
or mark it as an optional future documentation aid. Align dataclass names.

## M7 — Battery is promised but absent

**Evidence.** Spec §7 lists "model / fw / battery / last sync" in the sidebar.
`DeviceInfo` has firmware, serial, MAC, model only. The old `type=2/8` battery
path belongs to the command set this band rejects.

**Recommendation.** Remove battery from the v1 spec, or find and validate a read
command for it (hardware backlog). Do not display a fake or stale value.

## M8 — Read-only boundary needs an allowlist

**Evidence.** The sync sends the transport acks and command types `32`, `112`,
`113`, `115`, `117`, `123`. `117`/`123` are described in `PROTOCOL.md` as
"control/config". `232` is observed but not sent; `4109` and `48` are documented
but not sent. The spec says "no writes" without defining the boundary for
control commands.

**Recommendation.** Document the exact allowlist: `32`, `112`, `113`, `115`,
`117`, `123`, plus start/end acks. Forbid `16`, `17`, `48`, `232`, `4109`, and any
file/delete/settings write until separately validated. Add a unit test that
asserts the exact set of command types `run_sync` emits, so a future change cannot
silently add a write. (The current suite does not assert this.)

## M9 — `SyncTimeout` is unused; timeout math can truncate

**Evidence.** `noiserep/errors.py:14` defines `SyncTimeout`, but nothing raises
it. `run_sync` has `overall_timeout=90.0`, while a worst-case loop is 16 metrics
× 2 requests × 6 s = 192 s. Per-command `asyncio.TimeoutError` is swallowed
(`sync.py:40-50`), so a slow band can produce a partial result with no signal.

**Recommendation.** Either use `SyncTimeout` for an overall deadline and report
partial results explicitly, or remove it. Make the overall budget proportional to
the request count, or stop the loop based on elapsed time and tell the caller.
Keep the partial-result behavior (it is valuable); make it observable.

## M10 — Blob-only metrics and export metadata

**Evidence.** `Store.ingest` skips any series without `samples` and
`interval_s` (`db.py:132-134`). Metrics `14`, `18`, `19`, `25`, `27` carry bytes
in `f2`; those bytes are not queryable as samples and survive only in
`activity_files` if a file also arrived. The exporters write a flat
`metric,timestamp,value` table or a `{metric: [{t,v}]}` object with no units,
device, source, or date range.

**Recommendation.** Add a raw-blob table (or store the raw payload per metric) so
export-all can include every channel. Add an export metadata envelope with
schema version, units (or "unverified"), device model/firmware (no MAC by
default), and the time-offset used. Document zero-skipping: a dense daily array's
`0` means "no reading", not a real zero.

## M11 — `noiserep gui` argv and dependency handling

**Evidence.** `cli.py:196-198` calls `gui_main([])`, dropping Qt arguments.
A missing PySide6 raises `ImportError` with a traceback instead of an install
hint.

**Recommendation.** Pass the remaining argv (`sys.argv` or an explicit list) and
catch `ImportError` to print `pip install 'noiserep[gui]'`. This is CLI-owner
work.

## M12 — Phase 4 status is stale

**Evidence.** Spec §8 says "Git is not initialized in this working directory
yet"; the repo has commits. Spec §11 lists Phase 4 as future; CI and the
PyInstaller build script land in this change, though no binary is released.

**Recommendation.** Update the spec status or add a note that
`docs/ROADMAP.md` and this review supersede the stale lines. Do not rewrite
history; mark the document as a point-in-time design.

## L13 — Stale README references

**Evidence.** Spec §1 links to README sections "2026-10-27 BREAKTHROUGH" and
"2026-10-27 CONFIRMED LIVE". The README has been rewritten and no longer has
those headings.

**Recommendation.** Point protocol claims at `docs/PROTOCOL.md`; keep the README
as the user-facing summary.

## L14 — Python support story

**Evidence.** Spec §12 calls the venv Python 3.14; `pyproject.toml` requires
`>=3.10`; the local venv is 3.14.5; CI covers 3.10–3.13. PySide6/bleak wheels vary
by version.

**Recommendation.** State "supported and CI-tested: 3.10–3.13; 3.14 tested
locally, add to CI when the optional wheels are stable." Keep `requires-python
>=3.10`. Do not claim a version the matrix does not test.

## L15 — `--version` and `menubar`

**Evidence.** The CLI has `decode`, `sync`, `export`, and `gui`; there is no
`--version` and no `menubar` subcommand. The README must not imply one.

**Recommendation.** Add `--version` (reads `noiserep.__version__`). Do not
document a menu bar until ROADMAP item 10 is implemented and validated.

## L16 — Packaging, signing, and LGPL

**Evidence.** `.github/workflows/release.yml` builds unsigned macOS artifacts by
design. PySide6 is LGPL; a bundled app must include the licence and allow
relinking/replacement of the Qt libraries.

**Recommendation.** Keep the unsigned warning in the workflow and README. Include
`LICENSE` (done in `tools/build_app.py`) and a third-party notices file.
Publish checksums. Consider Apple Developer ID signing/notarization only when
there is a maintainer identity to attach it to.

---

## What this pass changed

- Added release metadata to `pyproject.toml` (readme, SPDX licence, classifiers,
  optional extras, dynamic version, pytest config).
- Added `.github/workflows/ci.yml` (3.10–3.13 core, offscreen GUI job),
  `.github/workflows/release.yml` (tagged Linux/macOS PyInstaller artifacts),
  and `tools/build_app.py`.
- Added issue forms and `SECURITY.md` with a privacy-first reporting process.
- Added `noiserep/config.py` + tests as the foundation for ROADMAP item 5.
- Rewrote the README status/roadmap/privacy sections and added
  `docs/ROADMAP.md`.

## Still blocked on hardware

Metric identity, RRI/HRV format, sleep, realtime HR, battery (if any), clock and
`type=48` behavior, explicit `4109` file requests, GUI live sync, packaged-app
runtime, and reconnect behavior. None of these should be claimed as working in
the README or release notes until a documented hardware run exists.
