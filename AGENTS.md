# AGENTS.md — ansible-collection-molecule

Read `~/Code/AGENTS.md` first. This repo ships three things from one tag:

| Path | What | Ships via |
|---|---|---|
| `molecule_git_cgallagher/`, `pyproject.toml` | the `molecule-git-cgallagher` Molecule driver: name, schema, bundled create/destroy that import the collection's playbooks | pip (git URL; PyPI later) |
| `playbooks/`, `roles/`, `galaxy.yml` | collection `git_cgallagher.molecule`: create/destroy dispatch, kinds `docker` and `windows_vm`, roles `ad_forest` and `quiet_vm` | Galaxy / git |
| `.github/actions/setup-molecule`, `vm-screendump` | composite actions the consumer repos pin by SHA | GitHub |
| `extensions/molecule/` | self-tests: `docker_smoke`, `docker_full` (any Docker host), `windows_dc` and `windows_cluster` (KVM only); `config.yml` is their shared base config, `tasks/` their shared verify tasks | not shipped |
| `docs/MIGRATION.md` | the migration record: isc (2.0.1), orchestration and windows (formerly gpo), all on the driver | — |

## Commands

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[windows]' ansible-core ansible-lint yamllint
ansible-galaxy collection install -r extensions/molecule/requirements.yml
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
`# vX.Y.Z` comment), which installs the driver and the collection from that same commit. Galaxy and
PyPI publishing aren't set up yet (TODO below).

Which commit gets the tag:

- **The PR's head**, if `main` didn't move while the PR was open, so the head's tree equals the
  merge commit's (`git rev-parse <head>^{tree} <merge>^{tree}`). A consumer PR can then pin that SHA
  and prove it in its own CI before the release, with nothing to re-pin afterwards. `v0.2.0` is
  `b2601b3`, the head of PR #4; its merge commit `552ba75` has the same tree, and isc PR #18 was
  tested and merged on that pin.
- **The merge commit** otherwise (`v0.1.0` is `d9421b5`).

## Repo settings (public-repo baseline, applied 2026-10-31)

- Ruleset **"main protection"** (id `26286539`) on the default branch: active, no bypass actors,
  deletion and force-push blocked, PR required with 0 approvals, and these checks required (GitHub
  Actions, integration `15368`): `lint`, `sanity (stable-2.18)`, `sanity (stable-2.19)`,
  `sanity (stable-2.20)`, `sanity (stable-2.21)`, `molecule docker_smoke`, `molecule docker_full`.
  Not required, on purpose: `sanity (devel, venv)` (an upstream canary) and the two `windows-lab`
  jobs (path-filtered, and they depend on a third-party box download). Still merge only when those
  are green too: merges are by hand, and repo auto-merge is off.
- Secret scanning + push protection, Dependabot alerts + security updates, private vulnerability
  reporting (`SECURITY.md`), CodeQL default setup (`python`, `actions`), delete branch on merge;
  wiki and projects off. Actions are SHA-pinned; `.github/dependabot.yml` keeps them current.
- Every CI job sets `timeout-minutes` (2026-11-01): GitHub's default is 6 hours, and a stalled
  install would hold the runner that long. `ci.yml`'s jobs get 15 (30 for Molecule), several times
  their slowest green run; `windows-lab` keeps 120. A new job needs one too.
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
- **ansible-test's devel container can't start on GitHub runners** (it fails remounting
  `/sys/fs/cgroup`, 2026-10-31, in isc too), so `sanity (devel, venv)` runs `ansible-test --venv`
  on a plain runner, as git_cgallagher.orchestration does.
- **Windows re-arms `wuauserv` after a feature install and reboot** (the servicing stack starts it
  and resets its start mode). `quiet_vm` at the start of `ad_forest` alone made `windows_dc`'s
  idempotence step fail (PR #9's first run), so the role includes it again at the end.
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

## Verified (2026-10-31 to 2026-11-01)

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
- **Not verified yet:** lab networks with more than two VMs or more than one network per VM.

## TODO

- Shared disks for cluster storage (WSFC cluster disks need SCSI-3 persistent reservations) and
  extra data disks per VM: not supported yet.
- Galaxy publishing (`GALAXY_API_KEY` in a main-only `galaxy` environment, as in the other
  collections) and PyPI (trusted publishing). Optional while consumers pin the action SHA.
- `molecule reset` support (remove containers/networks labelled `owner=molecule`).
