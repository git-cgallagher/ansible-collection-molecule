# AGENTS.md — ansible-collection-molecule

Read `~/Code/AGENTS.md` first. This repo ships three things from one tag:

| Path | What | Ships via |
|---|---|---|
| `molecule_git_cgallagher/`, `pyproject.toml` | the `molecule-git-cgallagher` Molecule driver: name, schema, bundled create/destroy that import the collection's playbooks | pip (git URL; PyPI later) |
| `playbooks/`, `roles/`, `galaxy.yml` | collection `git_cgallagher.molecule`: create/destroy dispatch, kinds `docker` and `windows_vm`, roles `ad_forest` and `quiet_vm` | git (the action's commit, or a tag); not Galaxy |
| `.github/actions/setup-molecule`, `vm-screendump` | composite actions the consumer repos pin by SHA | GitHub |
| `extensions/molecule/` | self-tests: `docker_smoke`, `docker_full` (any Docker host), `windows_dc` and `windows_cluster` (KVM only); `config.yml` is their shared base config, `tasks/` their shared verify tasks | not shipped |
| `docs/MIGRATION.md` | the migration record: isc (2.0.1), orchestration, windows (formerly gpo) and aws, all on the driver | — |

## Commands

```bash
python3 -m venv .venv && . .venv/bin/activate
# CI's versions: the ansible-runtime image's constraints (RUNTIME_IMAGE in .github/workflows/ci.yml)
export RUNTIME_IMAGE=$(sed -n 's/^ *RUNTIME_IMAGE: //p' .github/workflows/ci.yml)
mkdir -p .cache && docker run --rm "$RUNTIME_IMAGE" cat /usr/share/ansible-runtime/constraints.txt > .cache/constraints.txt
pip install -c .cache/constraints.txt -e '.[windows]' ansible-core ansible-lint yamllint
# CI's collections, from the image, not Galaxy: they replace any copy of the same collections
# in ~/.ansible/collections; then check the self-tests' requirements against them, offline
.github/actions/setup-molecule/image_collections.sh "$RUNTIME_IMAGE" ~/.ansible/collections
ansible-galaxy collection install --offline -r extensions/molecule/requirements.yml
mkdir -p .molecule-collections/ansible_collections/git_cgallagher && ln -sfn ../../.. .molecule-collections/ansible_collections/git_cgallagher/molecule
yamllint . && ansible-lint               # production profile, must stay at 0
molecule test -s docker_smoke            # from the repo root
molecule test -s docker_full             # every docker key; ~3 min on a Mac
molecule test -s windows_dc              # Linux x86-64 + /dev/kvm only (CI: windows-lab workflow)
molecule test -s windows_cluster         # two VMs on a lab network; same requirements
```

Sanity needs an `ansible_collections/git_cgallagher/molecule` checkout: `ansible-test sanity --docker`.

## How it fits together

- Molecule calls the driver's `get_playbook(step)`: `molecule_git_cgallagher/playbooks/create.yml` is one
  `import_playbook: git_cgallagher.molecule.create`. If the collection isn't installed that import
  fails loudly ("Could not find playbook"), which is the guard.
- `playbooks/create.yml` loops over `molecule_platforms` twice, including the role named after
  each platform's `kind` (default `docker`): entry point `create` for every platform, then `ready` for
  every platform. Destroy loops once, in reverse, with `destroy`. `molecule_platforms` is each
  `molecule_yml.platforms` entry over the driver's `platform_defaults` option (`driver.options`),
  worked out in `playbooks/vars/driver.yml`, which both playbooks load.
- **Kind contract.** A kind role reads `molecule_platform` and `molecule_lab_dir` (and
  `molecule_platforms`, if it needs the other platforms). Its `create`
  asserts the platform's keys against its own list (`vars/main.yml`; unknown keys fail), starts the
  platform without waiting, and appends instance entries (`instance`, `address`, `connection`, and
  `port`/`user`/`password` when needed) to `<kind>_instances`; its `ready` waits until the platform is
  usable. The driver subclasses Molecule's delegated driver, which maps those entries to
  `ansible_host`/`ansible_port`/`ansible_connection`/... in the inventory. New kind = new role + its
  name in `molecule_kinds` in `playbooks/vars/driver.yml` + a self-test.
- **windows_vm networking.** NIC 0 is QEMU user networking (WinRM + `forwards` on 127.0.0.1, the same
  10.0.2.15 on every VM). Each `lab_networks` entry adds a virtio NIC on a QEMU socket netdev in UDP
  mode, talking to `files/lab_hub.py` on 127.0.0.1, which repeats every frame to the network's other
  members. `tasks/lab_nics.yml` derives hub ports (16-port blocks in 20000–59999) from the scenario
  directory + network name, and MACs from those + the platform name, so create, ready and destroy
  agree without state. The first VM of a network starts its hub (`tasks/hubs.yml`); destroy stops it.
  `ready` waits for the first boot, then `templates/configure_lab_nics.ps1.j2` (over PSRP, in a fresh
  process) renames the NICs, sets the static addresses and the Private profile, and turns off DNS
  registration on `management`.
- **windows_vm lifecycle (0.4.0).** `create` starts QEMU through `files/qemu_supervisor.py`, with
  `-no-shutdown`: QEMU runs in the foreground of a detached supervisor that writes QEMU's output, then
  how it stopped, to `<platform>-qemu.log`, and the call returns once QEMU's pidfile and monitor socket
  exist (or fails with the log). `ready`'s first-boot poll is `files/first_boot_poll.py`, a fresh
  process per attempt that checks, in order: QEMU alive (its pidfile), the run state (`info status` on
  the monitor), the scenario's deadline (`windows_vm_first_boot_timeout`, started once by the first
  windows_vm's `ready`), then WinRM. rc 10 (QEMU gone), 11 (guest stopped) and 12 (deadline) end the
  until loop at once; 0 is ready; 3, or a hung attempt's 124, polls again. `.github/ci/windows_vm_lifecycle.sh`
  (windows-lab's `lifecycle` job) checks each of those answers but "ready" against a disk-less QEMU.
- **setup-molecule's collections (0.5.0).** With `collections-image`,
  `.github/actions/setup-molecule/image_collections.sh` asks the image for its `ansible_collections`
  directory (`ansible_collections.__path__` in the image's Python: ansible-runtime installs them in
  site-packages, whose path moves with its Python version) and copies it out with `docker create` +
  `docker cp`, minus `git_cgallagher.molecule`, into the first collections path (where
  `ansible-galaxy` installs), each collection replacing any older copy. It refuses an image with no
  such directory or more than one, and one with other `git_cgallagher.*` collections (the runtime
  target: a caller's mistake, since the `-ci` target has none). ansible-runtime's `smoke.sh`
  checks the same contract (one directory holding every pinned collection; ansible-runtime PR #16,
  `95ed629`), so an image change that would break the step fails the image build first.
  Every `ansible-galaxy` call
  after that is `--offline`: the action's own collection, whose `galaxy.yml` dependencies must be in
  the image, and each `requirements` file, which then only checks. This repo's jobs that use the
  action map galaxy.ansible.com to 0.0.0.0 first, so anything that still tries to download fails.
- **setup-molecule's QEMU install (0.5.1).** `kvm: 'true'` runs four steps around
  `.github/actions/setup-molecule/apt_install.sh`. `list` runs `apt-get update`, then
  `apt-get install --print-uris -o Acquire::ForceHash=SHA256` writes the files apt would download
  (`'URI' FILE SIZE SHA256:…`) to `$RUNNER_TEMP/setup-molecule-apt/files`, and outputs the cache
  key: `setup-molecule-apt-<ImageOS>-` plus 16 hex digits of a SHA256 over the sorted
  `FILE SIZE SHA256` lines (not the URIs, which name the mirror), or nothing when nothing needs
  downloading. `actions/cache/restore` fills `…/debs`. `install` copies each cached file whose
  SHA256 matches into `/var/cache/apt/archives`, downloads the rest (`--download-only`), copies
  the files into `…/debs`, then installs (`--no-download`). `actions/cache/save` stores `…/debs`
  after a miss, at once rather than in a post step, so a lab that fails later still fills the
  cache. Every `apt-get` runs under `sudo timeout --kill-after=30`; `fetch` gives the update (60 s)
  and the download (120 s) one more try (60 s and 240 s) after `drop_first_mirror` takes the lowest
  `priority:` lines out of `/etc/apt/apt-mirrors.txt`, once per job (the original is kept as
  `…/apt-mirrors.txt.orig`). No second update is needed: apt names the package lists after the
  mirror list's path (`_etc_apt_apt-mirrors.txt_dists_…`), not after its mirrors. 10 minutes of apt
  at most; a healthy runner needs about 15 s.
- Platform key names for `docker` follow molecule-plugins' docker driver so consumers' existing
  `platforms:` carry over. Keep that true when adding keys.
- **The Windows lab roles follow Ansible's module principles (owner rule, 2026-11-01).** A task
  reports `changed` only when it changed the VM, a second run reports none (the self-tests'
  `idempotence` step is the proof: `windows_dc` runs `ad_forest` from converge for that reason), and
  check mode predicts without writing. Use a module where one exists; a `win_powershell` that has
  to stay (Defender's `Set-MpPreference`, `Set-Date`) reads the state first, sets `$Ansible.Changed`
  from it and declares `[CmdletBinding(SupportsShouldProcess)]`, or win_powershell skips it in
  check mode. `quiet_vm` is the shared "quiet the VM" step; the windows repo's prepare playbooks
  include it (`ansible.builtin.include_role: name: git_cgallagher.molecule.quiet_vm`) instead of
  copying the block.

## Releasing

Merge to `main` with `galaxy.yml` and `pyproject.toml` versions bumped together and a CHANGELOG entry,
then push an annotated tag `vX.Y.Z`. Consumers pin the setup-molecule action's commit SHA (with a
`# vX.Y.Z` comment), which installs the driver and the collection from that same commit. The
collection is **not published to Galaxy** (Chris, 2026-11-02): it's test tooling, installed from the
action's commit or a git tag. The collections that do go to Galaxy are windows, aws, orchestration,
isc and vmware. PyPI publishing for the driver isn't set up (TODO below).

Which commit gets the tag:

- **The PR's head**, if `main` didn't move while the PR was open, so the head's tree equals the
  merge commit's (`git rev-parse <head>^{tree} <merge>^{tree}`). A consumer PR can then pin that SHA
  and prove it in its own CI before the release, with nothing to re-pin afterwards. `v0.2.0` is
  `b2601b3`, the head of PR #4; its merge commit `552ba75` has the same tree, and isc PR #18 was
  tested and merged on that pin.
- **The merge commit** otherwise (`v0.1.0` is `d9421b5`).

The tag doesn't finish a release: the consumers' labs and the ansible-runtime image's driver pin
move to it in one round, then the consumers' `RUNTIME_IMAGE`. The steps, in order, are in
ansible-runtime's
[docs/SHARED-CI.md](https://github.com/git-cgallagher/ansible-runtime/blob/main/docs/SHARED-CI.md#a-driver-release-and-the-round-after-it).

## Repo settings (public-repo baseline, applied 2026-10-31)

- Ruleset **"main protection"** (id `26286539`) on the default branch: active, no bypass actors,
  deletion and force-push blocked, PR required with 0 approvals, and these checks required (GitHub
  Actions, integration `15368`): `lint`, `sanity (stable-2.18)`, `sanity (stable-2.19)`,
  `sanity (stable-2.20)`, `sanity (stable-2.21)`, `molecule docker_smoke`, `molecule docker_full`.
  Not required, on purpose: the `windows-lab`
  jobs (path-filtered, and the scenario jobs depend on a third-party box download). Still merge only when those
  are green too: merges are by hand, and repo auto-merge is off.
- Secret scanning + push protection, Dependabot alerts + security updates, private vulnerability
  reporting (`SECURITY.md`), CodeQL default setup (`python`, `actions`), delete branch on merge;
  wiki and projects off. Actions are SHA-pinned; `.github/dependabot.yml` keeps them current.
- Every CI job sets `timeout-minutes` (2026-11-01): GitHub's default is 6 hours, and a stalled
  install would hold the runner that long. `ci.yml`'s jobs get 15 (30 for Molecule), several times
  their slowest green run; `windows-lab`'s scenario jobs keep 120 and its `lifecycle` job gets 20
  (15 until 0.5.1: about a minute normally, but setup-molecule's apt alone can take 10 when a
  mirror is slow). A new job needs one too.
- Pushes go over SSH (`git@github.com:`): the `gh` token has no `workflow` scope, so HTTPS pushes
  that touch `.github/workflows/` are refused.

## ⚠️ Gotchas

- **Driver names must match `molecule-*` or `custom-*`.** Molecule's `molecule.json` schema only
  allows its built-in names or those patterns, so a bare `name: molecule` fails validation before the
  driver loads, and the pip name `molecule` is Molecule itself: hence `molecule-git-cgallagher`
  (2026-10-31, when Chris named the collection `molecule`). The driver also overrides `schema_file()`
  (its `driver.json` allows only `molecule-git-cgallagher`).
- **Our variables share Molecule's `molecule_` prefix.** Molecule itself injects `molecule_file`,
  `molecule_yml`, `molecule_ephemeral_directory`, `molecule_scenario_directory`,
  `molecule_instance_config` and `molecule_no_log`; ours are `molecule_platform`,
  `molecule_platforms`, `molecule_platform_defaults`, `molecule_kinds`, `molecule_lab_dir` and
  `molecule_instance_vars`. Check a new name against
  `molecule/provisioner/ansible.py` before using it, and never reuse one of Molecule's.
- **A create/destroy Molecule can't find is skipped, not an error.** `create`, `destroy`, `prepare`,
  `verify`, `cleanup`, `side_effect` are optional steps: with the `default` driver and no
  `create.yml`, `molecule test` "passes" without creating anything. The driver ships its own, so a
  scenario using it can't hit this; the `driver: default` fallback can.
- **Base config.** Molecule merges `.config/molecule/config.yml` from the VCS root (found from a role
  directory too), else `extensions/molecule/config.yml` in a collection, else `~/.config/molecule/`.
  It's read from the directory Molecule starts in, and lists in a scenario replace (not extend) the
  base's lists (`test_sequence`). `CRITICAL 'extensions/molecule/default/molecule.yml' glob failed`
  is Molecule looking for a `default` scenario: harmless.
- **`ansible.builtin.command` expands `$NAME` in its arguments** (`expand_argument_vars`, on by
  default since ansible-core 2.16), from the environment of the host the task runs on. PowerShell's
  `$_` matched the runner's `_` variable, so `$_.MacAddress` reached Windows as
  `/opt/hostedtoolcache/.../bin/molecule.MacAddress` (first `windows_cluster` CI run, PR #1). Every
  `command` task in the roles sets `expand_argument_vars: false`; keep it on new ones.
- **ansible-core `devel` isn't tested** (owner decision, 2026-11-01; the `sanity (devel, venv)`
  canary was removed). If it ever comes back: devel's test container failed to start on GitHub
  runners (remounting `/sys/fs/cgroup`, 2026-10-31), so it needs `ansible-test --venv`.
- **Windows Update Medic re-arms a disabled `wuauserv` shortly after boot** (start mode back to
  manual, service started), even with the box's "no automatic updates" policy, and an
  administrator can't stop it over the connection: Medic's service config is protected and a
  registry `Start` change waits for a reboot. Seen as idempotence failures: `windows_dc` on PR #9's
  first run (first blamed on the feature install and reboot; `ad_forest` re-quiets at the end) and
  node1 in PR #19's `windows_cluster`, eight seconds after the first converge. It has been a
  one-shot repair, so `quiet_vm` waits `quiet_vm_settle_seconds` after a change, reads the
  service back and quiets it once more (`quiet_vm_wuauserv_rearmed`). If it ever loops, the
  question is the contract (run it where consumers do, in prepare), not more code.
- **Windows can refuse to stop `wuauserv` right after first boot** (trigger-start or
  `StopPending`): `win_service state: stopped` fails with "Service 'Windows Update (wuauserv)' stop
  failed" (issue #18, consumers' prepare steps on 0.3.0). `quiet_vm` disables first, retries the
  stop, and warns rather than fails if Windows still refuses; it records
  `quiet_vm_wuauserv_stopped` as a cacheable fact, which the Windows scenarios' verify reads
  through their jsonfile fact cache (`ANSIBLE_CACHE_PLUGIN` in their `molecule.yml`).
- **ansible-core 2.19+ templates an `assert`'s `fail_msg` even when the assertion passes.** A
  `fail_msg` that dereferences a key the assertion is checking for (`{{ item.kind }}`) errors on the
  good path; use `default()` in messages.
- **A privileged systemd container clears the host's binfmt_misc** (its `systemd-binfmt.service`),
  and every emulated `linux/amd64` container on that host then fails with `exec format error`, until
  `docker run --privileged --rm tonistiigi/binfmt --install amd64`. On Apple silicon this is why
  amd64 emulation "vanishes". Mask the unit in such containers:
  `volumes: [/dev/null:/etc/systemd/system/systemd-binfmt.service:ro]` (see `docker_full`). Found
  2026-10-31.
- **Molecule copies the project collection into `~/.ansible/collections` on every run** (the
  `Collection ... detected` step), replacing whatever another checkout put there. Scenarios must keep
  `.molecule-collections` first in `ANSIBLE_COLLECTIONS_PATH`, or they test a stale copy.
- `ansible-galaxy collection install <dir>` honours `build_ignore`; keep the Python package and
  build output in it.
- **`platform_defaults` can't hold `name`, `groups` or `children`.** Molecule builds the inventory
  (and `molecule list`) from the platform entries as written, before the driver merges anything, so
  defaults for those would reach the kinds but not the inventory. `create.yml` refuses them.
- **QEMU's `-daemonize` discards its output and exit status.** Once detached it points stdout and
  stderr at /dev/null, and nothing waits for it, so a VM whose QEMU vanished during first boot left
  nothing to say why. It happened in the windows repo's three-VM `wsfc_fileserver` lab on 2 of about 9
  runs (windows PR #36: dc01 in run 41334289914, node1 in run 41364643685), and wasn't OOM. Hence the
  supervisor (0.4.0, issue #13). Don't add `-daemonize` back: QEMU would leave the supervisor, which
  would then log an exit that never happened.
- **A kill still ends QEMU under `-no-shutdown`.** A host signal overrides the shutdown action (QEMU's
  `qemu_system_killed`), so destroy's SIGTERM works; the lifecycle test checks it. Only a guest that
  powers itself off stays, paused in the "shutdown" state.
- **Some runners come up short of disk, and `/mnt` is no disk of its own.** On `ubuntu-latest`
  (2026-11), `/mnt` is a directory on the root filesystem: usually `/dev/root`, 145 GB with about
  85 GB free, but some runners come up with a 72 GB `/dev/mapper/cow-root` and 15.7 GB free
  (actions/runner-images#15238). There `windows_vm`'s create refuses ("/mnt/molecule-lab has
  15.7 GB free; this run needs 20.0 GB") before any VM boots: the windows collection's
  `domain-lab` lost random jobs to it (git-cgallagher/ansible-collection-windows#62).
  `windows-lab.yml`'s `molecule` job starts with "Make room for the lab", the same step as the
  consumers' Windows labs (windows' `domain-lab.yml`, its PR #63, and isc's `windows-dc.yml`): it
  measures `/mnt` and, under 30 GB, deletes preinstalled toolchains the lab never uses and moves the
  lab to `$HOME/molecule-lab` through `$GITHUB_ENV`. Later steps read the path from
  `${{ env.MOLECULE_LAB_DIR }}` (or `$MOLECULE_LAB_DIR`), so never write `/mnt/molecule-lab` into a
  step; the `lifecycle` job boots no box and keeps `/mnt`. "Show the lab's disk use" prints what
  the lab holds at the end. Measured 2026-11-05 (runner image 20261101.360.1): a `workflow_dispatch`
  run that first left every runner 15.7 GB free (run 42095393550) passed all three jobs this way.
  The cleanup freed 25.2 GB in 50 to 143 s (Android SDK 11.0, .NET 5.7, GHCup 3.7, Swift 3.5,
  CodeQL 1.3; `/opt/ghc` is gone), and the labs ended at 6.8 GB (`windows_dc`: the 5.6 GB box plus
  dc01's 1.3 GB disk) and 7.3 GB (`windows_cluster`: box, node1 0.7, node2 1.0), with 33 to 34 GB
  left. A normal run (42095300651) had 85.8 GB free, kept `/mnt/molecule-lab` and ended at 7.0 and
  8.7 GB: a VM's disk ends anywhere from 0.7 to 2.4 GB. A consumer's lab needs the step too:
  setup-molecule's `lab-dir` only creates the directory.
  A real `cow-root` runner is provisioned differently (copy-on-write, going by the device name;
  unconfirmed), so read the step's log the first time a run lands on one.
- **To apt, a crawling mirror isn't a failing one.** GitHub's Ubuntu runners read their mirrors from
  `/etc/apt/apt-mirrors.txt` (`mirror+file:` sources): `http://azure.archive.ubuntu.com/ubuntu/`
  at `priority:1`, then `https://archive.ubuntu.com/ubuntu/` and
  `https://security.ubuntu.com/ubuntu/` (the same Canonical servers). apt's mirror method
  (`methods/mirror.cc`) tries the lowest priority first and moves on only when a download fails.
  On 2026-11-05 from 18:43 UTC, every westus2 runner got QEMU's `.deb`s from the Azure mirror at
  about 40 kB/s while `apt-get update` from the same host took a second: 8 lab jobs hit 0.5.0's
  8-minute limit (5 in windows' `domain-lab`, 3 `windows_vm lifecycle` jobs here) and 3 got
  through in 7 to 8 minutes; that morning westus2 had taken 2 to 3 s, and 76 jobs in other
  regions took 0 to 16 s. apt's own `Acquire::http::Timeout` counts only silence, and apt has no
  minimum speed (`Dl-Limit` is a cap), so tuning apt can't catch a crawl: hence setup-molecule's
  cache, its short first limits, and the retry without the first mirror (0.5.1).
- **apt takes a `.deb` already in its archives directory on its size alone.** apt 2.8.3's
  `pkgAcqArchive::QueueNext` (`apt-pkg/acquire-item.cc`) marks a file of the right size done with
  no hash check; apt checks hashes only on what it downloads. In the local test of 0.5.1 a cached
  `seabios` with one byte changed reached dpkg ("zstd error: Data corruption detected"); a planted
  `.deb` of the right size would have been installed. So `apt_install.sh` checks each cached file
  against the SHA256 from the signed package lists (`--print-uris` prints MD5 unless given
  `-o Acquire::ForceHash=SHA256`) before copying it there.
- **Docker's `ubuntu` images delete `.deb`s after every dpkg run** (`/etc/apt/apt.conf.d/docker-clean`),
  so in a container the archives are gone after the install. Runners keep them, but
  `apt_install.sh` copies the downloaded files for the cache before installing either way.
- **Jinja's `'\n'` stays a literal backslash-n in an Ansible template** (ansible-core 2.21.5, seen in
  the first-boot failure message). In a folded block (`>-`), a blank line is what makes a newline.
- **Installing this collection from a checkout downloads its dependencies.** `ansible-galaxy
  collection install --force <checkout>` resolves `galaxy.yml`'s ansible.windows, microsoft.ad and
  community.docker (plus its community.library_inventory_filtering_v1) from Galaxy unless they're
  installed already: in the consumers' lab logs those were the first four Galaxy downloads, before any
  `requirements` file. Hence 0.5.0 copies the image's collections first and installs with `--offline`.
- **ansible-galaxy only contacts Galaxy for what isn't installed** (ansible-core 2.21.5). A
  requirement an installed collection satisfies, in any configured path or on the Python path, gives
  "Nothing to do" without opening a socket: `install_collections` returns before resolving, and the
  resolver skips the API for installed candidates (`dependency_resolution/providers.py`). That's why
  Molecule's `dependency` step and prerun download nothing once setup-molecule has installed
  everything. An unsatisfied one downloads, unless `--offline`, which fails it instead ("Could not
  satisfy the following requirements"). Checked with `--network none` (2026-11-02).
- **`ansible-galaxy collection list` lists `ansible._protomatter`**, ansible-core's internal
  collection under `ansible/_internal/ansible_collections`, so it's no way to find an image's
  collection directory. `image_collections.sh` asks Python for `ansible_collections.__path__`, which
  holds only the directories on `sys.path`.

## Verified (2026-10-31 to 2026-11-02)

- On a Mac (Docker Desktop 29.7.2, arm64; Molecule 26.10.0, ansible-core 2.21.5): `docker_smoke` and
  `docker_full` pass `molecule test` with no create/destroy files; unknown keys fail for both kinds;
  `windows_vm` validates keys and expands forwards, then stops at the `/dev/kvm` check, as designed.
- isc `kea_dhcp/ha3` (3 emulated amd64 nodes, static-IP network, HA failover verify) passes
  `molecule test` on the driver, in a scratch clone of isc `main` 9d20478 with
  `docs/isc-scenarios.patch` applied, and so does `kea_dhcp/default --platform-name kea-debian12`
  (only that container is created); isc's converted Windows scenario validates up to `/dev/kvm`.
- After the rename to `git_cgallagher.molecule` / `molecule-git-cgallagher`: lint, sanity,
  `docker_smoke`, `docker_full` and isc `kea_dhcp/default --platform-name kea-debian12` re-run green.
- A non-editable `pip install .` (what setup-molecule does) ships `playbooks/*.yml` and
  `schema/driver.json`, and `molecule --version` lists `molecule-git-cgallagher`.
- Lab networks, on the Mac: `lab_hub.py` repeats each frame to every other member and refuses a
  taken port; started through the role's own `hubs.yml` for three VMs, only the first VM starts the
  hub, a second create starts nothing, frames cross it, and `destroy.yml` stops it. The NIC
  calculation gives distinct hub blocks, member ports in platform order and unique MACs, and the
  rendered NIC script parses in PowerShell 7 and passes PSScriptAnalyzer. Duplicate lab addresses,
  clashing host ports and bad `lab_networks` keys fail with a message.
- In CI (PR #1, the initial collection; GitHub `ubuntu-latest`, KVM): the setup-molecule action
  installs everything from its own ref, and every check passes, including both `windows-lab` jobs:
  `windows_dc` (a Windows Server 2022 VM promoted to a new forest by `ad_forest`, its DNS queried
  from a host-network container through the forward; ~10 min) and `windows_cluster` (two VMs booted
  side by side; node1 starts the `cluster` hub, node2 finds it; the ready step configures both lab
  NICs; each node reaches the other's WinRM port over the lab network; ~6 min).
- Released as `v0.1.0` (annotated tag on merge commit `d9421b5`): the README's pip and
  ansible-galaxy install lines both work against the tag (the driver registers with Molecule, the
  `windows` extra brings `pypsrp`, the collection installs with its three roles).
- 0.2.0, on the Mac (same versions): `docker_smoke` (both platforms from `platform_defaults`; the
  verify checks that smoke1 runs the default image) and `docker_full` pass; `windows_cluster`, its box
  now from `platform_defaults`, passes its key, port and NIC checks and stops at `/dev/kvm`.
  `platform_defaults` with `groups`, two VMs inheriting one `winrm_port`, and a docker platform
  inheriting `box_url` each fail with a message. The merge expression gives the same platforms on
  ansible-core 2.18.19 and 2.21.5, and the built tarball ships `playbooks/vars/driver.yml`.
- isc on the driver (isc PR #18, 2.0.1, merged 2026-10-31 as `39d33e1`): all 22 required checks
  pass on v0.2.0, so the setup-molecule action and `constraints` work in a real consumer, as do the
  7 docker scenarios, including the privileged systemd ones and the 7-platform matrix. The manual
  `windows-dc` run on the PR branch also passed (isc run 41262871072): a Windows Server 2022 VM
  booted through `windows_vm` and was promoted by `ad_forest`, and Kea's GSS-TSIG updates were read
  back from Microsoft DNS.
- orchestration on the driver (orchestration PR #15, merged 2026-11-01 as `6334663`): all 16 checks
  pass, devel included, with its four docker platforms from `platform_defaults`.
- windows on the driver (windows PR #23, merged 2026-11-01 as `7584116`): every check passes,
  including all four `domain-lab` jobs (windows run [41320266419](https://github.com/git-cgallagher/ansible-collection-windows/actions/runs/41320266419)). Each boots its VM through
  `windows_vm` from the base config's `platform_defaults`: gpo_backup and gpo_import on a DC promoted
  by `ad_forest` (with GPMC, a SYSVOL-ready test and the SYSVOL path wait), wsfc's one-node
  workgroup cluster, and the IIS modules' targets on `iis01`, a scenario that names only its VM.
- 0.2.1's apt limits, in an `ubuntu:24.04` container with the helper exactly as the action has it,
  under `bash -eo pipefail` (as GitHub runs a step) and a fake `apt-get`: a hang stops at the limit
  (rc 124) and one that ignores SIGTERM is killed 30 seconds later (rc 137), both with the error
  message; a plain apt error keeps its own code (100) with no message, and a success carries on.
- 0.3.0, on the Mac: lint, sanity (34 tests) and `docker_smoke` pass (`docker_full` couldn't: amd64
  emulation had vanished, the binfmt gotcha above; CI's job is the proof); `windows_dc` and
  `windows_cluster` run `dependency` and `syntax` on the new role and tasks, then stop at `/dev/kvm`.
  The account expression gives `vagrant` for `vagrant` and `MOLECULELAB\vagrant` and `fallback` for
  a `LAB\fallback` user fact. CI's `windows-lab` jobs are the Windows proof (see the PR).
- 0.3.1, on the Mac: lint, sanity and `docker_smoke` pass. The Tamper Protection branch of the
  Defender script (write ignored, warning, no change) can't be exercised on the lab box, which has
  Tamper Protection off; CI's `windows_dc` and `windows_cluster` prove the normal branch.
- 0.3.2, on the Mac: lint, sanity and `docker_smoke` pass. The stop's rescue logic, run against a
  simulated failure on localhost: a refused stop ("... stop failed") retries, warns and records
  `false` without failing; any other error still fails; a clean stop records `true`; the fact
  reaches a second playbook run through the jsonfile cache. Molecule merges the scenarios'
  cache env with the base config's. The settle-and-reassert flow, with the Windows modules stubbed
  on localhost: a re-arm runs one more pass, no re-arm skips it, an unchanged run skips the settle,
  and check mode skips the read-back. Windows refusing the stop, or Medic's repair, can't be forced
  on the lab box; CI's `windows_dc` and `windows_cluster` are the proof, and since the repair is
  probabilistic one green run is evidence, not certainty.
- aws on the driver (aws PR #4, merged 2026-11-01 as `465f104`): its first scenario,
  `ssm_parameter`, passes locally and in CI inside the ansible-runtime image (`0.2.1-ci`), with two
  docker platforms on one network from `platform_defaults` (`docker_networks` + `networks`) and moto
  keeping its own entrypoint through `override_command: false`.
- 0.4.0, before CI: `.github/ci/windows_vm_lifecycle.sh` passes against QEMU 8.2.2 (Ubuntu 24.04's,
  in a container on the Mac, so TCG). A bad option fails the supervisor with QEMU's own message and
  "exited with status 1". A running VM polls 3, `stop` 11 ("paused"), the deadline 12 ("QEMU status:
  running; WinRM port 1 is closed"). SIGTERM ends QEMU under `-no-shutdown` ("terminating on signal
  15", exit 0) and the next poll says 10; SIGKILL is logged as "killed by signal 9 (SIGKILL)". The
  failure message renders for rc 10, 11, 12 and 124, and passes cleanly on 0; the old poll count maps
  to seconds (120 → 1200). `vm-screendump`'s script, run as GitHub runs it (`bash -eo pipefail`)
  against that QEMU, prints "VM status: running" and the log (QEMU's own warnings included), "no
  monitor socket" for a VM that never existed, and "no answer from the monitor" after a SIGKILL,
  exiting 0 each time. yamllint, ansible-lint (production), actionlint, shellcheck and sanity pass
  (ansible-core 2.21.5).
- 0.5.0, before CI: the install step, extracted verbatim from `action.yml` and run as GitHub runs a
  step (`bash -eo pipefail`) in an `ubuntu:24.04` container (Python 3.12, the image's constraints,
  so ansible-core 2.21.5 and Molecule 26.10.0) with the Docker socket mounted and galaxy.ansible.com
  mapped to 0.0.0.0, against `ansible-runtime:1.1.0-ci@sha256:cc04da98…` (the consumers' ref shape)
  and `1.2.0-ci@sha256:209b64e1…`. It copies the image's seven collections (site-packages under
  Python 3.13, found by asking the image), skips the image's `git_cgallagher.molecule` 0.3.2,
  installs the checkout's `--offline`, and `extensions/molecule/requirements.yml` is "Nothing to do":
  rc 0, no Galaxy line in the log. A requirements file naming community.crypto, and one asking for
  community.docker >=6.0.0 (the image has 5.4.0), fail with ansible-galaxy's "Could not satisfy" and
  the action's error, without contacting Galaxy. `python:3.13-slim` and `ubuntu:24.04` as the image
  fail with "no ansible_collections directory on its Python path"; the runtime target
  (`ansible-runtime:1.2.0@sha256:e64b914a…`) fails naming its five other `git_cgallagher.*`
  collections, before anything is copied. A stale community.docker 4.0.0
  with an extra file in `~/.ansible/collections` is replaced (the file is gone), and a second run
  leaves the tree byte-identical. Without `collections-image` the step takes the old branch: four
  Galaxy downloads, rc 0. With no network at all (`--network none`), ansible-galaxy said "Nothing to
  do" for satisfied requirements with and without `--offline`, and `--offline` failed unsatisfied
  ones (missing collection, version too new, exact pin mismatch, a missing dependency of the
  checkout) without trying the network. `image_collections.sh` also runs on macOS (BSD tools; no
  `timeout`, so a pull isn't limited there). shellcheck 0.9.0 is clean on the script and on the
  step's new lines (its two findings, the pip extra `$root[windows]` and `EXTRA`, predate 0.5.0);
  actionlint, yamllint and ansible-lint (production) pass.
- 0.5.0 in CI and in the labs that use it (2026-11-02). Every check on PR #22's head `fe24140`
  passed with galaxy.ansible.com mapped to 0.0.0.0, including the KVM `windows_dc` and
  `windows_cluster` jobs. Their logs show the image's collections copied, "Nothing to do" from the
  action's check and from Molecule's `dependency` step, and no Galaxy API call or download. PR #22
  merged as `29152ec`, with the same tree as `fe24140`, which carries the tag. Each consumer lab
  passed with no `galaxy.ansible.com` line in its logs:
  - windows `domain-lab`, all six jobs: windows PR #56, merged as `2125d1d`; runs
    41514013814 and 41521829034, the latter's gpo_backup re-run after a first-boot flake (TODO
    below).
  - isc `windows-dc`: isc PR #34, merged as `f28ffd7`; manual run 41524754716, by the ansible-runtime
    maintainer.
  ansible-runtime 1.3.0 (its PR #18, `278adc9`) pins the driver at `fe24140`.
- **Not verified yet:** lab networks with more than two VMs or more than one network per VM. A real
  guest powering itself off (state "shutdown"): the lifecycle test's `stop` ("paused") stands in for it.

## TODO

- Shared disks for cluster storage (WSFC cluster disks need SCSI-3 persistent reservations) and
  extra data disks per VM: not supported yet.
- PyPI publishing for the driver (trusted publishing). Optional while consumers pin the action SHA.
  (Galaxy is out: the collection stays off it, see Releasing.)
- `molecule reset` support (remove containers/networks labelled `owner=molecule`).
- Drop `windows_vm_first_boot_retries` (deprecated in 0.4.0, mapped to 10 s polls) once no consumer
  sets it; windows PR #36's `wsfc_fileserver` scenario did, at 180.
- A guest whose own first-boot setup fails ("Windows Setup could not configure Windows to run on
  this computer's hardware", in the screendump) keeps WinRM's port open, so `ready` waits out the
  whole 30-minute deadline. Seen three times on 2026-11-02, in windows runs 41464369281 and
  41521829034 and isc run 41512670995; a re-run cleared each. Logging the runner's CPU model
  (`lscpu`) with the lab diagnostics would show whether it follows a host CPU type under
  `-cpu host`; retrying such a VM's first boot once, on a fresh overlay, is another option.
