#!/usr/bin/env python3
"""Install the Conga cloud watchdog on a robot.

The script does not hard-code an IP address. If no password is entered it tries
the known factory default passwords. Run it from a trusted machine on the same
network as the robot:

    python tools/install_watchdog.py --host 192.168.1.75
"""

from __future__ import annotations

import argparse
from getpass import getpass
import subprocess
import sys


WATCHDOG_SCRIPT = r"""#!/bin/sh
# Persistent storage, so the log survives the reboots it may trigger.
LOG=/mnt/UDISK/log/conga_cloud_watchdog.log
LOG_MAX_BYTES=200000
APP_LOG=/mnt/UDISK/log/app_logfile.temp
INTERVAL=60
BAD_LIMIT=3
STALE_LOG_LIMIT=600
RESTART_WAIT=60
MAX_RECOVERIES_PER_HOUR=3
bad=0
recoveries=0
window_start="$(date +%s 2>/dev/null)"

log() {
    mkdir -p "$(dirname "$LOG")"
    size="$(wc -c < "$LOG" 2>/dev/null)"
    if [ -n "$size" ] && [ "$size" -ge "$LOG_MAX_BYTES" ]; then
        mv -f "$LOG" "$LOG.1"
    fi
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

recovery_allowed() {
    now="$(date +%s 2>/dev/null)"
    if [ -n "$now" ] && [ -n "$window_start" ] && [ $((now - window_start)) -ge 3600 ]; then
        window_start="$now"
        recoveries=0
    fi
    [ "$recoveries" -lt "$MAX_RECOVERIES_PER_HOUR" ]
}

recover_robotapp() {
    reason="$1"
    recoveries=$((recoveries + 1))
    pid="$(pidof RobotApp | awk '{print $1}')"
    log "recovering RobotApp ($recoveries/$MAX_RECOVERIES_PER_HOUR this hour): $reason pid=$pid"
    [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null || true

    # The firmware is expected to respawn RobotApp. If it does not, the robot
    # would stay without its main program, so reboot instead.
    waited=0
    while [ "$waited" -lt "$RESTART_WAIT" ]; do
        sleep 5
        waited=$((waited + 5))
        if pidof RobotApp >/dev/null 2>&1; then
            log "RobotApp running again after ${waited}s"
            return
        fi
    done
    log "RobotApp did not restart within ${RESTART_WAIT}s; rebooting"
    sync
    reboot
}

# Only recover when the robot is clearly docked or idle. Cleaning, returning,
# stuck or in error, or an unknown state (no app log) all mean "leave it alone".
robot_safe_to_recover() {
    [ -f "$APP_LOG" ] || return 1

    latest_mode="$(tail -n 400 "$APP_LOG" 2>/dev/null | grep 'systemSleepControl current_app_mode' | tail -n 1)"

    case "$latest_mode" in
        *APP_MODE_IDLE*|*APP_MODE_CHARGE*|*APP_MODE_SLEEP*)
            tail -n 120 "$APP_LOG" 2>/dev/null | grep -q 'clean_roomId [1-9]' && return 1
            return 0
            ;;
    esac
    return 1
}

log "watchdog started interval=${INTERVAL}s bad_limit=${BAD_LIMIT} stale_log_limit=${STALE_LOG_LIMIT}s max_recoveries_per_hour=${MAX_RECOVERIES_PER_HOUR}"

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
        if ! robot_safe_to_recover; then
            log "cloud failure detected but robot is not docked/idle; skipping recovery"
        elif ! recovery_allowed; then
            log "cloud failure detected but recovery limit reached this hour; skipping"
        else
            recover_robotapp "persistent cloud failure"
        fi
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


# The robot's dropbear only offers ssh-rsa host keys, which recent paramiko
# releases (5.x) no longer support.
PARAMIKO_REQUIREMENT = "paramiko<4"

# Known factory default root passwords, also listed in the README.
DEFAULT_PASSWORDS = ("@3I#sc$RD%xm^2S&", "3irobotics")


def ensure_paramiko():
    try:
        import paramiko  # type: ignore

        if "ssh-rsa" in getattr(paramiko.Transport, "_key_info", {}):
            return paramiko
        print(f"paramiko {paramiko.__version__} cannot connect to the robot (no ssh-rsa support).")
    except ImportError:
        print("Missing dependency: paramiko")

    print(f"Installing {PARAMIKO_REQUIREMENT} with pip...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", PARAMIKO_REQUIREMENT])
    except Exception as exc:
        print(f"Automatic install failed: {exc}")
        print(f'Install it manually with: python -m pip install --user "{PARAMIKO_REQUIREMENT}"')
        sys.exit(1)

    # Start again in a fresh interpreter so the newly installed version is used.
    sys.exit(subprocess.call([sys.executable] + sys.argv))


WATCHDOG_STATUS_COMMAND = r"""
sleep 2
pid="$(cat /tmp/conga_cloud_watchdog.pid 2>/dev/null)"
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "Watchdog running (pid $pid)"
else
    echo "WARNING: watchdog is not running"
fi
tail -n 3 /mnt/UDISK/log/conga_cloud_watchdog.log 2>/dev/null
"""


def connect(paramiko, host: str, user: str, password: str):
    """Log in with the given password, or with the factory defaults if empty."""
    candidates = [password] if password else list(DEFAULT_PASSWORDS)
    for candidate in candidates:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(host, username=user, password=candidate, timeout=10, look_for_keys=False, allow_agent=False)
            return ssh
        except paramiko.AuthenticationException:
            ssh.close()
    print("Authentication failed: wrong password, or this IP is not the robot.")
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
    parser.add_argument("--password", help="SSH password; asked interactively if omitted")
    args = parser.parse_args()

    paramiko = ensure_paramiko()

    host = args.host or input("Robot IP/hostname: ").strip()
    password = args.password
    if password is None:
        password = getpass(f"SSH password for {args.user}@{host} (Enter = try factory defaults): ")
    ssh = connect(paramiko, host, args.user, password)

    try:
        print(run(ssh, "mkdir -p /mnt/UDISK/bin"))
        write_file(ssh, "/mnt/UDISK/bin/conga_cloud_watchdog.sh", WATCHDOG_SCRIPT)
        write_file(ssh, "/etc/init.d/conga_cloud_watchdog", INIT_SCRIPT)
        print(run(ssh, "/etc/init.d/conga_cloud_watchdog stop >/dev/null 2>&1 || true"))
        print(run(ssh, "/etc/init.d/conga_cloud_watchdog enable"))
        print(run(ssh, "/etc/init.d/conga_cloud_watchdog start"))
        # Plain `ps` on the robot only lists processes of the current session,
        # so check the daemon through its pid file instead.
        print(run(ssh, WATCHDOG_STATUS_COMMAND))
    finally:
        ssh.close()

    print("Watchdog installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
