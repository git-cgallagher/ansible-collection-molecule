"""Runs a windows_vm's QEMU and records how it stopped.

QEMU's -daemonize points its stdout and stderr at /dev/null once it detaches,
and nothing waits for its exit status, so a VM that dies leaves no trace. This
runs QEMU as the child of a detached supervisor instead: QEMU's output goes to
the log, and when QEMU stops the supervisor appends how (its exit status, or
the signal that killed it).

    python3 qemu_supervisor.py --log vm-qemu.log --wait-for vm.pid \\
        --wait-for vm-monitor.sock -- qemu-system-x86_64 -pidfile vm.pid ...

Returns once every --wait-for path exists (QEMU writes its -pidfile and opens
its -monitor socket while it starts), after removing stale ones first. If QEMU
stops before that, it fails with the log, so a QEMU that can't start fails the
caller with its own error message, as it did under -daemonize. Don't pass
-daemonize as well: QEMU would leave the supervisor, which would then log an
exit that never happened.
"""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import time

# Starts the line written once QEMU has stopped; the first-boot poll's
# failure message shows it with the rest of the log.
STOPPED = "qemu-supervisor: stopped:"


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ending(returncode: int) -> str:
    if returncode >= 0:
        return f"exited with status {returncode}"
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = "unknown"
    return f"killed by signal {-returncode} ({name})"


def supervise(log: str, command: list[str]) -> None:
    """In the detached grandchild: run QEMU with its output in the log."""
    devnull = os.open(os.devnull, os.O_RDONLY)
    output = os.open(log, os.O_WRONLY | os.O_APPEND)
    os.dup2(devnull, 0)
    os.dup2(output, 1)
    os.dup2(output, 2)
    os.chdir("/")
    print(f"{stamp()} qemu-supervisor: pid {os.getpid()} runs {shlex.join(command)}", flush=True)
    try:
        returncode = subprocess.call(command)
    except OSError as error:
        print(f"{stamp()} {STOPPED} couldn't start {command[0]}: {error}", flush=True)
        os._exit(1)
    print(f"{stamp()} {STOPPED} {ending(returncode)}", flush=True)
    os._exit(0)


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", required=True, help="QEMU's output, then how it stopped")
    parser.add_argument("--wait-for", action="append", default=[], help="a path QEMU creates once it's up")
    parser.add_argument("--timeout", type=float, default=60, help="seconds to wait for those paths")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- qemu-system-x86_64 ...")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command after --")

    log = os.path.abspath(args.log)
    waits = [os.path.abspath(path) for path in args.wait_for]
    # Stale from a VM that's gone: the caller checked that its ports are free.
    for path in waits:
        if os.path.lexists(path):
            os.remove(path)
    # Emptied here rather than in the child, so the wait below can't read a
    # previous run's last line.
    with open(log, "w", encoding="utf-8") as handle:
        handle.write("")
    os.chmod(log, 0o600)

    child = os.fork()
    if child == 0:
        # Never return into the caller's code below, whatever happens here.
        try:
            os.setsid()
            if os.fork() > 0:
                os._exit(0)
            supervise(log, command)
        finally:
            os._exit(1)
    os.waitpid(child, 0)

    deadline = time.monotonic() + args.timeout
    while True:
        if STOPPED in read(log):
            reason = "stopped while starting"
            break
        if all(os.path.exists(path) for path in waits):
            print(f"QEMU is up; its output, and how it stops, go to {log}")
            return 0
        if time.monotonic() > deadline:
            reason = f"didn't create {', '.join(path for path in waits if not os.path.exists(path))} in {args.timeout:g} s"
            break
        time.sleep(0.1)
    print(f"QEMU {reason}. {log}:\n{read(log)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
