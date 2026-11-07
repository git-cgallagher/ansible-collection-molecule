#!/usr/bin/env bash
# The windows_vm kind's QEMU lifecycle, without a Windows box: a disk-less QEMU
# (BIOS only, no OS) started through roles/windows_vm/files/qemu_supervisor.py,
# and each answer of first_boot_poll.py but "ready" (that one needs Windows;
# the windows_dc and windows_cluster scenarios prove it). A guest powering
# itself off needs an OS too; a monitor `stop` stands in for "the guest
# stopped". The supervisor's watchdog (vm_watch.py) judges a guest by its disk
# I/O and by answers on its WinRM port: a disk-less guest does no I/O, and a
# local HTTP listener stands in for its WinRM, so stopping the listener is a
# stall and starting it again is the guest moving again. Run from the repo root
# on a host with QEMU and a Python with pypsrp (windows-lab.yml: setup-molecule
# with kvm: 'true').
set -euo pipefail

files=roles/windows_vm/files
python=${PYTHON:-python3}
dir=$(mktemp -d)
log=$dir/vm-qemu.log
pid=$dir/vm.pid
mon=$dir/vm-monitor.sock
qmp=$dir/vm-qmp.sock
marker=$dir/vm.stalled
listener=

cleanup() {
  if [ -f "$pid" ]; then kill -9 "$(cat "$pid")" 2>/dev/null || true; fi
  if [ -n "$listener" ]; then kill "$listener" 2>/dev/null || true; fi
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
    --pidfile "$pid" --monitor "$mon" --stalled "$marker" --deadline "${1:-4102444800}"
}

# A QEMU whose supervisor watches it: a stall after 12 s without progress,
# sampled every 2 s, and an NMI 2 s after the capture.
watched() {
  "$python" "$files/qemu_supervisor.py" --log "$log" --wait-for "$pid" --wait-for "$mon" --wait-for "$qmp" \
    --watch-qmp "$qmp" --watch-port "$port" --watch-prefix "$dir/vm" --watch-marker "$marker" \
    --watch-timeout 12 --watch-interval 2 --watch-probe-timeout 2 --watch-nmi-wait 2 -- \
    qemu-system-x86_64 -machine pc,accel=kvm:tcg -m 128 -display none -no-shutdown -net none \
    -monitor "unix:$mon,server,nowait" -qmp "unix:$qmp,server,nowait" -pidfile "$pid"
}

# A stand-in for the guest's WinRM on 127.0.0.1:$port: answers every request.
answer() {
  "$python" - "$port" <<'PY' &
import socket, sys
server = socket.create_server(("127.0.0.1", int(sys.argv[1])))
while True:
    client, _ = server.accept()
    with client:
        client.settimeout(5)
        try:
            client.recv(4096)
            client.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
PY
  listener=$!
  for _ in $(seq 1 50); do
    "$python" -c 'import socket, sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), 1).close()' "$port" \
      2>/dev/null && return 0
    sleep 0.1
  done
  echo "::error::the stand-in WinRM listener didn't start"
  exit 1
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

# expect_log <text> <description> [seconds]: the QEMU log must say it within 5
# seconds, or as many as given (the supervisor writes its last line just after
# QEMU exits).
expect_log() {
  for _ in $(seq 1 $((${3:-5} * 10))); do
    if grep -q -- "$1" "$log"; then
      echo "log says: $(grep -- "$1" "$log" | tail -n 1 | cut -c1-160)"
      return 0
    fi
    sleep 0.1
  done
  echo "::error::$2: \"$1\" isn't in $log"
  cat "$log"
  exit 1
}

# expect_file <path> <description> / expect_no_file <path> <description>
expect_file() {
  if [ -e "$1" ]; then echo "exists: $1"; else echo "::error::$2: $1 is missing"; cat "$log"; exit 1; fi
}
expect_no_file() {
  if [ ! -e "$1" ]; then echo "gone: $1"; else echo "::error::$2: $1 is still there"; cat "$log"; exit 1; fi
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

echo "== the log starts with the host: kernel, clocksource, CPU"
expect_log "qemu-supervisor: host: kernel $(uname -r), clocksource " "the host line"

echo "== the watchdog: a guest that answers makes progress"
port=$("$python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
answer
expect 0 "start, watched" watched
expect_log "watchdog: no disk I/O and no answer on 127.0.0.1:$port for 12 s is a stall" "the watchdog's rule"
sleep 16
if grep -q "made no progress" "$log"; then
  echo "::error::the watchdog judged a guest whose WinRM answers"
  cat "$log"
  exit 1
fi
echo "no verdict in 16 s while the stand-in answers"
expect 3 "poll: running, no verdict" poll

echo "== silence for 12 s is a stall: the verdict, the capture, the marker, the poll's rc 13"
kill "$listener"
listener=
expect_log "watchdog: the guest made no progress for" "the verdict" 30
expect_log "watchdog: end of the capture" "the capture's end" 40
expect_log "    vCPU 0 (thread" "the vCPU sample"
expect_log "    vCPU 0 APIC: " "the APIC"
expect_log "    injected an NMI" "the NMI"
expect_log "    screen after the NMI: " "the second screen"
expect_file "$dir/vm-stalled-screen.ppm" "the screen at the verdict"
expect_file "$dir/vm-nmi-screen.ppm" "the screen after the NMI"
expect_file "$marker" "the marker"
expect 13 "poll: the watchdog's verdict" poll
echo "--- the capture, as the log has it"
sed -n '/watchdog: the guest made no progress/,/watchdog: end of the capture/p' "$log"
echo "---"

echo "== an answer again: progress, the marker goes"
answer
expect_log "watchdog: the guest is making progress again" "the recovery" 10
expect_no_file "$marker" "the marker after the recovery"
expect 3 "poll: running, no verdict" poll

echo "== QEMU's end: the longest silence, then how it stopped"
kill_qemu TERM
expect_log "watchdog: longest stretch without progress: " "the longest silence"
expect_log "stopped: exited with status" "the exit status"
expect 10 "poll: QEMU gone" poll

echo "All windows_vm lifecycle checks passed."
