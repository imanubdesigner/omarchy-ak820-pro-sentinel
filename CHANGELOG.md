# Changelog

All notable changes to AK820 Pro Sentinel are recorded here.

The project follows semantic versioning.

## [1.1.0] - 2026-11-11

Lifeline parity and charging detection release.

### Added

- Battery and charging state detection: detects when the AK820 Pro keyboard is connected
  via USB cable (`0c45:8009`) alongside the 2.4G dongle, showing "Charging for <time>"
  in the panel.
- Lifeline interface classification: inspects endpoint counts (endpoints >= 2) in sysfs
  to flag critical vendor keepalive interfaces (`hidraw3` and `hidraw4`).
- Persistent session statistics: recovery and disconnect counters are saved to
  `/var/lib/ak820-pro-sentinel/stats.json` across service restarts.
- Clear protection status: replaces confusing `0 RECOVERIES` label with
  `PROTECTED (0 DROPS)` tinted with the theme accent color, accurately reflecting
  successful proactive link maintenance.
- Explicit lifeline logging: panel event log highlights `Lifeline interface opened`
  with device paths (`/dev/hidraw3`, `/dev/hidraw4`) and keepalive status.
- Recovery Engine status now displays `Active (lifeline protecting)` during normal operation.

## [1.0.0] - 2026-10-31

First complete release. It replaces the backend-only 0.1.0 baseline.

### Added

- Read-only HID monitor with an explicit descriptor lifecycle and stale handle
  cleanup.
- Panel event log entries for interface openings, so a fresh session shows the
  five opened interfaces right after `Sentinel started`.
- One log row per event with the interface chip, instead of collapsing the
  simultaneous rows into a count, so the log shows which exact interface
  disconnected, reopened or recovered.
- Progressive recovery with a 1, 2, 4, 8, 15 and 30 second backoff, and a 30
  second stability window before the retry counter resets.
- Receiver and interface states that are kept separate. A present USB receiver
  is never reported as a working keyboard link.
- Atomic JSON status file at `/run/ak820-pro-sentinel/status.json`, written
  with mode `0644`.
- Structured events with an in-memory history, printed to the systemd journal.
- Command line tools: `--check`, `--status`, `--status --json`,
  `--diagnostics` and `--version`.
- Omarchy bar widget and panel that read the status file and follow the active
  theme through `Commons.Color`.
- A compact event log in the panel, opened with the `LOG` button. It shows
  receiver transitions, disconnections, errors, recovery outcomes and service
  start or stop, with simultaneous interface events grouped into one row.
- A fixed session line at the top of the log, refreshed every second:
  `5 HELD OPEN · UP 2h 34m · 0 RECOVERIES`. It proves that the service is
  holding the interfaces open without adding noise to the event list.
- A `session` block in the status file with the recoveries and disconnects of
  the current service run, and the time of the last recovery. Events of the
  same kind in the same second count as one episode.
- A setup card in the panel when the status file is missing. It says whether
  the service is not installed or merely stopped, and opens the installer or
  `systemctl restart` in an Omarchy terminal, where sudo can ask for the
  password. The panel switches to the normal view by itself when the service
  starts.
- `/usr/local/bin/ak820-pro-sentinel-remove`, the uninstaller left outside the
  plugin folder, so the backend can be removed after `omarchy plugin remove`.
- The installer asks which bar section to place the widget in, with the same
  prompt as the Omarchy marketplace. It only asks on the first placement and
  never moves a widget that is already in the bar.
- The project description now states the real purpose: preventing AK820 Pro
  disconnections on Linux.
- The plugin files live at the repository root, so the project is a valid
  Omarchy plugin folder and can be installed with `omarchy plugin add`. The
  backend and the installers sit beside them.
- Stale snapshot detection in the widget: a status file that stops updating
  without a clean stop is shown as `STALE` after five seconds.
- systemd unit with a runtime directory and basic hardening.
- Installer and uninstaller that never touch `ak820-linux-lifeline.service`.
- Single command installer for the backend, the systemd service and the
  Omarchy widget. The remove action reverses all three.
- Dependency-free unit test suite that runs without hardware.
- Documentation for the backend, the widget and the status contract.

### Fixed

- The 0.1.0 monitor assumed at least two endpoints per HID interface and
  skipped interfaces with one endpoint. Sentinel no longer uses an endpoint
  heuristic and monitors all five interfaces of the receiver.
- The 0.1.0 launcher did not set `PYTHONPATH`, so the installed wrapper could
  fail to import the `sentinel` package.
- HID uevent parsing used the whole `HID_ID=...` line as the regular
  expression. It now matches the value only.
- Stale interface state is now removed when the receiver disappears or moves
  to another USB port, instead of staying in the snapshot forever.
- The final status snapshot is written once, after the stop event, with
  `running` set to `false`.

### Changed

- The backend is split into small modules: configuration, events, state,
  recovery, USB detection, HID monitoring, diagnostics and the CLI.
- The recovery policy is independent from the hardware, so it can be tested
  with a simulated clock.
- The README describes the real installation flow, the status contract and
  the widget.
- The shield now uses the theme urgent color for every real problem:
  recovering, disconnected, stale and error. A clean stop and missing data
  stay muted.
- The panel header and the bar tooltip now spell out the state, for example
  `AK820 Pro connected`.
- The status file exposes `event_log` with the significant events instead of
  the raw `events` array, so the snapshot keeps the same size and the panel
  shows only what matters.
- The README now leads with the marketplace install: one `omarchy plugin add`
  command plus one click on the setup card, with the terminal one-liner as an
  alternative and `ak820-pro-sentinel-remove` for removal.
- The license placeholder was replaced by the full MIT license and a NOTICE
  file.

### Removed

- `LICENSE-NOTICE.txt`, which was a placeholder and not a license.

## [0.1.0]

Initial backend baseline: receiver detection, `hidraw` monitoring and a
systemd unit.
