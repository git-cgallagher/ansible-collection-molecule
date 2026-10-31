# Changelog

## 0.1.0

First release: the molecule-testlab driver (pip), the git_cgallagher.testlab collection and the
setup-molecule action, from one repo and one tag.

- `molecule-testlab` driver: bundled create/destroy import the collection's playbooks, so a
  scenario is its platforms plus converge/verify.
- Kind `docker`: containers through the docker CLI, with networks (static IPs), Dockerfile builds,
  published ports, systemd containers and `platform` pulls; molecule-plugins' docker-driver key
  names; unknown keys fail.
- Kind `windows_vm`: a Windows Server Vagrant box under QEMU/KVM with WinRM and extra ports
  forwarded to 127.0.0.1 (from the copies in git_cgallagher.gpo's and git_cgallagher.isc's
  Windows scenarios).
- Role `ad_forest`: promote a test VM to the first DC of a new forest.
- Actions `setup-molecule` and `vm-screendump`.
- Self-tests: `docker_smoke`, `docker_full` (every docker key; CI and any Docker host) and
  `windows_dc` (KVM; the `windows-lab` workflow).
