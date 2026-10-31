# Moving the git_cgallagher repos onto molecule-testlab

Status 2026-10-31: proposal. Nothing in the consumer repos has changed yet.

## Which repos

| Repo | Molecule today | Change |
|---|---|---|
| `ansible-collection-isc` | 8 scenarios in `roles/<role>/molecule/` (moved there in PR #13, merged 2026-10-31): 7 on molecule-plugins' docker driver, `kea_dhcp_msdns/windows` on the default driver with its own 290-line create/destroy | **Yes**: below |
| `ansible-collection-gpo` | `molecule/domain_lab` (default driver, its own create/destroy). A move to `roles/gpo_import/molecule/default`, with create/destroy in `tests/domain_lab/`, was in progress on `feat/role-molecule` when this was written | **Yes**, after that move lands |
| `ansible-collection-orchestration` | none (ansible-test only; a strategy plugin) | No |
| `ansible-collection-stork`, `ansible-role-kea-dhcp` | archived, read-only | No |

## What gets smaller

| | Before | After |
|---|---|---|
| pip, per Molecule job | `ansible-core molecule 'molecule-plugins[docker]' docker` (+ `pypsrp` for Windows) | `ansible-core molecule molecule-testlab` (from the action) |
| collections, per job | repo `requirements.yml` + `community.docker` (+ `ansible.windows`, `microsoft.ad` from a scenario `requirements.yml`) | repo `requirements.yml` + `git_cgallagher.testlab` (brings `community.docker`, `ansible.windows`, `microsoft.ad`) |
| per-scenario files | `molecule.yml` with dependency/driver/provisioner env/verifier/test_sequence; the Windows scenarios add `create.yml` + `destroy.yml` (~290 lines, copied between gpo and isc) and a `requirements.yml` | `molecule.yml` = platforms + inventory; no create/destroy anywhere |
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
     name: molecule-testlab
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
             name: git_cgallagher.testlab.ad_forest
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
   - uses: git-cgallagher/ansible-collection-testlab/.github/actions/setup-molecule@<sha>  # v0.1.0
     with:
       collection: git_cgallagher.isc
       requirements: requirements.yml
       ansible-core: ansible-core>=2.18,<2.22   # the stork jobs' existing pin
   ```

   `windows-dc.yml`: the KVM/QEMU, lab-dir, install and symlink steps become the same action with
   `kvm: 'true'` and `lab-dir: /mnt/kea-lab`; the screen capture becomes
   `git-cgallagher/ansible-collection-testlab/.github/actions/vm-screendump@<sha>` (the file is now
   `dc01-screen.ppm`); drop `molecule-plugins[docker]`, and replace `KEA_LAB_DIR` with the
   `TESTLAB_DIR` the action exports (the diagnostics `docker cp` and the artifact paths). Job names don't change, so the ruleset's required checks don't either.
6. **Docs.** AGENTS.md: the Checks block (install = the two commands in this repo's README), drop
   the "every driver its siblings use" gotcha, note that platforms are validated against the kind's
   key list. CHANGELOG entry (no version bump). `roles/kea_dhcp_msdns/molecule/windows/README.md`:
   `KEA_LAB_DIR` → `TESTLAB_DIR`.

Steps 2–4 as a patch against isc `main` 9d20478: [`isc-scenarios.patch`](isc-scenarios.patch)
(14 files, +49 / −585 lines; `git apply --check` passes on that commit).

Tried on a Mac (Docker Desktop, arm64) in a scratch clone with that patch applied:

- `kea_dhcp/ha3`: full `molecule test` passes (3 emulated amd64 nodes on a static-IP network;
  verify checks the HA states and a real failover); nothing left behind.
- `kea_dhcp_msdns/windows`: create validates `dc01`'s keys and expands its four forwards, then stops
  at the `/dev/kvm` check, as on any Mac. The VM boot and `ad_forest` need the `windows-dc` run.
- Every docker platform in the 8 scenarios uses only keys the docker kind supports.

## ansible-collection-gpo

After the per-role move on `feat/role-molecule` merges (don't stack on an unmerged branch; check
with the session doing it). Then one PR:

1. Add `.config/molecule/config.yml` as for isc (gpo has no repo `requirements.yml` today: either
   point `requirements-file` at one, or drop `dependency:`; the testlab collection brings
   `ansible.windows` and `microsoft.ad`). Add `.config` to `build_ignore`.
2. Delete the shared `create.yml`/`destroy.yml` (`tests/domain_lab/` on that branch) and the
   scenario `requirements.yml`.
3. `molecule.yml`: delete `dependency:`/`driver:`/`provisioner.name`/`env`/`verifier:`; `dc01` gets
   `kind: windows_vm` (no `forwards`: WinRM only). Keep `lab_dir`, which the scenario uses for its GPO
   export repos: `lab_dir: "{{ lookup('ansible.builtin.env', 'TESTLAB_DIR') | default(lookup('ansible.builtin.env', 'HOME') ~ '/.cache/gpo-domain-lab', true) }}"`.
4. `prepare.yml`: the promotion play becomes `ad_forest` with

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

   then the "Show the domain" tasks and the seed/export play, unchanged.
5. `domain-lab.yml`: KVM/QEMU, lab-dir, install and symlink steps → `setup-molecule` with
   `collection: git_cgallagher.gpo`, `kvm: 'true'`, `lab-dir: /mnt/gpo-lab`; screen capture →
   `vm-screendump` (`dc01-screen.ppm`); the artifact upload keeps `gpo-repo*/`. `GPO_LAB_DIR` →
   `TESTLAB_DIR`. Still not a required check.
6. Docs: AGENTS.md, README "Running the lab", CHANGELOG.

## Order

1. Publish this repo (public, full new-public-repo baseline), merge, let `ci` and `windows-lab` go
   green once, tag `v0.1.0`.
2. isc PR (the docker scenarios prove the docker kind on every PR; run `windows-dc` by hand on the
   PR branch to prove `windows_vm` + `ad_forest` against isc).
3. gpo PR, after its per-role move. `domain-lab` runs on the PR (path filter).
4. Galaxy and PyPI publishing are optional: consumers pin the action's SHA, which installs the
   driver and the collection from that same commit.
