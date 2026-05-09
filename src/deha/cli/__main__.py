"""Narada body CLI — introspection, build, deploy.

Usage:
    python -m deha.cli ping              # quick reachability check
    python -m deha.cli status            # full body state
    python -m deha.cli build             # compile firmware
    python -m deha.cli deploy            # build + OTA flash
    python -m deha.cli logs              # tail device logs
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from deha.proprioception import BodyClient, DEFAULT_DEVICE_IP

FIRMWARE_DIR = Path("embodiment/firmware")
FIRMWARE_YAML = "narada-body.yaml"


def cmd_ping(args) -> int:
    client = BodyClient(device_ip=args.ip)
    if client.ping():
        print(f"OK — {args.ip} is reachable")
        return 0
    print(f"UNREACHABLE — {args.ip} did not respond")
    return 1


def cmd_status(args) -> int:
    client = BodyClient(device_ip=args.ip)
    state = client.status()
    for line in state.display_lines():
        print(line)
    if not state.reachable:
        return 1

    # Also show what the daemon last did, from memory.db
    db_path = Path("data/heartbeat/memory.db")
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT timestamp, action, topic, judgment_approved, result_summary "
                "FROM heartbeats ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                print()
                print("=== Last heartbeat (from memory.db) ===")
                print(f"Time:         {row[0]}")
                print(f"Action:       {row[1]}")
                print(f"Topic:        {row[2]}")
                print(f"Approved:     {bool(row[3])}")
                if row[4]:
                    print(f"Result:       {row[4][:120]}")
        except Exception as e:
            print(f"\n(could not read memory.db: {e})")
    return 0


def _esphome_via_powershell(subcommand: str, extra: list[str] | None = None) -> int:
    """Run an esphome subcommand via PowerShell with MSYS vars cleared.

    PlatformIO's idf_tools.py rejects MSYS2/Git Bash environments outright.
    We documented this on 2026-04-09: must launch via PowerShell with the
    MSYS environment variables set to $null. This wrapper makes that one
    command.
    """
    extra = extra or []
    args_str = " ".join(['"' + a + '"' for a in extra])
    yaml_quoted = f'"{FIRMWARE_YAML}"'
    cwd = Path.cwd() / FIRMWARE_DIR
    if not cwd.exists():
        print(f"firmware directory not found: {cwd}", file=sys.stderr)
        return 2

    ps_command = (
        "$env:MSYSTEM=$null; "
        "$env:MINGW_PREFIX=$null; "
        "$env:MSYS=$null; "
        f'cd "{cwd}"; '
        f"python -m esphome {subcommand} {yaml_quoted} {args_str}"
    )

    print(f">>> esphome {subcommand} {FIRMWARE_YAML}")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_command],
        cwd=str(cwd),
    )
    return result.returncode


def cmd_build(args) -> int:
    return _esphome_via_powershell("compile")


def cmd_deploy(args) -> int:
    """Build and upload firmware. Always compile first then upload — `esphome
    upload` alone will silently send a stale cached binary if the build dir
    already has one (we lost a deploy this way on 2026-04-10). Always pass
    --device explicitly so ESPHome doesn't prompt when both USB and OTA are
    available."""
    rc = _esphome_via_powershell("compile")
    if rc != 0:
        print(f"compile failed (rc={rc}), not uploading", file=sys.stderr)
        return rc
    return _esphome_via_powershell("upload", ["--device", args.ip])


def cmd_logs(args) -> int:
    """Stream device logs. OTA log connection, ctrl-c to stop."""
    return _esphome_via_powershell("logs")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m deha.cli",
        description="Narada body — ESP32 introspection and firmware control",
    )
    parser.add_argument(
        "--ip", default=DEFAULT_DEVICE_IP,
        help=f"Device IP (default: {DEFAULT_DEVICE_IP})",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping", help="Quick HTTP reachability check")
    sub.add_parser("status", help="Full body state report")
    sub.add_parser("build", help="Compile firmware (no flash)")
    sub.add_parser("deploy", help="Compile + OTA flash firmware")
    sub.add_parser("logs", help="Stream device logs")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    handlers = {
        "ping": cmd_ping,
        "status": cmd_status,
        "build": cmd_build,
        "deploy": cmd_deploy,
        "logs": cmd_logs,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
