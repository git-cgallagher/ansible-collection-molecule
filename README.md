# git_cgallagher.molecule — a shared Molecule driver

One Molecule driver for every `git_cgallagher` collection and role. A scenario
only describes its platforms; the driver creates and destroys them.

- **`molecule-git-cgallagher`**, a Molecule driver plugin (pip). Its bundled create and
  destroy import the collection's playbooks, so scenarios need no `create.yml`
  or `destroy.yml`. (It can't be called plain `molecule`: Molecule only accepts
  third-party driver names starting `molecule-` or `custom-`, and the pip package
  `molecule` is Molecule itself.)
- **`git_cgallagher.molecule`**, the collection that does the work (Galaxy/git).
  It dispatches each platform by `kind` to the role of that name:

  | kind | role | creates | needs |
  |---|---|---|---|
  | `docker` (default) | `git_cgallagher.molecule.docker` | a container through the docker CLI, plus its networks and Dockerfile build | Docker |
  | `windows_vm` | `git_cgallagher.molecule.windows_vm` | a Windows Server Vagrant box under QEMU/KVM, WinRM and other ports forwarded to 127.0.0.1; several per scenario, on shared lab networks for clusters | Linux x86-64 with `/dev/kvm`, ~20 GB disk |

  Plus two prepare-time roles for Windows labs: **`git_cgallagher.molecule.ad_forest`**
  promotes a `windows_vm` to the first DC of a new forest, and
  **`git_cgallagher.molecule.quiet_vm`** stops Windows Update and Defender real-time scanning on
  any test VM (`ad_forest` runs it first; see [Quieting a test VM](#quieting-a-test-vm)).
- **`setup-molecule`**, a composite GitHub Action that installs Python, ansible-core,
  Molecule, the driver and the collection from one pinned ref (and KVM/QEMU when asked),
  optionally under the repo's pip constraints file.

## A scenario

`extensions/molecule/config.yml` (collection-level scenarios) or
`.config/molecule/config.yml` (per-role scenarios, or a standalone role repo) holds what every
scenario shares. Molecule merges it under each `molecule.yml`:

```yaml
# .config/molecule/config.yml (scenarios in roles/<role>/molecule/, run from the role)
dependency:
  name: galaxy
  options:
    requirements-file: ${MOLECULE_PROJECT_DIRECTORY}/../../requirements.yml
driver:
  name: molecule-git-cgallagher
provisioner:
  name: ansible
  env:
    ANSIBLE_COLLECTIONS_PATH: ${MOLECULE_PROJECT_DIRECTORY}/../../.molecule-collections:~/.ansible/collections
verifier:
  name: ansible
```

A scenario is then its platforms, a `converge.yml` and a `verify.yml`:

```yaml
# roles/kea_dhcp/molecule/ha3/molecule.yml
platforms:
  - name: kea1
    image: public.ecr.aws/amazonlinux/amazonlinux:2023-minimal
    platform: linux/amd64
    docker_networks:
      - name: kea-ha3
        ipam_config: [{subnet: 172.29.30.0/24, gateway: 172.29.30.1}]
    networks: [{name: kea-ha3, ipv4_address: 172.29.30.11}]
```

```yaml
# a Windows DC next to a container on the host network
platforms:
  - name: dc01
    kind: windows_vm
    box_url: https://vagrantcloud.com/jborean93/boxes/WindowsServer2022/versions/1.2.0/providers/libvirt/amd64/vagrant.box
    box_sha256: bc2e6a173390d4457e66ba30d5ffff7dbc87b849532bc0f41f4c1246f4e53bd4
    box_user: vagrant
    box_password: vagrant
    winrm_port: 5985
    forwards: [{port: 15353, guest_port: 53, protocols: [tcp, udp]}]
  - name: client
    image: python:3.13-slim
    network_mode: host
```

### Platform keys

**docker** uses molecule-plugins' docker-driver names, so existing platform definitions carry over:
`image`, `platform`, `dockerfile` (+ `pre_build_image: false`), `hostname`, `command`
(string or list; `""` keeps the image's CMD; default `sleep infinity`), `override_command`, `env`,
`privileged`, `cgroupns_mode`, `volumes`, `tmpfs`, `network_mode`, `networks`, `docker_networks`,
`published_ports`, plus Molecule's `groups`/`children`. **Any other key fails the run**, so a scenario
can't stay green while ignoring a setting it relies on. Differences from the docker driver: the
Dockerfile isn't a Jinja template, a built image keeps its own name (no `molecule_local/`), and
prebuilt images are always pulled (for `platform`, that architecture).

**windows_vm**: `box_url`, `box_sha256`, `box_user`, `box_password`, `winrm_port` (required),
`memory_mb` (4096), `cpus` (2), `forwards`, `lab_networks`. The box is cached by checksum in the lab
directory: `$MOLECULE_LAB_DIR` (a base config can set it in `provisioner.env`), else
`~/.cache/molecule-lab`.

### Keys every platform shares: `platform_defaults`

When a scenario sets `platforms`, Molecule replaces the base config's list rather than merging it, so
a box pin kept there would have to be repeated in every scenario that names its own VM. The driver's
`platform_defaults` option holds such keys once: each platform entry starts from them, and its own
keys win.

```yaml
# .config/molecule/config.yml
driver:
  name: molecule-git-cgallagher
  options:
    platform_defaults:
      kind: windows_vm
      box_url: https://vagrantcloud.com/jborean93/boxes/WindowsServer2022/versions/1.2.0/providers/libvirt/amd64/vagrant.box
      box_sha256: bc2e6a173390d4457e66ba30d5ffff7dbc87b849532bc0f41f4c1246f4e53bd4
      box_user: vagrant
      box_password: vagrant
      winrm_port: 5985
platforms:
  - name: dc01
```

```yaml
# a scenario's molecule.yml: its own VM, the same box
platforms:
  - name: iis01
```

The defaults go under every platform of the scenario, whatever its kind, and the kind checks the
merged entry's keys, so they suit scenarios whose platforms share a kind. They can't set `name`,
`groups` or `children`: Molecule builds its inventory from the entries as written. The option needs
`driver: molecule-git-cgallagher` (Molecule's schema for `driver: default` rejects options it
doesn't know).

### Several VMs, and clusters

A scenario can list several `windows_vm` platforms, each with its own `winrm_port` (and forward
ports; a clash fails the run). They all start before the driver waits for any of them, so they boot
side by side rather than one after another.

Each VM's first NIC is QEMU user networking: it carries WinRM and the `forwards`, all on 127.0.0.1.
It has the same address (10.0.2.15) on every VM and can't reach the others. For VM-to-VM traffic,
give the VMs a **lab network**:

```yaml
driver:
  options:
    platform_defaults:
      kind: windows_vm
      # box_url, box_sha256, box_user, box_password ...
platforms:
  - name: node1
    winrm_port: 15985
    lab_networks:
      - {name: cluster, ipv4_address: 10.42.0.11}      # prefix_length: 24 by default
  - name: node2
    winrm_port: 15986
    lab_networks:
      - {name: cluster, ipv4_address: 10.42.0.12}
```

VMs that list the same network name share an Ethernet segment, and a VM can be on several networks
(cluster and storage, say). The driver adds one NIC per network, names it after the network in
Windows, gives it the static address and files it as a Private network. On a VM with lab networks it
also renames the first NIC `management` and keeps it out of DNS registration. It needs no root: each network is a small UDP hub on 127.0.0.1
(`roles/windows_vm/files/lab_hub.py`) that QEMU's socket NICs talk to. Containers can't join a lab
network; they reach VMs through the forwards.

For a cluster lab, keep in mind:

- **DNS for a domain join.** The management NIC's DHCP DNS (10.0.2.3) can't resolve the lab
  domain. Point every adapter at the DC's lab address in prepare (`ansible.windows.win_dns_client`
  with `adapter_names: '*'`).
- **WSFC sees the management network on every node.** It has the same subnet on every node but
  can't carry traffic between them, so take it out of cluster use (`(Get-ClusterNetwork <name>).Role
  = 0`) and let the cluster run over the lab network.
- **Memory.** GitHub's standard Linux runner has 4 vCPUs and 16 GB: room for about three VMs at
  3–4 GB each (an estimate; the `windows_cluster` self-test runs two at 3 GB).

## Quieting a test VM

Windows Update and Defender real-time scanning slow a lab run down, and Windows Update can reboot
the VM under it. `quiet_vm` turns both off: the `wuauserv` service disabled, then stopped, through
`ansible.windows.win_service` (the stop is retried while the service is still starting after first
boot; if Windows keeps refusing, the role warns and leaves it disabled but running, and the
cacheable fact `quiet_vm_wuauserv_stopped` is `false`; and since Windows Update Medic can re-enable
and start it shortly after boot, which nothing an administrator can prevent, the role waits
`quiet_vm_settle_seconds` after a change and quiets it once more if that happened), and Defender's `DisableRealtimeMonitoring` preference set through a
small `win_powershell` (there is no module), which reads the preference first so it reports `changed`
only when the value flipped and predicts in check mode. A VM without Defender is left alone, and
one whose Tamper Protection ignores the write gets a warning, not a change on every run. Both
halves have a toggle (`quiet_vm_windows_update`, `quiet_vm_defender`, default `true`).

`ad_forest` includes it (`ad_forest_quiet: true`, the default), first and again after the promotion:
installing features and rebooting re-arm the Windows Update service, so a lab whose converge installs
features may want the include after that step too. A lab that doesn't promote a DC, such
as ansible-collection-windows' wsfc and IIS scenarios, includes it from its `prepare.yml` instead of
carrying the block itself:

```yaml
# roles/wsfc/molecule/default/prepare.yml
- name: Prepare a workgroup node
  hosts: all
  gather_facts: false
  tasks:
    - name: Wait for the connection
      ansible.builtin.wait_for_connection:
        timeout: 600

    - name: Quiet the VM (Windows Update off, Defender real-time scanning off)
      ansible.builtin.include_role:
        name: git_cgallagher.molecule.quiet_vm
```

The collection is already installed wherever the driver is (the setup-molecule action, or the
`ansible-galaxy` line below), so nothing else is needed. The `windows_cluster` self-test runs it this
way on two workgroup nodes, and `windows_dc` through `ad_forest`; both pass Molecule's idempotence
step.

## Install

Locally, at one tag:

```bash
pip install "molecule-git-cgallagher[windows] @ git+https://github.com/git-cgallagher/ansible-collection-molecule@v0.3.2"
```

```bash
ansible-galaxy collection install git+https://github.com/git-cgallagher/ansible-collection-molecule.git,v0.3.2
```

In GitHub Actions, one step (pin the SHA; Dependabot keeps it current):

```yaml
- uses: git-cgallagher/ansible-collection-molecule/.github/actions/setup-molecule@<sha>  # v0.3.2
  with:
    collection: git_cgallagher.isc
    requirements: requirements.yml
    # constraints: .cache/constraints.txt  # pip -c, e.g. the ansible-runtime image's pins (below)
    # pip: ansible-lint                          # extra packages
    # kvm: 'true'          # windows_vm platforms (apt-get time-limited: 3 min update, 8 min install)
    # lab-dir: /mnt/lab    # their box cache and disks
- run: molecule test -s ha3
  working-directory: roles/kea_dhcp
```

The `git_cgallagher.*` collections take `constraints` from the
[ansible-runtime](https://github.com/git-cgallagher/ansible-runtime) image their CI pins, so a lab
runs the same versions as their jobs in the image. Read it in a step before this one:
`docker run --rm "$RUNTIME_IMAGE" cat /usr/share/ansible-runtime/constraints.txt > .cache/constraints.txt`.

Without the pip package, a scenario can use `driver: default` (`options: {managed: true}`) and a
`create.yml`/`destroy.yml` that each hold one line,
`ansible.builtin.import_playbook: git_cgallagher.molecule.create` (or `.destroy`).

## Adding a kind

A kind is a role in this collection with `create`, `ready` and `destroy` entry points. Each reads
`molecule_platform` (one `molecule.yml` platform, over `platform_defaults`) and `molecule_lab_dir`,
and `molecule_platforms` (every platform, the same way) if it needs the others: `windows_vm` checks
host ports and lab addresses across them. `create` checks the platform's keys, starts it and appends
its Molecule instance-config entries to `<kind>_instances`; `ready` waits until it's usable (every
platform's `create` runs before any `ready`). The kind is listed in `molecule_kinds` in
`playbooks/vars/driver.yml`.

## License

MIT
