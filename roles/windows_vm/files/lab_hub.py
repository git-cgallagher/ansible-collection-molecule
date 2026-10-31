"""Ethernet hub for windows_vm lab networks.

Each VM's lab NIC is a QEMU socket netdev in UDP mode: QEMU sends every
Ethernet frame as one datagram from the VM's own 127.0.0.1 port to the hub's
port, and receives frames on its own port. The hub repeats each frame to every
other member, so the VMs share one Ethernet segment without root, a bridge or
multicast.

    python3 lab_hub.py --hub 20016 --member 20017 --member 20018 --pidfile hub.pid

Detaches once the hub port is bound and the pidfile is written; --foreground
keeps it attached (tests).
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time

HOST = "127.0.0.1"


def serve(sock: socket.socket, members: list[tuple[str, int]]) -> None:
    while True:
        try:
            frame, source = sock.recvfrom(65535)
        except OSError:
            continue
        for member in members:
            if member != source:
                try:
                    sock.sendto(frame, member)
                except OSError:
                    # A member whose QEMU isn't up (yet, or any more).
                    pass


def detach(pidfile: str) -> None:
    """Double-fork; the original process exits once the pidfile exists."""
    if os.fork() > 0:
        for _attempt in range(100):
            if os.path.exists(pidfile):
                os._exit(0)
            time.sleep(0.05)
        os._exit(1)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir("/")
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hub", type=int, required=True, help="UDP port the VMs send to")
    parser.add_argument("--member", type=int, action="append", required=True, help="a VM's own UDP port")
    parser.add_argument("--pidfile", required=True)
    parser.add_argument("--foreground", action="store_true")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Bind before detaching, so a taken port fails the caller.
    sock.bind((HOST, args.hub))
    pidfile = os.path.abspath(args.pidfile)
    # Stale from a hub that died: the port was free, so nothing owns it.
    if os.path.exists(pidfile):
        os.remove(pidfile)
    if not args.foreground:
        detach(pidfile)
    with open(pidfile, "w", encoding="ascii") as handle:
        handle.write(f"{os.getpid()}\n")
    serve(sock, [(HOST, port) for port in args.member])
    return 0


if __name__ == "__main__":
    sys.exit(main())
