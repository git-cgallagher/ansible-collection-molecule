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

The log starts with the host's kernel, clocksource and CPU, which a nested
guest's timekeeping depends on. With --watch-qmp and --watch-port, the
supervisor also watches the guest while QEMU runs (vm_watch.py): a guest that
makes no progress for --watch-timeout seconds (no disk I/O, no answer on its
WinRM port) gets a verdict and a capture in the log, and a --watch-marker file.
"""

from __future__ import annotations

import argparse
import importlib.util
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


def load_vm_watch():
    """vm_watch.py, from this file's directory (the role's files/)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm_watch.py")
    spec = importlib.util.spec_from_file_location("vm_watch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def host() -> str:
    """The host's kernel, clocksource and CPU: what a nested guest's clock and
    timers rest on, for matching a failure to the runner's hardware."""
    def first(path: str, prefix: str = "") -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith(prefix):
                        return line.split(":", 1)[1].strip() if prefix else line.strip()
        except OSError:
            pass
        return "?"
    clocksource = first("/sys/devices/system/clocksource/clocksource0/current_clocksource")
    return f"host: kernel {os.uname().release}, clocksource {clocksource}, CPU {first('/proc/cpuinfo', 'model name')}"


def ending(returncode: int) -> str:
    if returncode >= 0:
        return f"exited with status {returncode}"
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = "unknown"
    return f"killed by signal {-returncode} ({name})"


def watch(process: subprocess.Popen, vm_watch, args: argparse.Namespace) -> None:
    """While QEMU runs, sample its guest's progress (vm_watch.Watcher). The
    watchdog only ever writes to the log: an error in it never stops QEMU or
    the supervisor."""
    def log(text: str) -> None:
        print(text, flush=True)

    try:
        watcher = vm_watch.Watcher(args, process.pid, log)
    except Exception as error:  # pylint: disable=broad-exception-caught
        log(f"{stamp()} qemu-supervisor: watchdog: not started: {type(error).__name__}: {error}")
        return
    log(f"{stamp()} qemu-supervisor: watchdog: no disk I/O and no answer on 127.0.0.1:{args.port} "
        f"for {args.timeout:g} s is a stall")
    while process.poll() is None:
        try:
            watcher.step()
        except Exception as error:  # pylint: disable=broad-exception-caught
            log(f"{stamp()} qemu-supervisor: watchdog: {type(error).__name__}: {error}")
        try:
            process.wait(timeout=args.interval)
        except subprocess.TimeoutExpired:
            pass
    watcher.finish()


def supervise(log: str, command: list[str], vm_watch=None, watch_args: argparse.Namespace | None = None) -> None:
    """In the detached grandchild: run QEMU with its output in the log."""
    devnull = os.open(os.devnull, os.O_RDONLY)
    output = os.open(log, os.O_WRONLY | os.O_APPEND)
    os.dup2(devnull, 0)
    os.dup2(output, 1)
    os.dup2(output, 2)
    os.chdir("/")
    print(f"{stamp()} qemu-supervisor: {host()}", flush=True)
    print(f"{stamp()} qemu-supervisor: pid {os.getpid()} runs {shlex.join(command)}", flush=True)
    try:
        process = subprocess.Popen(command)  # pylint: disable=consider-using-with
    except OSError as error:
        print(f"{stamp()} {STOPPED} couldn't start {command[0]}: {error}", flush=True)
        os._exit(1)
    if vm_watch is not None and watch_args is not None:
        watch(process, vm_watch, watch_args)
    returncode = process.wait()
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
    vm_watch = load_vm_watch()
    vm_watch.add_arguments(parser, prefix="watch-")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- qemu-system-x86_64 ...")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command after --")
    watch_args = None
    if args.watch_qmp and args.watch_port and args.watch_timeout > 0:
        if not (args.watch_prefix and args.watch_marker):
            parser.error("--watch-qmp needs --watch-prefix and --watch-marker")
        watch_values = {key[len("watch_"):]: value for key, value in vars(args).items() if key.startswith("watch_")}
        watch_args = argparse.Namespace(**watch_values)

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
            supervise(log, command, vm_watch if watch_args else None, watch_args)
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
