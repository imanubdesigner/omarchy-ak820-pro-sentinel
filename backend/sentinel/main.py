"""Command line entry point for AK820 Pro Sentinel."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from typing import Any

from . import APP_NAME, __version__
from .config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    load_config,
)
from .diagnostics import (
    StatusPublisher,
    collect,
    environment_info,
    format_summary,
    read_status,
)
from .events import EventBus, EventType, isoformat
from .hid_monitor import HidMonitor
from .recovery import RecoveryEngine
from .state import StateStore

LOG = logging.getLogger("ak820-pro-sentinel")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ak820-pro-sentinel",
        description=(
            "Read only watchdog for the Ajazz AK820 Pro 2.4 GHz USB receiver. "
            "It monitors HID interfaces and reopens descriptors that became stale."
        ),
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"configuration file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("--vendor", help="override the USB vendor ID")
    parser.add_argument("--product", help="override the USB product ID")
    parser.add_argument(
        "--interfaces",
        help='interface numbers to monitor, for example "0,1,2", or "all"',
    )
    parser.add_argument(
        "--rescan-interval",
        type=float,
        help="seconds between sysfs scans (default: 3.0)",
    )
    parser.add_argument(
        "--stable-seconds",
        type=float,
        help="seconds a connection must stay up before the retry counter resets",
    )
    parser.add_argument(
        "--backoff",
        help='recovery backoff in seconds, for example "1,2,4,8,15,30"',
    )
    parser.add_argument("--status-file", help="path of the JSON status snapshot")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="journal verbosity",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase verbosity, repeat for debug reports",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="log warnings and errors only",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="print environment diagnostics as JSON and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print the current status snapshot and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --status, print the raw JSON snapshot",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the configuration and scan the receiver once, then exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {__version__}",
    )
    return parser


def _config_from_args(args: argparse.Namespace):
    overrides: dict[str, Any] = {
        "vendor": args.vendor,
        "product": args.product,
        "interfaces": args.interfaces,
        "rescan_interval": args.rescan_interval,
        "stable_seconds": args.stable_seconds,
        "backoff": args.backoff,
        "status_file": args.status_file,
        "log_level": args.log_level,
    }
    return load_config(args.config, **overrides)


def _log_level(args: argparse.Namespace, configured: str) -> str:
    if args.log_level:
        return args.log_level
    if args.quiet:
        return "WARNING"
    if args.verbose >= 2:
        return "DEBUG"
    if args.verbose == 1 and configured == "INFO":
        return "DEBUG"
    return configured


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _run_check(config) -> int:
    info = environment_info(config)
    matches = info.get("matching_interfaces", [])
    print(f"{APP_NAME} {__version__}")
    print(f"config: {config.config_path}")
    print(f"service: {info['service']}")
    print(f"receiver: {'detected' if matches else 'not detected'}")
    if info.get("scan_error"):
        print(f"scan error: {info['scan_error']}")
    for item in matches:
        print(
            f"  interface {item['interface']}: {item['device']} "
            f"({item['hid_name'] or 'HID'}, {item['hid_id']})"
        )
    if not matches:
        print("No matching HID interface is available right now.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = _config_from_args(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    level = _log_level(args, config.log_level)
    _configure_logging(level)

    if args.diagnostics:
        _print_json(collect(config))
        return 0

    if args.status:
        snapshot = read_status(config.status_file)
        if args.json:
            _print_json(snapshot if snapshot is not None else {})
        else:
            print(format_summary(snapshot))
        return 0 if snapshot is not None else 1

    if args.check:
        return _run_check(config)

    events = EventBus(
        LOG, history=config.event_history, stats_path=config.stats_file or None
    )
    store = StateStore(config.vendor, config.product)
    recovery = RecoveryEngine(config.backoff, config.stable_seconds)
    publisher = StatusPublisher(config, store, recovery, events)
    monitor = HidMonitor(
        config,
        store,
        events,
        recovery,
        on_change=lambda: publisher.publish(monitor.clock(), force=True),
        on_tick=publisher.publish,
    )

    def refresh_status(_event: Any) -> None:
        publisher.publish(monitor.clock(), force=True)

    # Any event immediately refreshes the snapshot, so the panel reacts fast.
    events.subscribe(refresh_status)

    stopping = {"value": False}

    def handle_signal(signum: int, _frame: Any) -> None:
        if stopping["value"]:
            return
        stopping["value"] = True
        LOG.info("received signal %s, stopping", signum)
        events.emit(EventType.SERVICE_STOPPING, "Sentinel stopping", signal=signum)
        monitor.request_stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    events.emit(
        EventType.CONFIG_LOADED,
        "configuration loaded",
        path=config.config_path,
        vendor_id=config.vendor,
        product_id=config.product,
        rescan_interval=config.rescan_interval,
        stable_seconds=config.stable_seconds,
    )
    events.emit(
        EventType.SERVICE_STARTED,
        "Sentinel started",
        pid=os.getpid(),
        version=__version__,
    )
    publisher.publish(monitor.clock(), force=True)

    exit_code = 0
    try:
        monitor.run()
    except Exception as exc:
        LOG.exception("Sentinel terminated by an unexpected error")
        events.emit(EventType.MONITOR_ERROR, "unexpected termination", error=str(exc))
        exit_code = 1
    finally:
        now = monitor.clock()
        events.unsubscribe(refresh_status)
        events.emit(EventType.SERVICE_STOPPED, "Sentinel stopped", pid=os.getpid())
        publisher.publish(
            now,
            force=True,
            extra={
                "running": False,
                "stopped_at": isoformat(now + store.clock_offset),
            },
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
