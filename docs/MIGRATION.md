# Moving the git_cgallagher repos onto molecule-git-cgallagher

Status 2026-10-31: **isc is on the driver** ([isc PR #18](https://github.com/git-cgallagher/ansible-collection-isc/pull/18),
released as 2.0.1). **windows is next**, once that repo's open PRs land: wsfc
([PR 20 there](https://github.com/git-cgallagher/ansible-collection-windows/pull/20)) and the IIS modules
([PR 22 there](https://github.com/git-cgallagher/ansible-collection-windows/pull/22)) change the same lab files.
Tracking: [issue #3](https://github.com/git-cgallagher/ansible-collection-molecule/issues/3).

## Which repos

| Repo | Molecule before | Status |
|---|---|---|
| `ansible-collection-isc` | 8 scenarios in `roles/<role>/molecule/`: 7 on molecule-plugins' docker driver, `kea_dhcp_msdns/windows` on the default driver with its own 290-line create/destroy | **Done**: isc PR #18, 2.0.1, merged 2026-10-31 |
| `ansible-collection-windows` (renamed from `ansible-collection-gpo` 2026-10-31) | one scenario per role (`roles/<role>/molecule/default`), plus `tests/integration/molecule/iis` with PR 22: default driver, shared create/destroy in `tests/domain_lab/` wired up by `.config/molecule/config.yml` | **Next**, after its PRs 20 and 22 merge (that repo's session will say when) |
| `ansible-collection-orchestration` | since 2026-10-31 one scenario, `extensions/molecule/default`: four `python:3.13-slim` containers on molecule-plugins' docker driver | Candidate, not planned: it fits the docker kind and `platform_defaults` (below) |
| `ansible-collection-stork`, `ansible-role-kea-dhcp` | deleted 2026-10-31 | No |

## What gets smaller

| | Before | After |
|---|---|---|
| pip, per Molecule job | `ansible-core molecule 'molecule-plugins[docker]' docker` (+ `pypsrp` for Windows) | `ansible-core molecule molecule-git-cgallagher` (from the action) |
| collections, per job | repo `requirements.yml` + `community.docker` (+ `ansible.windows`, `microsoft.ad` from a scenario `requirements.yml`) | repo `requirements.yml` + `git_cgallagher.molecule` (brings `community.docker`, `ansible.windows`, `microsoft.ad`) |
| per-scenario files | `molecule.yml` with dependency/driver/provisioner env/verifier/test_sequence; the Windows scenarios add `create.yml` + `destroy.yml` (~290 lines, copied between the windows and isc repos) and a `requirements.yml` | `molecule.yml` = platforms + inventory; no create/destroy anywhere |
| CI setup, per job | 4-15 lines (pip, galaxy, symlink; KVM, QEMU and lab dir for Windows) | one `uses:` step |
| mixed drivers | a job needs every driver its sibling scenarios use (isc 8d63b31: `windows-dc` installed the docker driver for a scenario it never runs) | one driver for all scenarios |

## ansible-collection-isc (done)

isc PR #18, "no role behaviour changes", merged as `39d33e1`. Where it differs from the first plan:

- **It's a release after all (2.0.1).** Adding `.config` to `build_ignore` changes `galaxy.yml`,
  which isc's version gate counts as shipped content, so it bumps the version as 1.6.2 did for
  `roles/*/molecule`. The gate's ignore list gained `\.config/` too.
- **CI kept its pins.** isc pins every CI tool in `.github/ci/requirements.txt` (since that morning's
  isc PR #16). So setup-molecule gained the `constraints` input (v0.2.0), and the file dropped
  `molecule-plugins` and `docker`: nothing on the controller uses the Docker SDK.
- **The lint job didn't need the action.** ansible-lint doesn't resolve the Windows scenario's
  dynamic `include_role` of `ad_forest`, and its other modules come from `requirements.yml`. This was
  checked with an isolated `HOME`, because ansible-lint always adds `~/.ansible/collections`. The
  job only lost the deleted scenario `requirements.yml`.

The rest went as planned:

- `.config/molecule/config.yml` holds the driver, the galaxy dependency, the collections path that
  exposes the checkout as `git_cgallagher.isc`, the verifier and the test sequence.
- Each `molecule.yml` keeps its platforms, inventory and any different `test_sequence`.
- `kea_dhcp_msdns/windows` lost `create.yml`, `destroy.yml` and `requirements.yml`. `dc01` is a
  `windows_vm` platform with its DNS/KDC `forwards`, and prepare promotes it with `ad_forest`.

Verified on the PR: all 22 required checks passed, and `windows-dc` passed when run by hand on the
branch. That run booted the VM through `windows_vm`, promoted it with `ad_forest`, and read Kea's
GSS-TSIG updates back from Microsoft DNS.

## ansible-collection-windows (next)

Expected state after its PRs 20 and 22 (both still open on 2026-10-31):

- The base config `.config/molecule/config.yml` holds `platforms: [{name: dc01}]`, the box pin and VM
  size as a `lab_vm` group var (PR 22), and `provisioner.playbooks` pointing at
  `tests/domain_lab/create.yml` and `destroy.yml`. Those playbooks merge `lab_vm` under each
  scenario's platform entry.
- Scenarios:
  - `roles/gpo_backup` and `roles/gpo_import`: the domain labs.
  - `roles/wsfc` (PR 20): a one-node workgroup cluster on `dc01`, never promoted.
  - `tests/integration/molecule/iis` (PR 22): its own VM, `iis01`, never a DC.
- `tests/domain_lab/` also has `promote.yml`, `seed.yml`, `files/lab_fixture.ps1` and
  `requirements.yml`.
- The domain-lab workflow's matrix has `name`, `workdir`, `scenario` and `vm` per entry.

One PR (re-check all of this against `main` first):

1. `.config/molecule/config.yml`:
   - `driver: {name: molecule-git-cgallagher}` replaces `name: default` + `options.managed`.
   - `kind: windows_vm` and PR 22's `lab_vm` contents (box pin, size, `winrm_port`) move into
     `driver.options.platform_defaults`, and the `lab_vm` group var goes.
     `platforms: [{name: dc01}]` stays, so iis's `- name: iis01` still inherits the box.
   - Delete `provisioner.playbooks` (the create/destroy paths).
   - In `group_vars.all`, read `lab_dir` from `MOLECULE_LAB_DIR` (what setup-molecule exports)
     instead of `GPO_LAB_DIR`. The driver keeps the box cache and VM disk in `$MOLECULE_LAB_DIR`, so
     they stay next to the GPO export repos.
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

   `seed.yml`, the fixture and the scenarios' own prepare/converge/verify stay as they are. wsfc
   and iis never promote.
4. `tests/domain_lab/requirements.yml` can stay for local runs. In CI the action installs
   `git_cgallagher.molecule`, which brings `ansible.windows` and `microsoft.ad`.
5. The domain-lab workflow:
   - The KVM/QEMU, lab-dir, install and symlink steps become one setup-molecule step with
     `collection: git_cgallagher.windows`, `kvm: 'true'` and `lab-dir: /mnt/gpo-lab`.
   - The screen capture becomes `vm-screendump` with `platforms: ${{ matrix.vm }}` (the file is now
     `<vm>-screen.ppm`).
   - The artifact upload keeps `gpo-repo*/`, and `GPO_LAB_DIR` becomes `MOLECULE_LAB_DIR`.
   - It stays a non-required check.
6. Docs: AGENTS.md, `tests/domain_lab/README.md` and CHANGELOG. Check the version gate as for isc:
   a `galaxy.yml` change counts as shipped.

After that, wsfc can grow from one node to a real cluster: two or three `windows_vm` platforms on a
shared `lab_networks` segment (see the README's cluster notes).

## ansible-collection-orchestration (candidate)

Its scenario would be `driver.options.platform_defaults: {image: docker.io/library/python:3.13-slim}`
plus four named platforms: no YAML anchor, and no `pre_build_image` or `command`, which are the
defaults. Its Molecule CI job would be one setup-molecule step. Not planned: that's a separate
decision for that repo.

## Order

1. ✅ Publish this repo and tag `v0.1.0` (2026-10-31).
2. ✅ v0.2.0 (`platform_defaults`, `constraints`), then isc PR #18, released as 2.0.1 (2026-10-31).
3. windows, after its PRs 20 and 22. Its domain-lab workflow runs on the PR (path filter).
4. Galaxy and PyPI publishing stay optional: consumers pin the action's SHA, which installs the
   driver and the collection from that same commit.
