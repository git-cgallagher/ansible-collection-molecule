"""One poll of a windows_vm's first boot, for ready.yml's until loop.

The box's first boot runs OOBE, rearms the evaluation licence, reboots, then
recreates the WinRM listeners and finally deletes C:\\temp\\setup.ps1. Each
poll runs in a fresh process under a timeout: a WinRM call caught by that
reboot can hang on a half-open connection. It checks the VM before WinRM, so
a VM that can't finish ends the wait at once:

    10  QEMU is gone: its pidfile names no live process
    11  the guest stopped (powered off, which -no-shutdown keeps QEMU up for,
        or paused on an error); QEMU's status goes to stdout
    12  the scenario's first-boot deadline has passed
     0  the first boot has finished
     3  not yet: WinRM didn't answer, or the box is still busy

    python3 first_boot_poll.py --host 127.0.0.1 --port 15985 --user vagrant \\
        --password vagrant --pidfile dc01.pid --monitor dc01-monitor.sock \\
        --deadline 1762041600
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time

READY_CHECK = "if (Test-Path -LiteralPath 'C:\\temp\\setup.ps1') { 'busy' } else { 'ready' }"


def qemu_alive(pidfile: str) -> bool:
    try:
        with open(pidfile, encoding="ascii") as handle:
            pid = int(handle.read().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, and someone else's: not ours to judge.
        return True
    return True


def monitor_status(path: str, timeout: float = 5.0) -> str | None:
    """QEMU's run state from its human monitor ("running", "shutdown",
    "paused (internal-error)", ...), or None if the monitor didn't answer."""
    reply = b""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            banner = b""
            while b"(qemu)" not in banner:
                chunk = sock.recv(4096)
                if not chunk:
                    return None
                banner += chunk
            sock.sendall(b"info status\n")
            # The reply echoes the command, then "VM status: ...", then a new
            # prompt.
            while not (b"VM status:" in reply and reply.rstrip().endswith(b"(qemu)")):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                reply += chunk
    except OSError:
        if b"VM status:" not in reply:
            return None
    for line in reply.decode("utf-8", "replace").splitlines():
        if "VM status:" in line:
            return line.split("VM status:", 1)[1].strip()
    return None


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def first_boot_done(args: argparse.Namespace) -> bool:
    # Imported here: the VM checks above don't need pypsrp.
    from pypsrp.client import Client  # pylint: disable=import-outside-toplevel

    client = Client(args.host, port=args.port, username=args.user, password=args.password,
                    ssl=False, auth="negotiate", connection_timeout=10)
    out, _streams, _had_errors = client.execute_ps(READY_CHECK)
    return out.strip() == "ready"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--pidfile", required=True, help="QEMU's -pidfile")
    parser.add_argument("--monitor", required=True, help="QEMU's -monitor unix socket")
    parser.add_argument("--deadline", type=float, required=True, help="seconds since the epoch")
    args = parser.parse_args()

    if not qemu_alive(args.pidfile):
        print(f"QEMU is gone (no live process in {args.pidfile})")
        return 10
    status = monitor_status(args.monitor)
    if status is not None and not status.startswith("running"):
        print(status)
        return 11
    if time.time() > args.deadline:
        print(f"QEMU status: {status or 'no answer from the monitor'}; WinRM port {args.port} "
              f"{'accepts connections but the box never reported ready' if port_open(args.host, args.port) else 'is closed'}")
        return 12
    try:
        done = first_boot_done(args)
    # Anything WinRM raises (refused, reset, timed out, auth) means "not yet".
    except Exception as error:  # pylint: disable=broad-exception-caught
        print(f"not yet: {type(error).__name__}: {error}")
        return 3
    if done:
        return 0
    print("not yet: the box is still busy (C:\\temp\\setup.ps1 exists)")
    return 3


if __name__ == "__main__":
    sys.exit(main())
