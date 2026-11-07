"""Watches a windows_vm guest from outside and records a stall.

A guest can stop while QEMU still reports it "running": the screen's clock stops,
the disk is no longer written, and the next WinRM call times out. QEMU's run state
can't see that, so this samples two things the guest does while it is alive, every
few seconds, through QEMU's QMP socket and the WinRM port forward:

- disk I/O: the read, write and flush counts of every drive (query-blockstats);
- the WinRM port: an HTTP request through QEMU's forward. The guest's TCP stack
  answers it (an HTTP reply, or a reset when nothing listens) within milliseconds;
  a guest that has stopped never does, and the probe times out.

When neither has moved for --timeout seconds, the guest made no progress: the
watcher writes the verdict and what QEMU can still tell about the guest (screen,
vCPU threads and KVM counters, registers and local APICs, user-network
connections) to its log, and the verdict to the --marker file, which
first_boot_poll.py reads. It only reports: the VM keeps running, and if the guest
moves again the marker goes and the log says so. A paused or powered-off guest
isn't judged (first_boot_poll.py reports those).

qemu_supervisor.py runs it for each VM (create.yml passes --watch-*); it also runs
on its own against a running VM, for a one-off capture:

    python3 vm_watch.py --qmp dc01-qmp.sock --port 5985 --pid 4321 \\
        --prefix /mnt/lab/dc01 --capture
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time

# The WinRM probe: any reply, or the guest closing the connection, is an answer.
PROBE = b"GET /wsman HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

# KVM's per-vCPU counters worth comparing across a few seconds of a stall.
VCPU_STATS = ("exits", "halt_exits", "halt_wakeup", "irq_injections", "nmi_injections",
              "irq_window_exits", "irq_exits", "hypercalls", "io_exits", "mmio_exits",
              "insn_emulation", "blocking")


def stamp(when: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))


class QmpError(Exception):
    pass


class Qmp:
    """A QMP connection, one per use (with ...:): QEMU serves one client per socket."""

    def __init__(self, path: str, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.buffer = b""

    def __enter__(self) -> Qmp:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(self.path)
            self._message()  # the greeting
            self.command("qmp_capabilities")
        except BaseException:
            self.sock.close()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.sock is not None:
            self.sock.close()

    def _message(self) -> dict:
        assert self.sock is not None
        while b"\n" not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise QmpError("QEMU closed the QMP connection")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def command(self, name: str, **arguments: object) -> object:
        assert self.sock is not None
        request: dict = {"execute": name}
        if arguments:
            request["arguments"] = arguments
        self.sock.sendall(json.dumps(request).encode() + b"\n")
        while True:
            reply = self._message()
            if "return" in reply:
                return reply["return"]
            if "error" in reply:
                raise QmpError(f"{name}: {reply['error'].get('desc', reply['error'])}")
            # anything else is an asynchronous event: skip it

    def hmp(self, line: str, cpu: int | None = None) -> str:
        arguments: dict = {"command-line": line}
        if cpu is not None:
            arguments["cpu-index"] = cpu
        return str(self.command("human-monitor-command", **arguments))


def running(qmp: Qmp) -> bool:
    status = qmp.command("query-status")
    return isinstance(status, dict) and status.get("status") == "running"


def disk_operations(qmp: Qmp) -> int:
    """Reads, writes and flushes so far, over every drive."""
    total = 0
    for device in qmp.command("query-blockstats") or []:  # type: ignore[union-attr]
        stats = device.get("stats", {})
        total += sum(int(stats.get(key, 0)) for key in ("rd_operations", "wr_operations", "flush_operations"))
    return total


def port_answers(port: int, timeout: float = 10.0) -> bool | None:
    """Whether the guest answered on its forwarded port: True (a reply, or the
    guest closed or reset the connection), False (silence for timeout seconds),
    None (nothing listens on the host side: QEMU is gone)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(PROBE)
            sock.recv(1)
            return True
    except ConnectionRefusedError:
        return None
    except (ConnectionResetError, BrokenPipeError):
        return True
    except socket.timeout:
        return False
    except OSError:
        return None


class Progress:
    """Decides, from successive samples, when the guest stopped making progress.

    update() returns ("stalled", seconds) once the guest has been silent for the
    timeout, ("resumed", seconds) when a stalled guest moves again, else None. It
    also keeps, for the log, the longest silence and every one of a minute or
    more that ended."""

    NOTABLE = 60.0

    def __init__(self, timeout: float, now: float) -> None:
        self.timeout = timeout
        self.last_progress = now
        self.disk: int | None = None
        self.longest = 0.0
        self.longest_from = now
        self.notable: list[tuple[float, float]] = []
        self.stalled = False

    def update(self, now: float, is_running: bool, disk: int | None, answered: bool | None) -> tuple[str, float] | None:
        moved = bool(answered) or (disk is not None and self.disk is not None and disk != self.disk)
        if disk is not None:
            self.disk = disk
        silence = now - self.last_progress
        if not is_running:
            # Paused or powered off: not ours to judge (first_boot_poll.py reports it).
            self.last_progress = now
            return None
        if silence > self.longest:
            self.longest, self.longest_from = silence, self.last_progress
        if moved:
            if silence >= self.NOTABLE:
                self.notable.append((self.last_progress, silence))
            event = ("resumed", silence) if self.stalled else None
            self.stalled = False
            self.last_progress = now
            return event
        if not self.stalled and self.timeout > 0 and silence >= self.timeout:
            self.stalled = True
            return ("stalled", silence)
        return None


def thread_sample(pid: int, tid: int) -> tuple[str, int, str]:
    """A QEMU thread's state letter, CPU time in clock ticks, and wait channel."""
    base = f"/proc/{pid}/task/{tid}"
    try:
        with open(f"{base}/stat", encoding="ascii") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        state, ticks = fields[0], int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError):
        return "?", 0, "?"
    try:
        with open(f"{base}/wchan", encoding="ascii") as handle:
            wchan = handle.read().strip() or "-"
    except OSError:
        wchan = "?"
    return state, ticks, wchan


def vcpu_stats(qmp: Qmp) -> dict[str, dict[str, object]]:
    """KVM's counters per vCPU (qom path -> name -> value); empty if QEMU can't say."""
    try:
        results = qmp.command("query-stats", target="vcpu")
    except QmpError:
        return {}
    stats: dict[str, dict[str, object]] = {}
    for result in results or []:  # type: ignore[union-attr]
        path = result.get("qom-path", "?")
        for stat in result.get("stats") or []:
            stats.setdefault(path, {})[stat.get("name")] = stat.get("value")
    return stats


def capture(qmp_path: str, pid: int, prefix: str, nmi_wait: float = 0.0, sample: float = 5.0) -> list[str]:
    """What QEMU can still tell about the guest, as log lines. The screen first:
    its clock dates the stall. Commands that need a vCPU to stop (registers,
    APIC) come last: a vCPU stuck in the host kernel would block them."""
    lines: list[str] = []
    screen = f"{prefix}-stalled-screen.ppm"
    try:
        with Qmp(qmp_path, timeout=20) as qmp:
            qmp.command("screendump", filename=screen)
            lines.append(f"screen: {screen}")
            lines.append(f"status: {json.dumps(qmp.command('query-status'))}")
            cpus = [(cpu["cpu-index"], cpu["thread-id"], cpu.get("qom-path", "?"))
                    for cpu in qmp.command("query-cpus-fast") or []]  # type: ignore[union-attr]
            before = {tid: thread_sample(pid, tid) for _index, tid, _path in cpus}
            stats_before = vcpu_stats(qmp)
            time.sleep(sample)
            after = {tid: thread_sample(pid, tid) for _index, tid, _path in cpus}
            stats_after = vcpu_stats(qmp)
            hertz = os.sysconf("SC_CLK_TCK")
            for index, tid, path in cpus:
                state, ticks, wchan = after[tid]
                busy = 100.0 * (ticks - before[tid][1]) / hertz / sample
                counters = []
                for name in VCPU_STATS:
                    old, new = stats_before.get(path, {}).get(name), stats_after.get(path, {}).get(name)
                    if isinstance(new, int) and isinstance(old, int):
                        counters.append(f"{name} {new}" if name == "blocking" else f"{name} +{new - old}")
                lines.append(f"vCPU {index} (thread {tid}): state {state}, {busy:.0f}% CPU over {sample:g} s, "
                             f"waiting in {wchan}; KVM over {sample:g} s: {', '.join(counters) or 'no counters'}")
            lines.append(f"disk operations: {disk_operations(qmp)}")
            lines.extend("user network: " + line for line in qmp.hmp("info usernet").splitlines() if line.strip())
            for index, _tid, _path in cpus:
                registers = qmp.hmp("info registers", cpu=index)
                keep = re.findall(r"RIP=\S+|RFL=\S+ \[[^\]]*\]|CPL=\d|II=\d|HLT=\d", registers)
                lines.append(f"vCPU {index} registers: {' '.join(keep)}")
                lines.extend(f"vCPU {index} APIC: {line.strip()}" for line in qmp.hmp("info lapic", cpu=index).splitlines()
                             if line.strip() and not line.startswith("dumping"))
            if nmi_wait > 0:
                qmp.command("inject-nmi")
                lines.append(f"injected an NMI; a guest that can still run code bugchecks (screen in {nmi_wait:g} s)")
    except Exception as error:  # pylint: disable=broad-exception-caught
        # A partial capture beats none: say where it stopped (a socket timeout
        # here can mean a vCPU stuck in the host kernel).
        lines.append(f"capture stopped: {type(error).__name__}: {error}")
        return lines
    if nmi_wait > 0:
        time.sleep(nmi_wait)
        try:
            with Qmp(qmp_path, timeout=20) as qmp:
                qmp.command("screendump", filename=f"{prefix}-nmi-screen.ppm")
                lines.append(f"screen after the NMI: {prefix}-nmi-screen.ppm, "
                             f"status {json.dumps(qmp.command('query-status'))}, disk operations {disk_operations(qmp)}")
        except Exception as error:  # pylint: disable=broad-exception-caught
            lines.append(f"no screen after the NMI: {type(error).__name__}: {error}")
    return lines


class Watcher:
    """The loop qemu_supervisor.py runs while QEMU lives: step() every few seconds."""

    def __init__(self, args: argparse.Namespace, pid: int, log=print) -> None:
        self.args = args
        self.pid = pid
        self.log = log
        self.progress = Progress(args.timeout, time.time())
        self.errors = 0
        for stale in (args.marker, f"{args.prefix}-stalled-screen.ppm", f"{args.prefix}-nmi-screen.ppm"):
            if os.path.lexists(stale):
                os.remove(stale)

    def say(self, text: str) -> None:
        self.log(f"{stamp()} qemu-supervisor: watchdog: {text}")

    def step(self) -> None:
        if not os.path.exists(self.args.qmp):
            return
        try:
            with Qmp(self.args.qmp) as qmp:
                is_running = running(qmp)
                disk = disk_operations(qmp)
        except (OSError, QmpError, ValueError) as error:
            # QEMU is starting, stopping, or busy: no sample, no verdict.
            self.errors += 1
            if self.errors in (3, 30):
                self.say(f"QMP isn't answering ({type(error).__name__}: {error}); still watching")
            return
        self.errors = 0
        answered = port_answers(self.args.port, timeout=self.args.probe_timeout) if is_running else None
        event = self.progress.update(time.time(), is_running, disk, answered)
        if event is None:
            return
        kind, seconds = event
        if kind == "resumed":
            self.say(f"the guest is making progress again, after {seconds:.0f} s without")
            if os.path.exists(self.args.marker):
                os.remove(self.args.marker)
            return
        verdict = (f"the guest made no progress for {seconds:.0f} s: no disk I/O and no answer on "
                   f"127.0.0.1:{self.args.port} since {stamp(self.progress.last_progress)}, while QEMU "
                   "reports it running")
        self.say(verdict)
        for line in capture(self.args.qmp, self.pid, self.args.prefix, self.args.nmi_wait):
            self.log(f"    {line}")
        self.say("end of the capture; still watching")
        with open(self.args.marker, "w", encoding="utf-8") as handle:
            handle.write(f"{stamp()} {verdict}\n")

    def finish(self) -> None:
        """The margin the timeout had on this run, for whoever tunes it."""
        progress = self.progress
        notable = "; ".join(f"{seconds:.0f} s from {stamp(start)}" for start, seconds in progress.notable)
        self.say(f"longest stretch without progress: {progress.longest:.0f} s"
                 + (f" (from {stamp(progress.longest_from)})" if progress.longest else "")
                 + (f"; those of a minute or more that ended: {notable}" if notable else ""))


def add_arguments(parser: argparse.ArgumentParser, prefix: str = "") -> None:
    parser.add_argument(f"--{prefix}qmp", help="QEMU's -qmp unix socket")
    parser.add_argument(f"--{prefix}port", type=int, help="the host port forwarded to the guest's WinRM")
    parser.add_argument(f"--{prefix}prefix", help="path prefix of the screens (<prefix>-stalled-screen.ppm)")
    parser.add_argument(f"--{prefix}marker", help="file the verdict goes to while the guest is stalled")
    parser.add_argument(f"--{prefix}timeout", type=float, default=180, help="seconds without progress (0: never judge)")
    parser.add_argument(f"--{prefix}interval", type=float, default=10, help="seconds between samples")
    parser.add_argument(f"--{prefix}probe-timeout", type=float, default=10, help="seconds the WinRM probe waits")
    parser.add_argument(f"--{prefix}nmi-wait", type=float, default=0,
                        help="after a capture, inject an NMI and take another screen this many seconds later (0: no NMI)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_arguments(parser)
    parser.add_argument("--pid", type=int, required=True, help="QEMU's process id")
    parser.add_argument("--capture", action="store_true", help="capture once, now, and exit")
    args = parser.parse_args()
    if not args.capture:
        parser.error("only --capture runs on its own; qemu_supervisor.py runs the watcher")
    for line in capture(args.qmp, args.pid, args.prefix, args.nmi_wait):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
