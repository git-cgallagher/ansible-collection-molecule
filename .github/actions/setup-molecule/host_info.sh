#!/usr/bin/env bash
# What a windows_vm lab's guests run on, so a lab failure can be matched to the runner's
# hardware and kernel later: the host kernel, CPU, the TSC features and clocksource that
# nested guests' timekeeping depends on, the KVM module parameters, and (with sudo) the
# kernel's own notes on Hyper-V, the TSC and the clocksource.
#
#   host_info.sh [FILE]   prints the report, and also writes it to FILE if given
#
# Reads only; never fails the caller (every probe tolerates a missing file or tool).
set -uo pipefail

# Azure's instance metadata (a GitHub-hosted runner is an Azure VM): its size
# names the hardware generation, e.g. Standard_D4ads_v5 (AMD EPYC 7763).
azure() {
  curl -s -m 2 -H Metadata:true \
    "http://169.254.169.254/metadata/instance/compute/$1?api-version=2021-02-01&format=text" 2> /dev/null || true
}

report() {
  echo "kernel: $(uname -r) ($(uname -v))"
  echo "runner image: ${ImageOS:-?} ${ImageVersion:-?}"
  if [ -n "${GITHUB_ACTIONS:-}" ] && command -v curl > /dev/null; then
    echo "Azure VM: $(azure vmSize) in $(azure location)"
  fi
  if command -v lscpu > /dev/null; then
    lscpu | grep -E '^(Model name|Vendor ID|CPU family|Model|Stepping|CPU\(s\)|Thread\(s\) per core|Hypervisor vendor|Virtualization( type)?|Flags):' \
      | grep -v '^Flags:'
  fi
  local flags
  flags=" $(grep -m1 '^flags' /proc/cpuinfo 2> /dev/null | cut -d: -f2) "
  echo "microcode: $(grep -m1 '^microcode' /proc/cpuinfo 2> /dev/null | cut -d: -f2 | tr -d ' ')"
  local have=() lack=() flag
  for flag in vmx svm hypervisor constant_tsc nonstop_tsc tsc_reliable tsc_known_freq \
              tsc_deadline_timer tsc_adjust rdtscp x2apic; do
    if [[ $flags == *" $flag "* ]]; then have+=("$flag"); else lack+=("$flag"); fi
  done
  echo "CPU flags present: ${have[*]:-none}"
  echo "CPU flags absent: ${lack[*]:-none}"
  local cs=/sys/devices/system/clocksource/clocksource0
  echo "clocksource: $(cat "$cs/current_clocksource" 2> /dev/null || echo '?') (available: $(cat "$cs/available_clocksource" 2> /dev/null || echo '?'))"
  local module param
  for module in kvm kvm_intel kvm_amd; do
    [ -d "/sys/module/$module/parameters" ] || continue
    local values=()
    for param in /sys/module/"$module"/parameters/*; do
      values+=("$(basename "$param")=$(tr -d '\n' < "$param" 2> /dev/null || echo '?')")
    done
    echo "$module: ${values[*]}"
  done
  echo "memory: $(free -m 2> /dev/null | awk '/^Mem:/ {print $2 " MB, " $7 " MB available"}')"
  if sudo -n true 2> /dev/null; then
    echo "kernel log (Hyper-V, TSC, clocksource, KVM):"
    sudo -n dmesg 2> /dev/null | grep -iE 'hyper-?v|tsc|clocksource|kvm' | head -n 60 | sed 's/^/  /'
  fi
}

if [ $# -gt 0 ]; then
  report 2>&1 | tee "$1"
else
  report 2>&1
fi
exit 0
