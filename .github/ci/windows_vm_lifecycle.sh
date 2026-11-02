#!/usr/bin/env bash
# The windows_vm kind's QEMU lifecycle, without a Windows box: a disk-less QEMU
# (BIOS only, no OS) started through roles/windows_vm/files/qemu_supervisor.py,
# and each answer of first_boot_poll.py but "ready" (that one needs Windows;
# the windows_dc and windows_cluster scenarios prove it). A guest powering
# itself off needs an OS too; a monitor `stop` stands in for "the guest
# stopped". Run from the repo root on a host with QEMU and a Python with
# pypsrp (windows-lab.yml: setup-molecule with kvm: 'true').
set -euo pipefail

files=roles/windows_vm/files
python=${PYTHON:-python3}
dir=$(mktemp -d)
log=$dir/vm-qemu.log
pid=$dir/vm.pid
mon=$dir/vm-monitor.sock

cleanup() {
  if [ -f "$pid" ]; then kill -9 "$(cat "$pid")" 2>/dev/null || true; fi
}
trap cleanup EXIT

start() {
  "$python" "$files/qemu_supervisor.py" --log "$log" --wait-for "$pid" --wait-for "$mon" -- \
    qemu-system-x86_64 -machine pc,accel=kvm:tcg -m 128 -display none -no-shutdown \
    -monitor "unix:$mon,server,nowait" -pidfile "$pid" "$@"
}

# One poll; its exit status is the answer. Port 1 has no WinRM behind it.
poll() {
  "$python" "$files/first_boot_poll.py" --host 127.0.0.1 --port 1 --user vagrant --password vagrant \
    --pidfile "$pid" --monitor "$mon" --deadline "${1:-4102444800}"
}

# Sends one command to QEMU's human monitor and waits for its prompt.
monitor() {
  "$python" - "$mon" "$1" <<'PY'
import socket, sys
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.settimeout(10)
    sock.connect(sys.argv[1])
    data = b""
    while b"(qemu)" not in data:
        data += sock.recv(4096)
    sock.sendall(sys.argv[2].encode() + b"\n")
    data = b""
    while not data.rstrip().endswith(b"(qemu)"):
        data += sock.recv(4096)
PY
}

# expect <status> <description> <command...>: the command's exit status must match.
expect() {
  local want=$1 what=$2 rc=0 out
  shift 2
  out=$("$@" 2>&1) || rc=$?
  printf '%-58s rc %-3s %s\n' "$what" "$rc" "$(printf '%s' "$out" | tail -n 1 | cut -c1-120)"
  if [ "$rc" != "$want" ]; then
    echo "::error::$what: expected rc $want, got $rc"
    printf '%s\n' "$out"
    exit 1
  fi
}

# expect_log <text> <description>: the QEMU log must say it within 5 s (the
# supervisor writes its last line just after QEMU exits).
expect_log() {
  for _ in $(seq 1 50); do
    if grep -q -- "$1" "$log"; then
      echo "log says: $(grep -- "$1" "$log" | tail -n 1)"
      return 0
    fi
    sleep 0.1
  done
  echo "::error::$2: \"$1\" isn't in $log"
  cat "$log"
  exit 1
}

# kill_qemu <signal>: QEMU's /proc entry must be gone within 15 s. The pid is
# read first: QEMU removes its pidfile when it exits cleanly.
kill_qemu() {
  local qemu
  qemu=$(cat "$pid")
  kill "-$1" "$qemu"
  for _ in $(seq 1 150); do
    [ -e "/proc/$qemu" ] || return 0
    sleep 0.1
  done
  echo "::error::QEMU $qemu still runs 15 s after SIG$1"
  exit 1
}

echo "== a QEMU that can't start fails the supervisor, with QEMU's own error"
expect 1 "start with an unknown option" start -no-such-option
expect_log "stopped: exited with status 1" "the exit status"
expect_log "no-such-option" "QEMU's error message"

echo "== a running VM: the poll waits"
expect 0 "start" start
expect 3 "poll: running, WinRM closed (not yet)" poll

echo "== a stopped guest ends the wait at once"
monitor stop
expect 11 "poll: paused" poll
monitor cont
expect 3 "poll: running again" poll

echo "== the deadline ends the wait"
expect 12 "poll: deadline passed" poll 1

echo "== destroy's kill (SIGTERM) still ends QEMU under -no-shutdown"
kill_qemu TERM
expect_log "terminating on signal 15" "QEMU's own note of the signal"
expect_log "stopped: exited with status" "the exit status"
expect 10 "poll: QEMU gone" poll

echo "== a QEMU killed outright is recorded as killed"
expect 0 "start again" start
kill_qemu KILL
expect_log "stopped: killed by signal 9 (SIGKILL)" "the signal"
expect 10 "poll: QEMU gone" poll

echo "All windows_vm lifecycle checks passed."
