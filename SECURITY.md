# Security policy

noiserep is a local BLE client that is read-only by default. It opens no network
sockets, has no account system, and sends no telemetry. The main risks are local
data handling and accidental writes to the band, not remote compromise. The one
write path is the experimental, `--yes`-gated `buzz` haptics command; any
unintended write from it is in scope.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting (**Security → Report a
vulnerability**) instead of a public issue. Include the version, OS, Python
version, and a minimal reproduction. **Do not include** your band's MAC address,
serial number, capture files, or real health values; use placeholders.

## In scope

- Code execution or dependency vulnerabilities.
- An unintended write to the band (setting, alarm, clock, delete).
- Data written outside the documented database, export, or `data/` paths.
- A packaged binary that includes captures, databases, or other local data.

## Out of scope

- The band's own firmware or BLE security.
- The vendor app or its cloud service.
- An attacker with physical access to an unlocked machine.

## Privacy notes

A `.pklg` capture, the SQLite database, `data/` output, and CSV/JSON exports
contain device identifiers and health data. Never commit or share them. The CLI
prints the MAC and serial; redact them (and `-v` frame dumps) before posting.
