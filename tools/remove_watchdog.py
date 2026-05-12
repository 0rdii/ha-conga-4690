#!/usr/bin/env python3
"""Remove the Conga cloud watchdog from a robot.

The script intentionally does not hard-code an IP address or password:

    python tools/remove_watchdog.py --host 192.168.1.75
"""

from __future__ import annotations

import argparse
from getpass import getpass
import subprocess
import sys


def ensure_paramiko():
    try:
        import paramiko  # type: ignore

        return paramiko
    except ImportError:
        print("Missing dependency: paramiko")
        print("Trying to install it automatically with pip...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "paramiko"])
            import paramiko  # type: ignore

            return paramiko
        except Exception as exc:
            print(f"Automatic install failed: {exc}")
            print("Install it manually with: python -m pip install --user paramiko")
            sys.exit(1)


def run(ssh, command: str) -> str:
    _, stdout, stderr = ssh.exec_command(command, timeout=30)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if err:
        out += err
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove Conga cloud watchdog")
    parser.add_argument("--host", help="Robot IP or hostname")
    parser.add_argument("--user", default="root", help="SSH user, default: root")
    args = parser.parse_args()

    host = args.host or input("Robot IP/hostname: ").strip()
    password = getpass(f"SSH password for {args.user}@{host}: ")

    paramiko = ensure_paramiko()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(host, username=args.user, password=password, timeout=10, look_for_keys=False, allow_agent=False)
        command = r"""
/etc/init.d/conga_cloud_watchdog stop >/dev/null 2>&1 || true
/etc/init.d/conga_cloud_watchdog disable >/dev/null 2>&1 || true
rm -f /etc/init.d/conga_cloud_watchdog
rm -f /etc/rc.d/*conga_cloud_watchdog*
rm -f /mnt/UDISK/bin/conga_cloud_watchdog.sh
rm -f /tmp/conga_cloud_watchdog.pid
ps | grep conga_cloud_watchdog | grep -v grep || true
"""
        print(run(ssh, command))
    finally:
        ssh.close()

    print("Watchdog removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
