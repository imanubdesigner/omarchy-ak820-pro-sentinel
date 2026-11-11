# Omarchy widget

The widget is a thin, read-only view of the status file written by the
backend. It does not open the receiver, does not run with root privileges and
does not use a private IPC channel.

## Files

```text
manifest.json  plugin metadata and bar entry point
Panel.qml      bar shield and popup panel
Model.js       status parsing, state mapping, labels and log rows
```

The plugin files sit at the repository root, like every plugin in the Omarchy
marketplace, so this checkout can be validated and installed with
`omarchy plugin add`. The backend, the installers and the tests live beside
them and are ignored by the shell.

The plugin ID is `omarchy-ak820-pro-sentinel`.

## Data flow

```text
receiver -> backend -> /run/ak820-pro-sentinel/status.json -> widget
```

`Panel.qml` uses a Quickshell `FileView` with `watchChanges: true` and a two
second `Timer` as a fallback. The timer matters because the status file does
not exist while the service is stopped, and a file watcher alone cannot react
to a file that appears later.

The panel reads the file as the desktop user. It never needs the configuration
file and never talks to systemd.

Two more `FileView`s only answer setup questions: whether
`/etc/systemd/system/ak820-pro-sentinel.service` exists, and whether this
plugin checkout still contains `install/install.sh`. They never read device
data.

## Setup card

The widget cannot install a root service. When the status file is absent for
more than three seconds, the panel shows a setup card instead of the status
rows:

| Situation | Title | Button |
| --- | --- | --- |
| Unit file missing | `Service not installed` | `INSTALL IN A TERMINAL` |
| Unit file present, no status | `Service not running` | `START IN A TERMINAL` |

The button runs the installer through
`omarchy launch floating terminal with presentation`, the same pattern used by
other marketplace plugins that need root. The command is visible, `sudo` asks
for the password in that terminal and the shell process never holds it. If the
Omarchy launcher is missing, the widget falls back to `xdg-terminal-exec`.

A widget installed by `./install/omarchy-plugin.sh` only copies the three
plugin files, so `install/install.sh` is not present in the plugin folder. In
that case the card still explains the problem but shows the command to run,
without a button. The full checkout keeps the script and gets the button.

When the installer finishes and the service writes its first snapshot, the
existing two second timer replaces the card with the normal view. No shell
restart is needed.

## Theme colors

All colors come from `Commons.Color`:

| State | Color |
| --- | --- |
| Connected or stable | `Commons.Color.accent` |
| Detected | `Commons.Color.foreground` |
| Recovering | `Commons.Color.urgent` |
| Disconnected | `Commons.Color.urgent` |
| Stale | `Commons.Color.urgent` |
| Error | `Commons.Color.urgent` |
| Service stopped | `Commons.Color.muted` |
| Unknown or no data | `Commons.Color.muted` |

The shield uses the theme accent while everything is healthy. It switches to
the theme urgent color as soon as an interface, the receiver or the service
needs attention. A clean stop and a missing file stay muted, because they are
not failures of the watchdog.

The event log uses the same roles: `accent` for a good outcome, `urgent` for a
problem and `muted` for service context.

Typography and spacing use `Style.font` and `Style.space()`. There are no
hardcoded hues, glows or gradients, so the widget follows every theme change.

## State mapping

`Model.isStale(status, nowMs, 5)` runs first. It returns `true` when
`running` is not `false` and `updated_at` is more than five seconds old. That
is the only visible symptom of a service that died without writing a final
snapshot. The panel then shows `STALE` instead of a state that is no longer
trustworthy. A file with `running: false` is a clean stop and is never
reported as stale.

`Model.stateOf(status)` checks the fields in this order:

1. `running === false` gives `stopped`.
2. `receiver.present === false` gives `disconnected`.
3. `summary.errors > 0` gives `error`.
4. `summary.recovering > 0` gives `recovering`.
5. `summary.stable > 0` gives `stable`.
6. `summary.open > 0` gives `connected`.
7. Otherwise the first interface state is used, or `detected` when there is
   no interface data.

`running` is set to `false` only by a clean service stop. A missing file is
shown as the setup card, not as an error. The shield stays muted in both cases.

A one second `Timer` updates `nowMs` in the panel. It only drives the stale
check and never reopens the status file.

## Panel rows

| Row | Source |
| --- | --- |
| Status | `Model.statusSentence` in the header and `Model.stateLabel` in the row |
| USB receiver | `receiver.present` and `receiver.vendor_id` / `product_id` |
| HID interfaces | `summary.open` and `summary.total` |
| Recovery engine | `recovery.pending` and `recovery.active` |
| Last event | `last_event.message` |
| Uptime | `uptime_seconds` |

The bar shield shows the same sentence as a tooltip. The product name in the
sentence is fixed, because the USB receiver reports itself as `2.4G Dongle`.

The footer contains a fixed reminder that the radio link is not verified.

## Event log

The panel header has a `LOG` button. It swaps the status rows for the event log
and becomes `BACK`. Reopening the panel always starts from the status view. The
`LOG` button is hidden while the setup card is visible.

At the top of the log there is a fixed session line, refreshed every second
from the same snapshot:

```text
5 HELD OPEN · UP 2h 34m · 0 RECOVERIES
```

It comes from `sessionHoldLine` and `sessionRecoveryLine` in `Model.js`, which
read `summary.open`, `uptime_seconds` and the `session` block. The recovery
count turns accent once it is above zero. The line is not an event, so it does
not consume one of the 20 log rows. It is the visible proof that the service
currently holds the interfaces open.

The log reads `event_log` from the status file, newest first, and shows at most
20 rows. Every event gets its own row with its interface chip (`IF 0` to
`IF 4`), so the log shows exactly which interface disconnected, reopened or
recovered. One unplug produces one row per interface, and the log keeps the
latest 20 of them.

The backend stores only significant events:

| Event | Title | Tone |
| --- | --- | --- |
| `usb_receiver_removed` | USB receiver removed | `urgent` |
| `usb_receiver_detected` | USB receiver detected | `accent` |
| `hid_interface_opened` | Interface opened | `muted` |
| `hid_interface_closed` | Interface disconnected | `urgent` |
| `hid_open_failed` | Cannot open interface | `urgent` |
| `hid_read_error` | Read error | `urgent` |
| `hid_select_error` | Select error | `urgent` |
| `recovery_started` | Recovery started | `urgent` |
| `recovery_succeeded` | Link recovered | `accent` |
| `monitor_error` | Monitor error | `urgent` |
| `service_started` | Sentinel started | `muted` |
| `service_stopped` | Sentinel stopped | `muted` |

Routine start-up events, individual recovery attempts, stale descriptors and
clean shutdown closures stay out of the log. Interface openings are kept, so a
fresh session shows the five opened interfaces right after `Sentinel started`.
The systemd journal keeps the full event stream.

The log lives in memory and is cleared when the service restarts. It is a quick
view, not an archive.

## Install for development

The plugin files are in the repository root, so the checkout itself is a valid
plugin folder:

```bash
omarchy plugin validate .
```

The project installer copies the three plugin files into the plugins directory,
validates the copy and enables it:

```bash
./install/omarchy-plugin.sh
```

The manual equivalent, useful while editing QML:

```bash
rm -rf ~/.config/omarchy/plugins/omarchy-ak820-pro-sentinel
mkdir -p ~/.config/omarchy/plugins/omarchy-ak820-pro-sentinel
cp manifest.json Panel.qml Model.js \
  ~/.config/omarchy/plugins/omarchy-ak820-pro-sentinel/
omarchy plugin validate ~/.config/omarchy/plugins/omarchy-ak820-pro-sentinel
omarchy plugin enable omarchy-ak820-pro-sentinel
```

The installer asks for the bar section only when the widget is not in the bar
yet, using the same `gum` prompt as `omarchy plugin add`. For the widget alone,
without touching an existing service:

```bash
./install/omarchy-plugin.sh --no-backend
```

QML edits are not always picked up by a running shell. Restart it when the
panel still shows an old layout:

```bash
omarchy restart shell
```

## Preview without the service

When the status file is absent for three seconds, the panel shows the setup
card. To preview populated rows without root, write a fixture to a temporary
path and temporarily point `statusPath` in the installed copy at that file.
Restore the canonical path before committing changes.

A fixture can be produced by the backend itself:

```bash
PYTHONPATH=backend python3 - <<'PY'
import logging, time
from sentinel.config import load_config
from sentinel.diagnostics import StatusPublisher
from sentinel.events import EventBus, EventType
from sentinel.recovery import RecoveryEngine
from sentinel.state import StateStore
from sentinel.usb_detector import scan_interfaces

config = load_config("/tmp/none.conf", status_file="/tmp/sentinel-status.json")
found = scan_interfaces(
    config.vendor, config.product, None, config.sys_class, config.dev_root
)
store = StateStore(config.vendor, config.product, started_at=0.0,
                   clock_offset=time.time())
recovery = RecoveryEngine(config.backoff, config.stable_seconds)
events = EventBus(logging.getLogger("fixture"), history=config.event_history)
now = 0.0
store.set_receiver(True, now, name="2.4G Dongle", usb_path="1-2.2")
for key, info in found.items():
    store.upsert_interface(info, now)
    store.mark_open(key, 3, now + 1)
    store.mark_stable(key, now + 31)
events.emit(EventType.SERVICE_STARTED, "Sentinel started", version="1.0.0")
publisher = StatusPublisher(config, store, recovery, events, clock=lambda: 60.0)
publisher.publish(60.0, force=True)
PY
```

## Troubleshooting

Check discovery:

```bash
omarchy plugin list
omarchy plugin list --json
```

Check the panel data:

```bash
ak820-pro-sentinel --status --json
```

Watch shell errors:

```bash
journalctl --user -f | grep -i "ak820\|qml"
```

Common problems:

- Setup card: the service is not installed or not running. Click the button, or
  run the installer from the plugin folder in a terminal.
- `NO DATA`: the status file has not appeared yet, or `statusPath` is wrong.
- The widget is absent: the plugin is disabled or the bar layout does not list
  it. Run `omarchy plugin enable omarchy-ak820-pro-sentinel`, then
  `omarchy bar move omarchy-ak820-pro-sentinel --section right` to put it back.
- The panel opens empty: check the shell journal for QML errors after a plugin
  edit, then restart the shell.
- The shield stays muted while the receiver is plugged in: run
  `ak820-pro-sentinel --check` and confirm that the USB IDs match.
