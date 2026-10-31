# AGENTS.md — ansible-collection-molecule

Read `~/Code/AGENTS.md` first. This repo ships three things from one tag:

| Path | What | Ships via |
|---|---|---|
| `molecule_git_cgallagher/`, `pyproject.toml` | the `molecule-git-cgallagher` Molecule driver: name, schema, bundled create/destroy that import the collection's playbooks | pip (git URL; PyPI later) |
| `playbooks/`, `roles/`, `galaxy.yml` | collection `git_cgallagher.molecule`: create/destroy dispatch, kinds `docker` and `windows_vm`, role `ad_forest` | Galaxy / git |
| `.github/actions/setup-molecule`, `vm-screendump` | composite actions the consumer repos pin by SHA | GitHub |
| `extensions/molecule/` | self-tests: `docker_smoke`, `docker_full` (any Docker host), `windows_dc` and `windows_cluster` (KVM only); `config.yml` is their shared base config | not shipped |
| `docs/MIGRATION.md` | the per-repo plan for isc and windows (formerly gpo) | — |

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
- `playbooks/create.yml` loops over `molecule_yml.platforms` twice, including the role named after
  each platform's `kind` (default `docker`): entry point `create` for every platform, then `ready` for
  every platform. Destroy loops once, in reverse, with `destroy`.
- **Kind contract.** A kind role reads `molecule_platform` and `molecule_lab_dir`. Its `create`
  asserts the platform's keys against its own list (`vars/main.yml`; unknown keys fail), starts the
  platform without waiting, and appends instance entries (`instance`, `address`, `connection`, and
  `port`/`user`/`password` when needed) to `<kind>_instances`; its `ready` waits until the platform is
  usable. The driver subclasses Molecule's delegated driver, which maps those entries to
  `ansible_host`/`ansible_port`/`ansible_connection`/... in the inventory. New kind = new role + its
  name in `molecule_kinds` in both playbooks + a self-test.
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

## Releasing

Merge to `main` with `galaxy.yml` and `pyproject.toml` versions bumped together and a CHANGELOG entry,
then tag `vX.Y.Z` on the merge commit. Consumers pin the setup-molecule action's commit SHA (with a
`# vX.Y.Z` comment), which installs the driver and the collection from that same commit. Galaxy and
PyPI publishing aren't set up yet (TODO below).

## ⚠️ Gotchas

- **Driver names must match `molecule-*` or `custom-*`.** Molecule's `molecule.json` schema only
  allows its built-in names or those patterns, so a bare `name: molecule` fails validation before the
  driver loads, and the pip name `molecule` is Molecule itself: hence `molecule-git-cgallagher`
  (2026-10-31, when Chris named the collection `molecule`). The driver also overrides `schema_file()`
  (its `driver.json` allows only `molecule-git-cgallagher`).
- **Our variables share Molecule's `molecule_` prefix.** Molecule itself injects `molecule_file`,
  `molecule_yml`, `molecule_ephemeral_directory`, `molecule_scenario_directory`,
  `molecule_instance_config` and `molecule_no_log`; ours are `molecule_platform`, `molecule_kinds`,
  `molecule_lab_dir` and `molecule_instance_vars`. Check a new name against
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

## Verified (2026-10-31)

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
- **Not verified yet:** `windows_vm` booting a VM, `ad_forest`, and two VMs on a lab network (CI
  `windows-lab`: `windows_dc`, `windows_cluster`); QEMU's socket netdev against the hub (no QEMU on the
  Mac); the setup-molecule action (first CI run); and the isc and windows repos' Windows scenarios on
  the driver.

## TODO

- Publish the repo (public baseline), then run `ci` and `windows-lab` once and add the stable
  checks to the ruleset.
- Shared disks for cluster storage (WSFC cluster disks need SCSI-3 persistent reservations) and
  extra data disks per VM: not supported yet.
- Galaxy publishing (`GALAXY_API_KEY` in a main-only `galaxy` environment, as in the other
  collections) and PyPI (trusted publishing). Optional while consumers pin the action SHA.
- `molecule reset` support (remove containers/networks labelled `owner=molecule`).
- isc and windows (formerly gpo) migrations: `docs/MIGRATION.md`.
