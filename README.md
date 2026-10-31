# git_cgallagher.testlab — a shared Molecule driver

One Molecule driver for every `git_cgallagher` collection and role. A scenario
only describes its platforms; the driver creates and destroys them.

- **`molecule-testlab`**, a Molecule driver plugin (pip). Its bundled create and
  destroy import the collection's playbooks, so scenarios need no `create.yml`
  or `destroy.yml`.
- **`git_cgallagher.testlab`**, the collection that does the work (Galaxy/git).
  It dispatches each platform by `kind` to the role of that name:

  | kind | role | creates | needs |
  |---|---|---|---|
  | `docker` (default) | `git_cgallagher.testlab.docker` | a container through the docker CLI, plus its networks and Dockerfile build | Docker |
  | `windows_vm` | `git_cgallagher.testlab.windows_vm` | a Windows Server Vagrant box under QEMU/KVM, WinRM and other ports forwarded to 127.0.0.1 | Linux x86-64 with `/dev/kvm`, ~20 GB disk |

  Plus **`git_cgallagher.testlab.ad_forest`**, a prepare-time role that promotes a
  `windows_vm` to the first DC of a new forest.
- **`setup-molecule`**, a composite GitHub Action that installs Python, ansible-core,
  Molecule, the driver and the collection from one pinned ref (and KVM/QEMU when asked).

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
  name: molecule-testlab
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
`memory_mb` (4096), `cpus` (2), `forwards`. The box is cached by checksum in the lab directory: the
`testlab_dir` inventory variable, else `$TESTLAB_DIR`, else `~/.cache/testlab`.

## Install

Locally, at one tag:

```bash
pip install "molecule-testlab[windows] @ git+https://github.com/git-cgallagher/ansible-collection-testlab@v0.1.0"
```

```bash
ansible-galaxy collection install git+https://github.com/git-cgallagher/ansible-collection-testlab.git,v0.1.0
```

In GitHub Actions, one step (pin the SHA; Dependabot keeps it current):

```yaml
- uses: git-cgallagher/ansible-collection-testlab/.github/actions/setup-molecule@<sha>  # v0.1.0
  with:
    collection: git_cgallagher.isc
    requirements: requirements.yml
    # kvm: 'true'          # windows_vm platforms
    # lab-dir: /mnt/lab    # their box cache and disks
- run: molecule test -s ha3
  working-directory: roles/kea_dhcp
```

Without the pip package, a scenario can use `driver: default` (`options: {managed: true}`) and a
`create.yml`/`destroy.yml` that each hold one line,
`ansible.builtin.import_playbook: git_cgallagher.testlab.create` (or `.destroy`).

## Adding a kind

A kind is a role in this collection with `create` and `destroy` entry points. It reads
`testlab_platform` (one `molecule.yml` platform) and `testlab_lab_dir`, checks the platform's keys,
appends its Molecule instance-config entries to `<kind>_instances`, and is listed in `testlab_kinds`
in `playbooks/create.yml` and `playbooks/destroy.yml`.

## License

MIT
