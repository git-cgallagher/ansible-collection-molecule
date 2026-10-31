# Moving the git_cgallagher repos onto molecule-git-cgallagher

Status 2026-10-31: proposal. Nothing in the consumer repos has changed yet.

## Which repos

| Repo | Molecule today | Change |
|---|---|---|
| `ansible-collection-isc` | 8 scenarios in `roles/<role>/molecule/` (moved there in PR #13, merged 2026-10-31): 7 on molecule-plugins' docker driver, `kea_dhcp_msdns/windows` on the default driver with its own 290-line create/destroy | **Yes**: below |
| `ansible-collection-windows` (renamed from `ansible-collection-gpo` 2026-10-31) | `roles/gpo_backup/molecule/default` and `roles/gpo_import/molecule/default` (PR #17): default driver, with a shared create/destroy in `tests/domain_lab/` wired up by `.config/molecule/config.yml` | **Yes**, after the port of the old ansible-windows modules into it merges (in progress 2026-10-31; that repo's session will say when) |
| `ansible-collection-orchestration` | none (ansible-test only; a strategy plugin) | No |
| `ansible-collection-stork`, `ansible-role-kea-dhcp` | archived, read-only | No |

## What gets smaller

| | Before | After |
|---|---|---|
| pip, per Molecule job | `ansible-core molecule 'molecule-plugins[docker]' docker` (+ `pypsrp` for Windows) | `ansible-core molecule molecule-git-cgallagher` (from the action) |
| collections, per job | repo `requirements.yml` + `community.docker` (+ `ansible.windows`, `microsoft.ad` from a scenario `requirements.yml`) | repo `requirements.yml` + `git_cgallagher.molecule` (brings `community.docker`, `ansible.windows`, `microsoft.ad`) |
| per-scenario files | `molecule.yml` with dependency/driver/provisioner env/verifier/test_sequence; the Windows scenarios add `create.yml` + `destroy.yml` (~290 lines, copied between the windows and isc repos) and a `requirements.yml` | `molecule.yml` = platforms + inventory; no create/destroy anywhere |
| CI setup, per job | 4-15 lines (pip, galaxy, symlink; KVM, QEMU and lab dir for Windows) | one `uses:` step |
| mixed drivers | a job needs every driver its sibling scenarios use (isc 8d63b31: `windows-dc` installs the docker driver for a scenario it never runs) | one driver for all scenarios |

## ansible-collection-isc

One PR. Scenario files are build-ignored, so no Galaxy release is needed, but see step 1.

1. **`galaxy.yml` and the version gate.** Add `.config` to `build_ignore`, and
   `\.config/` to the `ignore=` regex of the `version-bump` job in `ansible-test.yml`. Otherwise the
   new base config counts as shipped content and the gate demands a version bump.
2. **Add `.config/molecule/config.yml`** at the repo root (Molecule finds it from the role directory,
   because it looks at the VCS root):

   ```yaml
   dependency:
     name: galaxy
     options:
       requirements-file: ${MOLECULE_PROJECT_DIRECTORY}/../../requirements.yml
   driver:
     name: molecule-git-cgallagher
   provisioner:
     name: ansible
     env:
       # Expose this checkout as git_cgallagher.isc (see AGENTS.md).
       ANSIBLE_COLLECTIONS_PATH: ${MOLECULE_PROJECT_DIRECTORY}/../../.molecule-collections:${MOLECULE_PROJECT_DIRECTORY}/../../collections:~/.ansible/collections
   verifier:
     name: ansible
   scenario:
     test_sequence: [dependency, destroy, syntax, create, prepare, converge, idempotence, verify, destroy]
   ```

3. **Each docker-driver `molecule.yml`** (`kea_dhcp/{default,ha3,container,dns}`,
   `kea_dhcp_msdns/default`, `stork_server/default`, `stork_agent/container`): delete `dependency:`,
   `driver:`, `provisioner.name`/`provisioner.env`, `verifier:`; delete `pre_build_image: true` and
   `command: sleep infinity` (the defaults). Keep `platforms`, `provisioner.inventory` and any
   `test_sequence` that differs from the base (the scenarios without a prepare). All their platform
   keys are supported (checked against every platform on main, 2026-10-31).

   `kea_dhcp/ha3`, before → after:

   ```diff
   -dependency:
   -  name: galaxy
   -  options:
   -    requirements-file: ${MOLECULE_PROJECT_DIRECTORY}/../../requirements.yml
   -driver:
   -  name: docker
    platforms:
      - name: kea1
        image: public.ecr.aws/amazonlinux/amazonlinux:2023-minimal
        platform: linux/amd64
   -    command: sleep infinity
   -    pre_build_image: true
        docker_networks: &kea_ha3_network
   ...
    provisioner:
   -  name: ansible
   -  env:
   -    # Expose this checkout as git_cgallagher.isc (see AGENTS.md).
   -    ANSIBLE_COLLECTIONS_PATH: ${MOLECULE_PROJECT_DIRECTORY}/../../.molecule-collections:...
      inventory:
        host_vars:
          kea1: {kea_dhcp_ha_address: 172.29.30.11}
   ...
   -verifier:
   -  name: ansible
    scenario:
   -  test_sequence:
   -    - dependency
   -    ...
   +  test_sequence: [dependency, destroy, create, converge, idempotence, verify, destroy]
   ```

4. **`kea_dhcp_msdns/windows`**:
   - delete `create.yml`, `destroy.yml` and `requirements.yml` (the collection brings
     `ansible.windows`, `microsoft.ad` and `community.docker`; `community.general` is in the repo's
     `requirements.yml`);
   - `molecule.yml`: delete `dependency:` and `driver:`; `dc01` gets `kind: windows_vm`, and its
     `dns_port: 15353` / `kdc_port: 18088` become
     `forwards: [{port: 15353, guest_port: 53, protocols: [tcp, udp]}, {port: 18088, guest_port: 88, protocols: [tcp, udp]}]`;
     `kea-win` gets `platform: linux/amd64` and `network_mode: host` (create.yml used to add both);
     add `lab_dns_port: 15353` and `lab_kdc_port: 18088` to `group_vars.all` and drop `lab_dir`;
   - `converge.yml`: read the ports from `lab_dns_port`/`lab_kdc_port` instead of
     `lab_dc.dns_port`/`lab_dc.kdc_port` (and drop `lab_dc`);
   - `prepare.yml`: the promotion play becomes

     ```yaml
     - name: Promote the VM to the first DC of a new forest, with Microsoft DNS
       hosts: dc01
       gather_facts: false
       tasks:
         - name: Promote
           ansible.builtin.include_role:
             name: git_cgallagher.molecule.ad_forest
           vars:
             ad_forest_domain: "{{ lab_domain }}"
             ad_forest_netbios: "{{ lab_netbios }}"
             ad_forest_dsrm_password: "{{ lab_dsrm_password }}"
             ad_forest_hostname: "{{ lab_hostname }}"
             ad_forest_features: [AD-Domain-Services, DNS, RSAT-AD-PowerShell, RSAT-DNS-Server]
             ad_forest_install_dns: true
             ad_forest_ready_modules: [ActiveDirectory, DnsServer]
             ad_forest_ready_test: "$null = Get-DnsServerZone -Name '{{ lab_domain }}' -ErrorAction Stop"
             ad_forest_sync_clock: true
     ```

     followed by the scenario's own "Show the domain" tasks.
5. **Workflows.** In `ansible-test.yml`, each of the 8 Molecule jobs' install + symlink steps
   become:

   ```yaml
   - uses: git-cgallagher/ansible-collection-molecule/.github/actions/setup-molecule@<sha>  # v0.1.0
     with:
       collection: git_cgallagher.isc
       requirements: requirements.yml
       ansible-core: ansible-core>=2.18,<2.22   # the stork jobs' existing pin
   ```

   `windows-dc.yml`: the KVM/QEMU, lab-dir, install and symlink steps become the same action with
   `kvm: 'true'` and `lab-dir: /mnt/kea-lab`; the screen capture becomes
   `git-cgallagher/ansible-collection-molecule/.github/actions/vm-screendump@<sha>` (the file is now
   `dc01-screen.ppm`); drop `molecule-plugins[docker]`, and replace `KEA_LAB_DIR` with the
   `MOLECULE_LAB_DIR` the action exports (the diagnostics `docker cp` and the artifact paths). Job names don't change, so the ruleset's required checks don't either.
6. **Docs.** AGENTS.md: the Checks block (install = the two commands in this repo's README), drop
   the "every driver its siblings use" gotcha, note that platforms are validated against the kind's
   key list. CHANGELOG entry (no version bump). `roles/kea_dhcp_msdns/molecule/windows/README.md`:
   `KEA_LAB_DIR` → `MOLECULE_LAB_DIR`.

Steps 2–4 as a patch against isc `main` 9d20478: [`isc-scenarios.patch`](isc-scenarios.patch)
(14 files, +49 / −585 lines; `git apply --check` passes on that commit).

Tried on a Mac (Docker Desktop, arm64) in a scratch clone with that patch applied:

- `kea_dhcp/ha3`: full `molecule test` passes (3 emulated amd64 nodes on a static-IP network;
  verify checks the HA states and a real failover); nothing left behind.
- `kea_dhcp_msdns/windows`: create validates `dc01`'s keys and expands its four forwards, then stops
  at the `/dev/kvm` check, as on any Mac. The VM boot and `ad_forest` need the `windows-dc` run.
- `kea_dhcp/default --platform-name kea-debian12` (the CI matrix form): full `molecule test`
  passes and only that platform's container is created.
- Every docker platform in the 8 scenarios uses only keys the docker kind supports.
- `yamllint .` and `ansible-lint` (production profile) stay at 0 with the patch applied.

On a Mac only: the privileged systemd platforms (`stork_server/default`, `stork_agent/container`,
`kea_dhcp/container`) clear Docker Desktop's amd64 emulation when they start, which breaks every
emulated scenario run after them. Adding
`/dev/null:/etc/systemd/system/systemd-binfmt.service:ro` to their `volumes` prevents it (see this
repo's AGENTS.md); GitHub's amd64 runners don't emulate, so CI is unaffected either way.

## ansible-collection-windows (formerly ansible-collection-gpo)

State on `main` 00e66cd (2026-10-31, after PR #18 renamed the collection to
`git_cgallagher.windows` 0.2.0; local checkout `~/Code/ansible-collection-windows`): Molecule runs
from the role directory, and
`.config/molecule/config.yml` already holds the shared platform (`dc01`), inventory and
`provisioner.playbooks` pointing at `tests/domain_lab/create.yml` / `destroy.yml`.
`tests/domain_lab/` also has `promote.yml`, `seed.yml`, `files/lab_fixture.ps1` and
`requirements.yml`. The session that owns the repo is now porting the old ansible-windows modules
into it, with tests on the same lab files (`tests/domain_lab/`, the base config, `domain-lab.yml`).
This PR waits until that port merges (that session will say when), and is Chris's call.

One PR:

1. `.config/molecule/config.yml`:
   - `driver: {name: molecule-git-cgallagher}`, replacing `name: default` + `options.managed`;
   - delete `provisioner.playbooks` (the create/destroy paths);
   - `dc01` gets `kind: windows_vm`, and no `forwards` (WinRM only);
   - in `group_vars.all`, read `lab_dir` from `MOLECULE_LAB_DIR` (what setup-molecule exports) instead of
     `GPO_LAB_DIR`, so the box cache and VM disk (which the driver keeps in `$MOLECULE_LAB_DIR`)
     stay next to the GPO export repos.
2. Delete `tests/domain_lab/create.yml` and `destroy.yml`.
3. `tests/domain_lab/promote.yml`: the promotion tasks (connection wait through the reconnect)
   become `git_cgallagher.molecule.ad_forest` with

   ```yaml
   ad_forest_domain: "{{ lab_domain }}"
   ad_forest_netbios: "{{ lab_netbios }}"
   ad_forest_dsrm_password: "{{ lab_dsrm_password }}"
   ad_forest_features: [AD-Domain-Services, GPMC, RSAT-AD-PowerShell]
   ad_forest_ready_modules: [ActiveDirectory, GroupPolicy]
   ad_forest_ready_test: >-
     $netlogon = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters' -ErrorAction Stop;
     if ($netlogon.SysvolReady -ne 1) { exit 1 };
     if (-not (Get-SmbShare -Name SYSVOL -ErrorAction SilentlyContinue)) { exit 1 };
     $null = Get-GPO -All -ErrorAction Stop
   ad_forest_wait_sysvol_path: true
   ad_forest_admin_groups: [Domain Admins, Group Policy Creator Owners]
   ```

   `seed.yml`, the fixture and the scenarios' own prepare/converge/verify stay as they are.
4. `tests/domain_lab/requirements.yml` can stay for local runs; in CI the action installs the
   `git_cgallagher.molecule` collection, which brings `ansible.windows` and `microsoft.ad`.
5. The domain-lab workflow: the KVM/QEMU, lab-dir, install and symlink steps become `setup-molecule`
   with `collection: git_cgallagher.windows`, `kvm: 'true'` and `lab-dir: /mnt/gpo-lab`; the screen
   capture becomes `vm-screendump` (the file is now `dc01-screen.ppm`); the artifact upload keeps
   `gpo-repo*/`; `GPO_LAB_DIR` becomes `MOLECULE_LAB_DIR`. Still not a required check.
6. Docs: AGENTS.md, `tests/domain_lab/README.md`, CHANGELOG.

After that, a role that needs several Windows nodes (the `wsfc` role's cluster, for one) can list
more `windows_vm` platforms on a shared `lab_networks` segment in its own scenario, instead of
settling for a single-VM lab.

## Order

1. Publish this repo (public, full new-public-repo baseline), merge, let `ci` and `windows-lab` go
   green once, tag `v0.1.0`.
2. isc PR (the docker scenarios prove the docker kind on every PR; run `windows-dc` by hand on the
   PR branch to prove `windows_vm` + `ad_forest` against isc).
3. windows PR, after the ansible-windows module port merges. Its domain-lab workflow runs on the PR (path filter).
4. Galaxy and PyPI publishing are optional: consumers pin the action's SHA, which installs the
   driver and the collection from that same commit.
