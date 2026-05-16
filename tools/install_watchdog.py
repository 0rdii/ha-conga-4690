#!/usr/bin/env python3
"""Install the Conga cloud watchdog on a robot.

The script intentionally does not hard-code an IP address or password. Run it
from a trusted machine on the same network as the robot:

    python tools/install_watchdog.py --host 192.168.1.75
"""

from __future__ import annotations

import argparse
from getpass import getpass
import subprocess
import sys


WATCHDOG_SCRIPT = r"""#!/bin/sh
LOG=/tmp/conga_cloud_watchdog.log
APP_LOG=/mnt/UDISK/log/app_logfile.temp
INTERVAL=60
BAD_LIMIT=3
STALE_LOG_LIMIT=600
bad=0

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

robot_state() {
    pid="$(pidof RobotApp | awk '{print $1}')"
    [ -n "$pid" ] || {
        echo "missing"
        return
    }
    awk '/^State:/ {print $2}' "/proc/$pid/status" 2>/dev/null
}

cloud_ok() {
    netstat -antup 2>/dev/null | grep 'RobotApp' | grep ':9090' | grep -q 'ESTABLISHED'
}

cloud_bad() {
    netstat -antup 2>/dev/null | grep 'RobotApp' | grep ':9090' | grep -q 'CLOSE_WAIT'
}

app_log_stale() {
    [ -f "$APP_LOG" ] || return 1
    now="$(date +%s 2>/dev/null)"
    mtime="$(stat -c %Y "$APP_LOG" 2>/dev/null)"
    [ -n "$now" ] && [ -n "$mtime" ] || return 1
    age=$((now - mtime))
    [ "$age" -ge "$STALE_LOG_LIMIT" ]
}

recover_robotapp() {
    reason="$1"
    pid="$(pidof RobotApp | awk '{print $1}')"
    log "recovering RobotApp: $reason pid=$pid"
    [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null || true
    sleep 30
}

robot_cleaning() {
    [ -f "$APP_LOG" ] || return 1

    latest_mode="$(tail -n 400 "$APP_LOG" 2>/dev/null | grep 'systemSleepControl current_app_mode' | tail -n 1)"

    case "$latest_mode" in
        *APP_MODE_AUTO*|*APP_MODE_PART*|*APP_MODE_SELECT*|*APP_MODE_ROOM*|*APP_MODE_ZONE*)
            return 0
            ;;
        *APP_MODE_IDLE*|*APP_MODE_CHARGE*|*APP_MODE_GO_HOME*|*APP_MODE_SLEEP*)
            return 1
            ;;
    esac

    tail -n 120 "$APP_LOG" 2>/dev/null | grep -q 'clean_roomId [1-9]'
}

log "watchdog started interval=${INTERVAL}s bad_limit=${BAD_LIMIT} stale_log_limit=${STALE_LOG_LIMIT}s"

while true; do
    state="$(robot_state)"

    if cloud_ok; then
        bad=0
    elif [ "$state" = "D" ] || [ "$state" = "Z" ] || cloud_bad || app_log_stale; then
        bad=$((bad + 1))
        log "bad cloud state count=$bad robot_state=$state"
    else
        bad=0
    fi

    if [ "$bad" -ge "$BAD_LIMIT" ]; then
        if robot_cleaning; then
            log "cloud failure detected but robot is cleaning; skipping reboot"
            bad=0
            sleep "$INTERVAL"
            continue
        fi
        recover_robotapp "persistent cloud failure"
        bad=0
    fi

    sleep "$INTERVAL"
done
"""


INIT_SCRIPT = r"""#!/bin/sh /etc/rc.common
START=99
STOP=10

start() {
    start-stop-daemon -S -b -m -p /tmp/conga_cloud_watchdog.pid -x /bin/sh -- /mnt/UDISK/bin/conga_cloud_watchdog.sh
}

stop() {
    [ -f /tmp/conga_cloud_watchdog.pid ] && kill "$(cat /tmp/conga_cloud_watchdog.pid)" 2>/dev/null || true
    rm -f /tmp/conga_cloud_watchdog.pid
    pkill -f conga_cloud_watchdog.sh 2>/dev/null || true
}
"""


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


def write_file(ssh, path: str, content: str, mode: str = "755") -> None:
    quoted_path = path.replace("'", "'\"'\"'")
    channel = ssh.get_transport().open_session()
    channel.exec_command(f"cat > '{quoted_path}' && chmod {mode} '{quoted_path}'")
    channel.sendall(content.encode("utf-8"))
    channel.shutdown_write()
    status = channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(f"failed to write {path}: exit {status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Conga cloud watchdog")
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
        print(run(ssh, "mkdir -p /mnt/UDISK/bin"))
        write_file(ssh, "/mnt/UDISK/bin/conga_cloud_watchdog.sh", WATCHDOG_SCRIPT)
        write_file(ssh, "/etc/init.d/conga_cloud_watchdog", INIT_SCRIPT)
        print(run(ssh, "/etc/init.d/conga_cloud_watchdog stop >/dev/null 2>&1 || true"))
        print(run(ssh, "/etc/init.d/conga_cloud_watchdog enable"))
        print(run(ssh, "/etc/init.d/conga_cloud_watchdog start"))
        print(run(ssh, "ps | grep conga_cloud_watchdog | grep -v grep || true"))
    finally:
        ssh.close()

    print("Watchdog installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
