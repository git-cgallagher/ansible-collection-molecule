# Changelog

## 0.5.0 (2026-11-02)

setup-molecule can take the collections from an ansible-runtime image instead of Galaxy, so a job
that already pulls the image downloads no collection at run time. On 2026-11-02 Galaxy cost two
windows lab jobs before any test ran: ansible-collection-windows PR #55 ("Failed to download
collection tar ... The handshake operation timed out", installing community.docker 5.4.0) and PR #54
("Missing expected 'results' in ansible-galaxy cache"). The labs were downloading the very versions
the image they had just pulled already contains.

- **New input `collections-image`** (an image ref; pass the caller's `RUNTIME_IMAGE`). The action
  asks the image where its collections are (the `ansible_collections` directory on its Python path,
  which moves with the image's Python version) and copies them into the first collections path,
  `~/.ansible/collections`, with `docker create` and `docker cp`. `git_cgallagher.molecule` is the
  exception: it still comes from the action's own ref. Every `ansible-galaxy` call then runs
  `--offline` (`.github/actions/setup-molecule/image_collections.sh`). The image must be a `-ci`
  one: an image with other `git_cgallagher.*` collections (the runtime target, whose released
  copies would sit next to the checkout under test) is refused, as is one with no
  `ansible_collections` directory on its Python path, or more than one.
- **`requirements` become a check** when `collections-image` is set: a file that names a collection
  the image lacks, or a version it doesn't have, fails the step and says to pin it in the image
  first. Nothing falls back to Galaxy. Without `collections-image`, `requirements` still install
  from Galaxy.
- The order matters: installing `git_cgallagher.molecule` resolves its `galaxy.yml` dependencies,
  and those (ansible.windows, microsoft.ad, community.docker and its
  community.library_inventory_filtering_v1) were a lab's first four Galaxy downloads. The image's
  collections now go in before it.
- CI: every job here that sets up Molecule (lint, the Docker and Windows scenarios, the `windows_vm`
  lifecycle job) takes its collections from `RUNTIME_IMAGE` and runs with galaxy.ansible.com
  unreachable (an `/etc/hosts` entry), so a download that is still left anywhere in it fails the job.

## 0.4.0 (2026-11-02)

A `windows_vm` whose QEMU dies, or whose guest stops, now fails the run at the next poll and says
why ([issue #13](https://github.com/git-cgallagher/ansible-collection-molecule/issues/13)). Found
in ansible-collection-windows PR #36: on 2 of about 9 runs of its three-VM lab, one VM's QEMU
vanished during first boot, and the wait then polled for its whole budget with nothing to show
why.

- **QEMU runs under a supervisor** (`roles/windows_vm/files/qemu_supervisor.py`) instead of
  `-daemonize`, which discarded QEMU's output and exit status. `<lab dir>/<platform>-qemu.log` holds
  what QEMU printed and how it stopped ("exited with status N" or "killed by signal N (SIGNAME)"), and
  destroy keeps it. Create still fails at once, with QEMU's own message, if QEMU can't start.
- **`-no-shutdown`:** a guest that powers itself off stays in QEMU, so its monitor still answers and
  the screendump still works. A kill (destroy's) still ends QEMU.
- **The first-boot wait checks the VM before WinRM** (`files/first_boot_poll.py`): a QEMU that
  exited, or a guest that isn't running (powered off, paused on an error), ends the wait at the next
  poll, and the failure message quotes the end of the QEMU log.
- **A deadline instead of a poll count:** `windows_vm_first_boot_timeout` (seconds, default 1800),
  counted once for all of a scenario's VMs. Polls that each hung for their 40 s could stretch the old
  90 polls to 75 minutes, the length of a consumer's whole CI step. `windows_vm_first_boot_retries`
  still works, as that many 10 s polls. The `windows_cluster` self-test drops its 120.
- **`vm-screendump`** also prints each platform's QEMU run state and the end of its QEMU log, so
  consumers' failure steps show them with no workflow change.
- CI: `windows-lab`'s new `windows_vm lifecycle` job runs the supervisor and each poll answer
  against a disk-less QEMU (`.github/ci/windows_vm_lifecycle.sh`, a few minutes, no box download),
  and the scenario jobs upload the QEMU logs with the screendumps.

## 0.3.2 (2026-11-01)

- **`quiet_vm` no longer fails when Windows refuses to stop Windows Update**
  ([issue #18](https://github.com/git-cgallagher/ansible-collection-molecule/issues/18), a regression
  from 0.3.0's `win_service` swap). Right after first boot `wuauserv` is often trigger-starting or
  `StopPending`, and `win_service` failed with "Service 'Windows Update (wuauserv)' stop failed",
  where the old script's `-ErrorAction SilentlyContinue` had carried on. The role now disables the
  service first (always works, and stops trigger starts), then stops it with retries
  (`quiet_vm_stop_retries`, `quiet_vm_stop_delay`: 10 × 6 s); if Windows still refuses, it warns and
  leaves the service disabled but running instead of failing. Any other error still fails. The new
  cacheable fact `quiet_vm_wuauserv_stopped` says which happened. A second run still reports no
  changes, and check mode predicts.
- **`quiet_vm` waits out Windows Update Medic.** Shortly after boot, Windows repairs a disabled
  `wuauserv` (start mode back to manual, service started) when something asks for it; the box
  already has the "no automatic updates" policy, and nothing an administrator can do over the
  connection prevents the repair (Medic's service config is protected, and a registry change waits
  for a reboot). It has been a one-shot repair (PR #19's first `windows_cluster` idempotence step
  caught it on a freshly booted node), so after a pass that changed `wuauserv` the role waits
  `quiet_vm_settle_seconds` (15), reads the service back, and disables and stops it once more if
  Windows re-armed it. The cacheable fact `quiet_vm_wuauserv_rearmed` records that.
- Self-tests: `windows_dc` and `windows_cluster` keep a fact cache, so their verify requires
  `wuauserv` stopped unless `quiet_vm` warned (disabled is always required), and prints whether
  Windows re-armed it during converge.
- CI: the `sanity (devel, venv)` canary is gone; ansible-core `devel` is no longer tested (owner
  decision). Sanity covers `stable-2.18`…`stable-2.21`.

## 0.3.1 (2026-11-01)

- **`quiet_vm` converges with Defender Tamper Protection on.** `Set-MpPreference
  -DisableRealtimeMonitoring` is silently ignored while Tamper Protection is on, so the Defender task
  reported `changed` on every run. It now reads the preference back after the write, reports
  `changed` only when the value flipped, and warns (the task's `warning` list and
  `output[0].tamper_protected`) when the write didn't take, so a lab with Defender on still runs and a
  second run reports no change either way. Found in review of PR #9; the lab box has Tamper
  Protection off, so CI never saw it.
- `.claude/worktrees/` is ignored by git.

## 0.3.0 (2026-11-01)

The `ad_forest` items of the 2026-11-01 `win_powershell` review
([issue #8](https://github.com/git-cgallagher/ansible-collection-molecule/issues/8)): modules where
one exists, and the lab-bootstrap block the four Windows labs copied now lives in one role.

- **New role `quiet_vm`:** stops and disables Windows Update (`ansible.windows.win_service`) and
  turns off Defender real-time scanning (a `win_powershell` that reads the preference first, so it
  reports `changed` only when it wrote and predicts in check mode; a VM without Defender is a no-op).
  Toggles `quiet_vm_windows_update` and `quiet_vm_defender`. `ad_forest` uses it (`ad_forest_quiet`)
  first and again after the promotion, since the feature install and the reboots re-arm the Windows
  Update service; the workgroup labs in ansible-collection-windows (wsfc, iis) can include it from
  prepare.
- **`ad_forest`:** the SYSVOL path wait is `ansible.windows.win_stat` (`until` the path exists); the
  admin-group membership is `microsoft.ad.group` (`members.add`), one task per group, with the
  account taken from `ansible_user` without a `DOMAIN\` prefix, or from the `user` facts when the
  inventory doesn't set `ansible_user`. The `Set-Date` clock sync stays a `win_powershell` (no
  module) and now predicts in check mode. A second run reports no changes.
- Self-tests: `windows_dc` runs `ad_forest` from converge, so Molecule's idempotence step is the
  second-converge proof, and its verify reads back what `quiet_vm` set; `windows_cluster` runs
  `quiet_vm` on both workgroup nodes, the way the windows labs will.

## 0.2.1 (2026-11-01)

- **setup-molecule time-limits apt.** With `kvm: 'true'`, `apt-get update` and the QEMU
  `apt-get install` stop after 3 and 8 minutes (killed 30 seconds later if they ignore it) and fail
  the step with a message to re-run the job, instead of hanging until the job's own limit. A caller
  can only limit the whole action, since a composite action's steps can't set `timeout-minutes`,
  and a stuck apt once hung an ansible-collection-windows `domain-lab` job for two hours.

## 0.2.0 (2026-10-31)

What moving isc and windows onto the driver needs.

- **Driver option `platform_defaults`:** keys every platform entry starts from; the entry's own keys
  win. A base config's box pin then stays in one place when a scenario names its own VMs (Molecule
  replaces a base config's `platforms` list rather than merging it). It can't set `name`, `groups`
  or `children`.
- Kinds get the merged platforms: `molecule_platform` is one of them, and the new
  `molecule_platforms` is all of them (`windows_vm`'s host-port and lab-network checks read it
  instead of `molecule_yml.platforms`).
- **setup-molecule `constraints` input:** a pip constraints file applied to everything the action
  installs, for repos that pin their CI tools (isc's `.github/ci/requirements.txt`).
- The create and destroy playbooks' vars moved to `playbooks/vars/driver.yml`.
- Self-tests: `docker_smoke` takes its image from `platform_defaults`, `windows_cluster` shares its
  box through them instead of a YAML anchor, and CI's Molecule jobs install under
  `.github/ci/constraints.txt`.

## 0.1.0 (2026-10-31)

First release: the molecule-git-cgallagher driver (pip), the git_cgallagher.molecule collection and the
setup-molecule action, from one repo and one tag.

- `molecule-git-cgallagher` driver: bundled create/destroy import the collection's playbooks, so a
  scenario is its platforms plus converge/verify.
- Kind `docker`: containers through the docker CLI, with networks (static IPs), Dockerfile builds,
  published ports, systemd containers and `platform` pulls; molecule-plugins' docker-driver key
  names; unknown keys fail.
- Kind `windows_vm`: a Windows Server Vagrant box under QEMU/KVM with WinRM and extra ports
  forwarded to 127.0.0.1 (from the copies in git_cgallagher.gpo's and git_cgallagher.isc's
  Windows scenarios).
- Several `windows_vm` platforms per scenario, booting side by side (kinds now have a `ready`
  entry point that runs after every platform's `create`), and `lab_networks`: shared Ethernet
  segments between VMs for cluster labs, through a UDP hub on 127.0.0.1 (no root), with static
  addresses set in Windows.
- Role `ad_forest`: promote a test VM to the first DC of a new forest.
- Actions `setup-molecule` and `vm-screendump`.
- Self-tests: `docker_smoke`, `docker_full` (every docker key; CI and any Docker host),
  `windows_dc` and `windows_cluster` (KVM; the `windows-lab` workflow).
