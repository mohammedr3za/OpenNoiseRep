# noiserep roadmap

Status: **2026-10-28**. Phases 0–3 are implemented; Phase 4 (CI, packaging, first
release) is in progress. This roadmap starts from what the code can honestly claim
today and orders the work by user trust first, daily usefulness second, reach
last.

Read [`README.md`](../README.md) for the current status and
[`docs/PROTOCOL.md`](PROTOCOL.md) for the wire format. The review of the original
spec and plans is in [`docs/SPEC-REVIEW.md`](SPEC-REVIEW.md).

## The rule that outranks the roadmap

noiserep is **read-only by default**. The allowed traffic is the historical-fetch
command set (`32`, `112`, `113`, `115`, `117`, `123`) plus the transport
start/end acks. No settings writes, no alarms, no deletes, no targeted file
requests, and no time-sync unless a later phase adds it behind explicit opt-in
and hardware validation. Every item below states how it respects that rule.

**One explicit exception:** `noiserep buzz` is an experimental, `--yes`-gated
haptics write. It is isolated in `protocol/vibration.py`, is never called by
sync, the GUI or the menu bar, and changes no persistent band state. It stays
opt-in and clearly documented as unverified.

On-device history is the only copy until it is fetched. Nothing in this roadmap
may clear, trim, or acknowledge away band data.

## Priority at a glance

| Priority | Work | Why now | Hardware? | Main risk |
|---|---|---|---|---|
| **P0** | 1. Metric identity validation | Labeling the wrong health signal is worse than labeling none | Yes | Misleading health data |
| **P0** | 2. Timestamp integrity | Dates around 2002 break "Today", ranges, exports and `--since` | Yes | A real fix may require a write |
| **P0** | 3. RRI/HRV + sleep decoding | HRV is the band's differentiator; sleep is the top user question | Offline + spot checks | Proprietary format, wrong HRV |
| **P1** | 4. Packaging & release | Users cannot install without Python today | Build only | Unsigned macOS, bundle size |
| **P1** | 5. User settings | The default band name only works for one device | No | Profile is sensitive |
| **P1** | 6. Sync scheduling / auto-sync | Removes the daily "remember to sync" step | Yes | Band sleep, phone holds the link |
| **P1** | 7. Export-all + backup | Ownership and recovery; one archive for analysis | No | Accidentally sharing health data |
| **P1** | 8. Data retention & backup | Years of history need a safe recovery path | No | Local deletion mistakes |
| **P1** | 9. Privacy & onboarding | Trust and first-five-minutes success | No | Overpromising |
| **P2** | 10. Menu-bar/tray live HR | The glanceable daily hook | Yes (unconfirmed command) | Realtime command unknown |
| **P2** | 11. HR zones & local alerts | Workout guidance without writing to the band | Yes | Medical-claim and spam risk |
| **P3** | 12. Compatibility / multi-band | The tool currently fits one model family | Yes | Per-model protocol drift |
| **P3** | 13. Windows support | Reach | Yes | Packaging and BLE backend work |

"Hardware?" means the item cannot be finished honestly without a band and a
documented test run. Everything else can land with unit tests and offscreen GUI
tests.

---

## P0 — Trust the data

### 1. Metric identity validation

**User value.** Turns "Channel 21" into steps, SpO2, sleep, stress or whatever it
actually is. Until this lands, charts other than heart rate are placeholders and
must stay neutrally labeled.

**Risk.** High. A wrong label is a false health claim: a user could read a raw
byte as SpO2 and worry, or trust a step count that is really an activity score.
Some ids may be aggregates rather than one metric, and the vendor app may derive
values on the phone instead of storing them on the band.

**Depends on.** Live sync, a second observation path (vendor-app screenshots or
a concurrent capture), and ideally more than one day and one firmware version.

**Read-only safety.** Uses only the existing `112`/`113`/`115` reads. Validation
is observational; no new command, no write.

**Approach.**
1. For each of the 16 requested metric ids, record cadence, sample length, value
   range, and whether the data is `f3` samples or an `f2` blob.
2. Align a full day with the vendor app's screens. Candidate checks: steps rise
   during a walk; SpO2 stays in 90–100; sleep forms long low-motion blocks; HRV
   is in milliseconds; stress is a bounded score.
3. Require two independent days and, where possible, a second band/firmware.
4. Record the evidence in `docs/PROTOCOL.md` and only then add the name to
   `noiserep/protocol/metrics.py`. Keep `metric_<id>` as the fallback forever.
5. Add a regression test that unknown ids stay neutrally named.

**Done when.** Every displayed channel is either validated with recorded evidence
or still shown as `Channel N`; no code comment calls a mapping "tentative" while
the GUI displays the name.

### 2. Timestamp integrity

**User value.** Correct "Today", date ranges, exports and trends. Without this,
the GUI can title a 2002 day as "Today" and `sync --since <host date>` can filter
out the very data the user is trying to fetch.

**Risk.** High. A freshly-unpaired band's RTC is unset (~2002) until something
sends a time-sync. The only true fix is a write (`type=48`), which the read-only
rule forbids by default. A local offset can drift after a reset or a long power
loss.

**Depends on.** Observations of the clock across charge cycles; the unconfirmed
`type=48` payload; a consent UX if writing is ever added.

**Read-only safety.**
- **Default:** never write. Store the device timestamp and a local
  anchor/offset; let the user correct the offset when they know one real date.
- **Optional later:** an advanced, off-by-default, explicitly confirmed
  time-sync. It is a write, it must be validated on redundant hardware first,
  and it must never run automatically.
- Change the default sync look-back to the earliest device index (or epoch)
  instead of the host's today, so an unset clock does not empty the fetch.

**Done when.** The store keeps `device_ts` and a resolved `ts` (or a documented
offset); the GUI shows the resolved date and an "unset clock" banner; `--since`
operates on the device timeline; exports record which timeline they used.

### 3. RRI / HRV and sleep decoding

**User value.** HRV (R-R intervals, RMSSD, SDNN) is a genuinely useful recovery
signal and something a step counter cannot provide. Sleep is one of the first
questions every band user asks.

**Risk.** High. The RRI payload is a ~7,200-byte vendor file whose internal
format is unknown; it may have a header, compression, or fixed-point scaling.
Sleep may be computed only in the vendor app, not on the band.

**Depends on.** Offline analysis of already-received files; a known R-R
distribution (roughly 300–2,000 ms at rest); a chest strap or vendor-app
cross-check; the timestamp anchor from item 2.

**Read-only safety.** Decode files the band already pushed. No new command. If a
targeted file request (`4109`) is ever added, treat it as an unvalidated read,
test it on a spare band, and never delete after reading.

**Approach.**
1. Parse the file structure offline: header, record length, endianness, CRC.
2. Reject implausible intervals rather than charting noise.
3. Compute RMSSD/SDNN with unit tests against a synthetic series.
4. Attempt sleep from motion/HR patterns; if the evidence is weak, mark sleep
   **unsupported** rather than inventing stages.

**Done when.** An RRI parser validates length/CRC and yields plausible intervals;
HRV is computed and tested; sleep is either evidenced in `PROTOCOL.md` or
explicitly out of scope.

---

## P1 — Ship it and make it a habit

### 4. Packaging and release

**User value.** A non-Python user can download, open and sync. Trustworthy,
reproducible artifacts also cut support load.

**Risk.** Medium. PySide6 bundles are large (often 150–300 MB); macOS Gatekeeper
blocks unsigned apps; Linux Qt/glibc compatibility varies; PySide6 is LGPL and
needs a notices/linkage story.

**Depends on.** Green CI, a public repository URL, and optionally an Apple
Developer ID for signing/notarization.

**Read-only safety.** No runtime protocol impact. The build must exclude
captures, databases, `data/`, and local exports.

**State.** CI and `tools/build_app.py` land with this change. Remaining work:
set the repository URLs in `pyproject.toml`, tag `v0.0.1`, produce the first
artifacts, document checksums, and add LGPL/third-party notices.

**Done when.** A `v*` tag builds Linux and macOS artifacts, tests run on
3.10–3.13 plus an offscreen GUI job, the release notes state that macOS is
unsigned, and a clean machine can sync without installing Python.

### 5. User settings

**User value.** Works with the user's band instead of the author's. Lets people
choose a database location, metric/imperial units, and an optional profile for
calorie/distance estimates.

**Risk.** Medium. The profile is sensitive; storing it locally is acceptable,
but exporting or transmitting it is not. Unit conversion and age-based formulas
are easy to get subtly wrong. Settings can drift from CLI flags if precedence is
unclear.

**Depends on.** `noiserep/config.py` (landed with this change), CLI/GUI
integration, and band discovery.

**Read-only safety.** Settings are local JSON only. `band_name` changes BLE
scanning, never the band. The profile and DB path never leave the machine and are
never written to the band. A future "write my profile to the band" feature is a
separate, opt-in write and is not part of this item.

**Config keys.** `band_name` (null = auto-detect `Noise_REP_*`), `db_path`
(null = per-OS default), `units` (`metric`/`imperial`), `profile.age`,
`profile.height_cm`, `profile.weight_kg`, `profile.sex`.

**Done when.** CLI flags override config; the GUI has a settings page; defaults
are validated and saved atomically; tests cover malformed files and range
checks. Precedence is documented: **flag > config > built-in default**.

### 6. Sync scheduling / auto-sync

**User value.** Data appears without remembering to press Sync. This is the
single biggest daily-use lever.

**Risk.** Medium–high. The band sleeps when off-charger; the phone may hold the
one central slot; OS background limits differ; overlapping syncs can duplicate or
interleave traffic; polling drains battery.

**Depends on.** Settings, the idempotent store (done), a fast sync path, and
reliable band-presence detection. macOS `launchd` and Linux `systemd --user` are
the natural schedulers; an in-app timer while the GUI/menu bar is running is the
simplest first step.

**Read-only safety.** Runs the existing read-only fetch, nothing else. Use a lock
file so two syncs cannot overlap. Never retry in a tight loop: a miss is expected
and should be reported, not hammered. Do not auto-write the clock to "fix" dates.

**Done when.** Auto-sync is opt-in, skips quietly when the band is absent, shows
"last synced", and produces the same idempotent rows as a manual sync. A test
proves two overlapping runs cannot double-ingest.

### 7. Export-all

**User value.** One archive for backup, spreadsheets, notebooks and migration.
This is the ownership promise in concrete form.

**Risk.** Medium. Archives are large and easy to share accidentally; mixed raw
and derived data can confuse; an undocumented format becomes a compatibility
burden.

**Depends on.** The store and exporters; a manifest with schema/version, units
and provenance; the raw activity files.

**Read-only safety.** Reads local data only. No band traffic.

**Done when.** `noiserep export --all --out bundle.zip` writes one CSV per
metric, a JSON metadata manifest, raw file blobs with SHA-256 hashes, and a
privacy README. The format carries a version and a redaction option.

**Gap in the current code.** `Store.ingest` only stores series that have a cadence
and samples; blob-only metrics (`14`, `18`, `19`, `25`, `27`) live only in
`activity_files`. Export-all must include them or state clearly that it cannot.

### 8. Data retention and backup

**User value.** Control disk use and recover years of history after a bad sync,
a migration, or a corrupted database.

**Risk.** Medium. Deletion is irreversible; backups contain health data; SQLite
can corrupt on an interrupted write; clock shifts can make retention windows
wrong.

**Depends on.** Export-all/backup, schema versioning, and an integrity check.

**Read-only safety.** Deletes only local rows/files, only after confirmation, and
only after a successful backup. It never sends a delete to the band and never
touches on-device history.

**Done when.** `noiserep backup`/`restore` (or the export-all bundle plus a
documented restore) works; `PRAGMA integrity_check` runs; retention is opt-in,
dry-run first, and keeps a safety copy. Default is keep everything.

### 9. Privacy and onboarding

**User value.** Users understand the read-only guarantee, where their data lives,
and what to do when the band is asleep. This is the difference between a
five-minute success and a support thread.

**Risk.** Low–medium. A wall of text gets skipped; vague claims create false
confidence; promising "private" while a capture sits in the repo would be
dishonest.

**Depends on.** Settings and packaging.

**Read-only safety.** Display only. No telemetry, no network calls, no writes.

**Done when.** A first-run screen (GUI) and a short `--help`/`noiserep doctor`
path (CLI) state: read-only, local-only, band on charger if asleep, phone
Bluetooth off, and "captures contain your MAC, serial and health data". The user
can pick a band name and DB location, and a `doctor` command checks Python,
`bleak`, GUI deps, DB permissions, and whether the band is visible. No analytics.

---

## P2 — Glanceable and proactive

### 10. Menu-bar / tray live HR

**User value.** A glanceable current HR without opening a window; a strong reason
to keep the app running; useful during workouts.

**Risk.** High. The realtime HR command is **unconfirmed** for this band. The old
Xiaomi `45`/`46`/`47` types came from the command set that this band rejected, so
they are not evidence. A session control may be required, and battery drain is
real. Tray APIs differ between macOS and Linux.

**Depends on.** Hardware validation of a realtime command, a connection
lifecycle that survives sleep/disconnect, settings, and packaging.

**Read-only safety.** Realtime telemetry is read-only, but starting/stopping a
realtime session is a session control. Send only the validated control; no
settings or alarm writes. Stop the session on disconnect and never leave one
running in the background.

**Done when.** A validated command produces HR notifications, the tray shows the
current value, the user opted in, and the session auto-stops. Until then this
stays explicitly out of scope; do not add a "live HR" button that guesses.

### 11. HR zones and local alerts

**User value.** Workout guidance and high/low HR notifications, plus a resting-HR
trend, without depending on the vendor app.

**Risk.** Medium–high. Medical-claim risk, false alarms, notification fatigue,
and age-based formulas that need the optional profile. Writing alerts to the band
is a write and is out of scope.

**Depends on.** Verified HR identity, settings/profile, realtime HR (item 10),
and OS notifications.

**Read-only safety.** Compute and notify locally. Never write band alerts,
settings, or vibration patterns. Label everything "not medical advice".

**Done when.** Zones are user-defined or age-based with a documented formula;
alerts have hysteresis and a cooldown; quiet hours and mute exist; zone math has
unit tests.

---

## P3 — Reach

### 12. Compatibility and multi-band support

**User value.** More users, and less risk that a firmware update silently breaks
a sync.

**Risk.** High. Command ids and payload shapes may differ by model/firmware; the
advertised name is a weak identifier and the Device Information strings are stock
Cordio placeholders. Support burden can grow without bound.

**Depends on.** Captures from other models, a capability matrix, and a clear
device-identification strategy.

**Read-only safety.** Keep a per-model read-only allowlist. Never share a write
command across models without separate validation.

**Done when.** `docs/COMPATIBILITY.md` lists tested models/firmware and what
works; an unknown device gets a clear refusal instead of a partial, misleading
sync.

### 13. Windows support

**User value.** Reach. The core is pure Python and may already run, but it is
untested and unpackaged.

**Risk.** Medium: BLE backend differences, path conventions, and tray packaging.

**Depends on.** A Windows test machine and a maintainer willing to support it.

**Read-only safety.** Same fetch, same allowlist.

**Done when.** CI runs the core on Windows and a packaged build is smoke-tested.
Until then the README should say "untested" rather than "supported".

---

## What makes people use this daily

Daily use is not a feature list; it is the removal of friction and the presence
of a reason to look. In order of impact:

1. **It syncs itself.** The band charges overnight; the app notices, fetches, and
   the day's data is there in the morning. No phone juggling, no manual button.
2. **It is glanceable.** A menu-bar HR value or a "today" summary answers the
   question in one second. The full chart is one click away.
3. **It is trustworthy.** Verified labels, correct dates, and a visible "raw from
   the band, decoded locally" provenance line. One wrong metric name costs more
   trust than ten missing charts.
4. **It is owned.** Local database, one-click export-all, no account, no cloud,
   works offline. This is the reason people choose a reverse-engineered tool over
   the vendor app.
5. **It is reliable.** A mid-sync drop keeps what arrived; re-syncing never
   duplicates; an asleep band produces a clear runbook, not a stack trace.
6. **The first five minutes work.** Install, discover the band, sync, see a heart
   rate chart — without reading the protocol notes. Onboarding is a feature, not
   documentation.
7. **One calm daily signal.** A single "new data synced" or morning summary
   notification, not a stream. No streaks, no guilt, no gamification.

What kills daily use, and therefore what to test against: a hard-coded band name,
dates in 2002, unlabeled channels, a frozen window during sync, silent failures,
and a phone that holds the BLE link. Each has an owner in the items above.

**Success signals (no telemetry).** Time-to-first-chart under five minutes;
successful-sync reports in issues; users asking for export formats rather than
basic install help; repeat contributors adding protocol evidence. We do not add
analytics to measure this.

## Intentionally not planned

- Cloud sync, accounts, social features, or leaderboards.
- Medical diagnosis, AFib detection, or treatment claims.
- Writing alarms, notifications, settings, or profiles to the band.
- Auto-update that phones home. Release notes and a manual download are enough.
- Deleting or acknowledging band history to "free space".

## Hardware validation backlog

These cannot be closed from unit tests alone:

- Metric identity for ids `0, 4, 8, 11, 12, 14, 17, 18, 19, 21, 22, 23, 25, 26,
  27` (only `2` has a plausible heart-rate identity, and it is still tentative).
- RRI/HRV byte format and plausible interval range.
- Sleep derivation (or an evidence-based "unsupported").
- Realtime HR command and its session lifecycle.
- Battery read, if this band exposes one (the old `type=2/8` path does not).
- Clock behavior across charge cycles and the `type=48` payload.
- Explicit file request `4109` and whether it is safe/read-only in practice.
- GUI live sync and packaged-app runtime on macOS and Linux.
- Reconnect behavior after band sleep and when the phone holds the link.
- Firmware variants and other Noise/Xiaomi rebrands.

## Release checklist

- [ ] CI green on Python 3.10–3.13 and the offscreen GUI job.
- [ ] Capture-dependent tests run locally with `NOISEREP_TEST_PKLG` and skip in CI.
- [ ] No MAC, serial, or real health values in the repo or git history.
- [ ] `[project.urls]` set to the real public repository.
- [ ] Version in `noiserep/__init__.py` matches the tag (`vX.Y.Z`).
- [ ] LICENSE and third-party notices included (PySide6 LGPL, bleak, pyqtgraph,
      qasync, Qt).
- [ ] macOS artifact marked unsigned with first-launch instructions.
- [ ] Release notes and SHA-256 checksums.
- [ ] Artifact smoke-tested on a clean machine; bundle contains no `data/`,
      `*.db`, `*.pklg`, or capture-derived files.
