# Changelog

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
