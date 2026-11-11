# AK820 Pro Sentinel

**Prevents the Ajazz AK820 Pro from disconnecting on Linux. A read-only
watchdog for the 2.4 GHz receiver, with an Omarchy bar widget.**

On Linux the AK820 Pro 2.4 GHz receiver can drop out and stop responding until
you unplug and replug it. Sentinel keeps a read handle open on every HID
interface of the receiver and reopens it when the descriptor goes stale, which
is what keeps the keyboard connected. It never sends anything to the keyboard.

The backend runs as a systemd service. The Omarchy widget only reads the JSON
status file, so the graphical part never needs root privileges.

![AK820 Pro Sentinel panel in the Omarchy bar](preview.png)

## Jump to

- [Install](#install)
  - [From the Omarchy marketplace](#from-the-omarchy-marketplace)
  - [Full install](#full-install)
  - [Update](#update)
- [Using the widget](#using-the-widget)
- [Event log](#event-log)
- [Status file](#status-file)
- [Commands](#commands)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [How it works](#how-it-works)
- [Limitations](#limitations)
- [Development](#development)
- [License and credits](#license-and-credits)

## Install

Sentinel has two parts:

- the systemd service, which is what keeps the receiver alive;
- the Omarchy bar widget, which shows the state.

The widget on its own cannot start the service, because it runs as your desktop
user. When the service is missing, the panel shows a setup card with an install
button instead of a status. Install once, then the panel reads the status file
written by the service.

### Requirements

- Arch Linux with Omarchy, for the bar widget.
- systemd and Python 3.10 or newer.
- The Ajazz AK820 Pro 2.4 GHz receiver, USB ID `0c45:fdfd`.
- Root for the service, requested by the installer through sudo.

### From the Omarchy marketplace

Add the plugin the way every Omarchy plugin is added:

```bash
omarchy plugin add https://github.com/imanubdesigner/omarchy-ak820-pro-sentinel.git --enable
```

Omarchy asks which bar section to place the widget in: **left**, **center** or
**right**. This is the standard prompt used for every plugin in the
marketplace, and `right` is preselected by the manifest.

The service needs root to open the HID interfaces, so a shell plugin cannot
install it during `plugin add`. On the first open the panel shows a setup card
instead of a status:

```text
SERVICE NOT INSTALLED
The bar widget cannot keep the receiver awake on its own.

[ INSTALL IN A TERMINAL ]
```

Click **INSTALL IN A TERMINAL**. The installer opens in an Omarchy terminal,
shows what it will do and asks for your password once. When it finishes, the
panel picks up the status file within two seconds and the card is replaced by
the normal view. No shell restart is needed.

If you prefer the terminal, the same two steps are one line:

```bash
omarchy plugin add https://github.com/imanubdesigner/omarchy-ak820-pro-sentinel.git --enable \
  && sudo ~/.config/omarchy/plugins/omarchy-ak820-pro-sentinel/install/install.sh
```

If the service is already installed and only the widget is missing, the same
command installs the widget and the setup card never appears.

### Full install

Download the ZIP from the latest release, extract it and open a terminal in the
project directory. Run the installer as your desktop user, without sudo:

```bash
./install/omarchy-plugin.sh
```

If your extraction tool removed the executable bit, restore it once:

```bash
chmod +x install/*.sh
```

It installs everything:

- the backend in `/opt/ak820-pro-sentinel`;
- the wrapper `/usr/local/bin/ak820-pro-sentinel`;
- the uninstaller `/usr/local/bin/ak820-pro-sentinel-remove`;
- `/etc/ak820-pro-sentinel.conf`, only if it does not exist yet;
- `ak820-pro-sentinel.service`, started now and enabled at boot;
- the widget in `~/.config/omarchy/plugins/omarchy-ak820-pro-sentinel`.

On the first install Omarchy asks which bar section to place the widget in:
**left**, **center** or **right**, with `right` preselected from the manifest.
A reinstall or an update never moves a widget that is already in the bar, so
the section you picked stays.

The backend needs root, so the script asks for your password once through
sudo. It does not stop, disable or remove `ak820-linux-lifeline.service`.

If the widget does not appear, refresh the shell:

```bash
omarchy restart shell
```

### Update

```bash
omarchy plugin update omarchy-ak820-pro-sentinel
```

This replaces the widget files and keeps the bar section you picked. If the
release also changed the service, run the installer from the updated plugin
folder once:

```bash
sudo ~/.config/omarchy/plugins/omarchy-ak820-pro-sentinel/install/install.sh
```

The installer keeps `/etc/ak820-pro-sentinel.conf`, restarts the service and
leaves the bar layout alone.

### Check the result

```bash
ak820-pro-sentinel --check
systemctl status ak820-pro-sentinel.service
```

### Install only one part

Backend and service only, without the widget:

```bash
sudo ./install/install.sh
```

Widget only, keeping an existing service:

```bash
./install/omarchy-plugin.sh --no-backend
```

## Using the widget

The bar shows a shield icon. Its color comes from the active Omarchy theme and
follows theme changes. It stays in the accent color while everything is
healthy, turns to the theme urgent color when the receiver, an interface or
the service needs attention, and stays muted for a clean stop or missing data.
Hovering the shield shows a sentence such as `AK820 Pro connected`.

The panel repeats that sentence under its title, so the product name and the
state are visible without hovering.

Click the shield to open the panel. The panel shows:

- the overall state: connected, recovering, disconnected, stale, error,
  stopped or no data;
- the USB receiver and its vendor and product IDs;
- how many HID interfaces are open;
- the recovery engine and the next retry, when one is pending;
- the last event and the service uptime.

The footer reminds you that Sentinel monitors the USB receiver only. It cannot
confirm the wireless link by itself.

## Event log

The `LOG` button in the panel header opens a short event log. It shows only
significant events: receiver removed or detected, interface disconnected,
errors, recovery started, link recovered, and the service start or stop.
Routine start-up events are left out, and events that happen together on the
five HID interfaces are grouped, for example `Interface disconnected x5`.

The log keeps the last 20 significant events in memory and is cleared when the
service restarts. It is a quick way to see what happened, not a persistent
archive. The full event stream remains in the systemd journal.

At the top of the log there is a session line, refreshed every second:

```text
5 HELD OPEN · UP 2h 34m · 0 RECOVERIES
```

It is the proof that the service is holding the interfaces open right now: the
count of open handles, how long the service has been running and how many
recoveries it already performed. The recovery count switches to the accent
color once it is above zero. The line is not an event, so it never takes a row
from the log.

## Status file

The service writes `/run/ak820-pro-sentinel/status.json`. The file is written
atomically and has mode `0644`, so the panel can read it as a normal user.

Main fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Version of the JSON contract. Currently `1`. |
| `app`, `version` | Application name and version. |
| `started_at`, `updated_at` | ISO 8601 timestamps. |
| `uptime_seconds` | Service uptime. |
| `running` | `true` while the service is alive, `false` after a clean stop. |
| `receiver` | Presence and identity of the USB receiver. |
| `interfaces` | One object per HID interface, with state, attempts and errors. |
| `summary` | Counts for total, open, stable, recovering and error interfaces. |
| `recovery` | Backoff configuration and pending retry attempts. |
| `last_event`, `event_log` | Last event and the recent significant events. |
| `session` | Recoveries and disconnects since the service started, with the last recovery time. |
| `pid`, `hostname`, `config_path` | Runtime details useful for debugging. |

Read it directly with `ak820-pro-sentinel --status --json`, or use any JSON
tool.

## Commands

```bash
ak820-pro-sentinel --check          # validate config and scan the receiver once
ak820-pro-sentinel --status         # short human readable summary
ak820-pro-sentinel --status --json  # raw status snapshot
ak820-pro-sentinel --diagnostics    # environment, sysfs and status details as JSON
ak820-pro-sentinel --version
```

The commands do not need root for reading. Only the service needs root to open
the HID devices.

## Configuration

The default file is `/etc/ak820-pro-sentinel.conf`. The installer creates it on
first install and leaves it alone on upgrades.

| Section | Key | Default | Notes |
| --- | --- | --- | --- |
| `device` | `vendor` | `0c45` | USB vendor ID. |
| `device` | `product` | `fdfd` | USB product ID. |
| `device` | `interfaces` | `all` | `all`, or a list such as `0,1,2`. |
| `monitor` | `rescan_interval` | `3.0` | Seconds between sysfs scans. |
| `monitor` | `settling_interval` | `0.4` | Maximum wait for one poll. |
| `monitor` | `stable_seconds` | `30.0` | Time before the retry counter resets. |
| `recovery` | `backoff` | `1,2,4,8,15,30` | Delay before each attempt, in seconds. |
| `status` | `file` | `/run/ak820-pro-sentinel/status.json` | Must be an absolute path. |
| `status` | `interval` | `1.0` | Refresh interval when nothing changes. |
| `status` | `event_history` | `20` | Number of significant events kept in the panel log. |
| `logging` | `level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `paths` | `sys_class` | `/sys/class/hidraw` | sysfs class used for discovery. |
| `paths` | `dev_root` | `/dev` | Directory containing the device nodes. |

After editing the file, restart the service:

```bash
sudo systemctl restart ak820-pro-sentinel.service
```

Every setting can be changed in the file. Some of them also have command line
flags, for example:

```bash
ak820-pro-sentinel --config /etc/ak820-pro-sentinel.conf \
  --vendor 0c45 --product fdfd --interfaces all \
  --rescan-interval 3 --stable-seconds 30 \
  --backoff 1,2,4,8,15,30 --status-file /tmp/status.json --log-level DEBUG
```

## Troubleshooting

Follow the journal:

```bash
journalctl -u ak820-pro-sentinel.service -f
```

### The service does not start

```bash
systemctl status ak820-pro-sentinel.service
journalctl -u ak820-pro-sentinel.service -n 50
```

A configuration error stops the service before it touches any device. The
error message names the invalid key.

### The receiver is detected but no interface is opened

Run:

```bash
ak820-pro-sentinel --check
ak820-pro-sentinel --diagnostics
```

Check that the expected nodes exist with `ls -l /dev/hidraw*` and that the
USB IDs match. The service runs as root, so node permissions do not affect the
service itself.

### The widget says NO DATA

The panel reads the status file. If the file is missing, the service is not
running or has not written its first snapshot yet. When the panel also shows
the setup card, the service is not installed at all: click **INSTALL IN A
TERMINAL**, or run:

```bash
sudo ~/.config/omarchy/plugins/omarchy-ak820-pro-sentinel/install/install.sh
```

If the service is installed, check it directly:

```bash
ak820-pro-sentinel --status
systemctl status ak820-pro-sentinel.service
```

### The widget says STALE

The file exists but has not been refreshed for more than five seconds, and it
does not say `running: false`. The service probably died without a clean stop:

```bash
systemctl status ak820-pro-sentinel.service
journalctl -u ak820-pro-sentinel.service -n 50
```

### The widget is missing from the bar

```bash
omarchy plugin list
omarchy plugin enable omarchy-ak820-pro-sentinel
omarchy bar move omarchy-ak820-pro-sentinel --section right
omarchy restart shell
```

### The keyboard still loses its connection

Sentinel only repairs HID descriptors. If the wireless link itself drops, the
receiver may keep its nodes while the keyboard is unreachable. Sentinel cannot
test or repair that link, and it does not reset the USB device on purpose.

### The old lifeline service is still active

Sentinel leaves it untouched. Avoid running both daemons during real use. Once
Sentinel is validated, stop and disable the old service yourself:

```bash
sudo systemctl disable --now ak820-linux-lifeline.service
```

Do not remove the old service until you are satisfied with Sentinel.

## Uninstall

Remove the widget with the standard command:

```bash
omarchy plugin remove omarchy-ak820-pro-sentinel
```

Then remove the service and its files:

```bash
sudo ak820-pro-sentinel-remove
```

The uninstaller stops and disables the service, then removes the wrapper,
`/opt/ak820-pro-sentinel`, `/usr/local/lib/ak820-pro-sentinel`,
`/usr/local/bin/ak820-pro-sentinel-remove` and `/etc/ak820-pro-sentinel.conf`.

If you installed from the ZIP, the same uninstaller is available in the
checkout:

```bash
sudo ./install/uninstall.sh
```

To remove only one part:

```bash
./install/omarchy-plugin.sh remove --no-backend   # widget only
sudo ./install/uninstall.sh                       # backend only
```

None of these commands touch `ak820-linux-lifeline`.

## How it works

### What it does

- Finds the receiver by USB ID `0c45:fdfd`.
- Opens every matching `/dev/hidraw*` node with `O_RDONLY`.
- Waits for report activity with `select()` and reads incoming reports.
- Closes descriptors whose node disappeared or was replaced.
- Reopens them with a progressive backoff of 1, 2, 4, 8, 15 and then 30
  seconds.
- Resets the retry counter only after a connection has stayed up for 30
  seconds.
- Writes an atomic JSON snapshot for the panel.
- Logs every state change to the systemd journal.

### What it does not do

- It does not send HID output reports.
- It does not send firmware or configuration commands.
- It does not reset, unbind or rebind USB devices.
- It does not change kernel modules, udev rules or USB autosuspend settings.
- It does not write anything to the receiver.
- It does not verify the radio link. A `hidraw` node proves that the USB
  receiver is present, not that the wireless connection to the keyboard works.

## Limitations

- Validated on Arch Linux with Omarchy on the development machine, with the
  receiver at `0c45:fdfd` and its five HID interfaces. Other models and
  receiver revisions have not been tested.
- Interface identity follows the USB path and the interface number. Moving
  the receiver to another port starts a fresh state for the new path.
- The panel reads the status file directly. It does not query the service over
  IPC, and it shows the setup card when the file is absent.
- The default USB IDs target the AK820 Pro. Other receivers need a different
  configuration.

## Development

### Tests

The tests do not need the receiver and do not touch the real sysfs:

```bash
python3 -m unittest discover -s tests -v
node tests/test_plugin_model.js
```

They cover configuration parsing, device discovery, descriptor lifecycle,
stale handle cleanup, recovery backoff, event history, session counters, the
status snapshot and the panel model, including the event log rows and the
session line.

### Project layout

```text
manifest.json       plugin metadata and bar entry point
Panel.qml           bar shield and popup panel
Model.js            status parsing, state mapping, labels and log rows
backend/sentinel/   backend modules and command line entry point
install/            backend installer, uninstaller and widget installer
systemd/            systemd unit
tests/              dependency-free unit tests
docs/omarchy-ui.md  widget architecture and troubleshooting notes
```

The plugin files sit at the repository root, like every plugin in the Omarchy
marketplace. The backend and the installers live beside them and are ignored by
the shell.

## License and credits

MIT. See `LICENSE`.

The original v0.1.0 baseline was based on `microdia-fix` by Artem K. See
[NOTICE](NOTICE) for the attribution.
