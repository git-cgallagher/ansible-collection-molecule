# Moving the git_cgallagher repos onto molecule-git-cgallagher

Status 2026-11-01: **done.** isc, orchestration, windows and aws all run their Molecule scenarios
on the driver, and none of them has its own create/destroy any more. Tracking:
[issue #3](https://github.com/git-cgallagher/ansible-collection-molecule/issues/3).

## Which repos

| Repo | Molecule before | Status |
|---|---|---|
| `ansible-collection-isc` | 8 scenarios in `roles/<role>/molecule/`: 7 on molecule-plugins' docker driver, `kea_dhcp_msdns/windows` on the default driver with its own 290-line create/destroy | **Done**: [isc PR #18](https://github.com/git-cgallagher/ansible-collection-isc/pull/18), 2.0.1, merged 2026-10-31 |
| `ansible-collection-orchestration` | one scenario, `extensions/molecule/default`: four `python:3.13-slim` containers on molecule-plugins' docker driver | **Done**: [orchestration PR #15](https://github.com/git-cgallagher/ansible-collection-orchestration/pull/15), merged 2026-11-01 (test-only, no release) |
| `ansible-collection-windows` (renamed from `ansible-collection-gpo` 2026-10-31) | one scenario per role (`roles/<role>/molecule/default`) plus `tests/integration/molecule/iis`: default driver, shared create/destroy in `tests/domain_lab/` wired up by `.config/molecule/config.yml` | **Done**: [windows PR #23](https://github.com/git-cgallagher/ansible-collection-windows/pull/23), merged 2026-11-01 (test-only, no release) |
| `ansible-collection-aws` | none: its `module_ssm_parameter` integration target runs the module on the controller, through the local connection | **Done**: [aws PR #4](https://github.com/git-cgallagher/ansible-collection-aws/pull/4), its first scenario, merged 2026-11-01 (test-only, no release) |
| `ansible-collection-stork`, `ansible-role-kea-dhcp` | deleted 2026-10-31 | No |

## What got smaller

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

## ansible-collection-orchestration (done)

Chris asked for it on 2026-11-01. orchestration PR #15, merged as `6334663`:

- Its scenario is `driver.options.platform_defaults: {image: docker.io/library/python:3.13-slim}`
  plus four named platforms: no YAML anchor, and no `pre_build_image` or `command`, which are the
  defaults.
- Its Molecule CI job is one setup-molecule step, with `extensions/molecule/requirements.txt` as
  `constraints` (it dropped `molecule-plugins` and `docker`). The job keeps its name, `molecule`, a
  required check there.
- Test-only: the version stayed 0.2.2, so its Galaxy publishing had nothing to do.

Verified on the PR: all 16 checks, devel included. Three local runs spaced task starts by 1.948 to
2.069 s, inside the owner's 1.90 to 2.08 s window, and a deliberately broken run (its control task
spaced too) failed verify, as it should.

## ansible-collection-windows (done)

windows PR #23, merged as `7584116`. Where it differs from the plan:

- **It landed after the IIS modules PR** (PR 22 there, 0.4.0), merged in rather than rebased because
  the PR was already up. That PR had moved the box pin into a `lab_vm` group var, which
  `tests/domain_lab/create.yml` merged under each platform; `lab_vm` became
  `driver.options.platform_defaults`, so the IIS scenario's `iis01` inherits the box like `dc01`.
- **No release.** `.config` and `tests` were already in `build_ignore`, and that repo's version gate
  counts `README.md` and `CHANGELOG.md` as shipped, so the change has no CHANGELOG entry, like its
  CI-only PR #21.
- **The KVM/QEMU step's 10-minute guard** (a stuck apt hung one run for two hours) became a 15-minute
  `timeout-minutes` on the setup-molecule step. A composite action's own steps can't set one: the
  runner's `action_yaml.json` has no such key.
- **The lint job didn't change**, for the reason given for isc.

The rest went as planned:

- `.config/molecule/config.yml`: `driver: molecule-git-cgallagher` with `kind: windows_vm` and the
  box in `platform_defaults`, `platforms: [{name: dc01}]`, no `provisioner.playbooks`, and `lab_dir`
  from `MOLECULE_LAB_DIR`, so the GPO exports sit next to the VM disk.
- `tests/domain_lab/create.yml` and `destroy.yml` are gone. `promote.yml` promotes with `ad_forest`
  (GPMC and the GroupPolicy module, a SYSVOL-ready test, `ad_forest_wait_sysvol_path`, Domain Admins
  plus Group Policy Creator Owners). wsfc and iis never promote.
- `domain-lab.yml`: one setup-molecule step (`kvm: 'true'`, `lab-dir: /mnt/gpo-lab`), the screen
  capture is `vm-screendump` with `platforms: ${{ matrix.vm }}`, and it is still not a required
  check.

Verified on the PR: every check passed, including all four `domain-lab` jobs (windows run
[41320266419](https://github.com/git-cgallagher/ansible-collection-windows/actions/runs/41320266419)): gpo_backup and gpo_import against a DC that `ad_forest` promoted, wsfc's one-node
workgroup cluster, and the IIS modules' targets on a plain server.

Next there: wsfc can grow from one node to a real cluster, two or three `windows_vm` platforms on a
shared `lab_networks` segment (windows issue #19). Its shared disks need SCSI-3 persistent
reservations, which this driver doesn't offer yet (AGENTS.md TODO).

## ansible-collection-aws (done)

Chris asked for it on 2026-11-01. aws had no Molecule: its `module_ssm_parameter` integration
target runs the module on the controller, through the local connection. aws PR #4, merged as
`465f104`, adds `tests/integration/molecule/ssm_parameter`:

- **The module on a real remote host.** `node1` (`python:3.13-slim`, over the docker connection)
  runs it against `moto` (`motoserver/moto:5.2.5`, standing in for SSM and STS), through the same
  cross-account role chain as the integration target. boto3 is installed on `node1` only and the
  controller has none, so the module, module_utils included, can only have run there.
- **Two platforms on one network, from `platform_defaults`:** `docker_networks` creates
  `aws-molecule` and `networks` joins both containers to it, so `node1` reaches `moto` by name.
- **`override_command: false` for moto.** The image's entrypoint is the server, and the kind's
  default command (`sleep infinity`) would reach it as arguments.
- **CI runs it in the ansible-runtime image, not through setup-molecule.** The job calls
  `.github/ci/runtime.sh` (adapted from isc's) with the `0.2.1-ci` image pinned by digest, which
  already has the driver and `git_cgallagher.molecule`, so the job installs nothing.
- **No base config, so no release.** aws's version gate counts `.config/` as shipped content, so the
  scenario keeps its driver settings in its own `molecule.yml`, and the wrapper links the checkout in
  as `git_cgallagher.aws` under `collections/`, which is gitignored and build-ignored. The version
  stayed 0.3.0.

Verified: a full local `molecule test` through the same wrapper and image (create, prepare,
converge, idempotence, verify, destroy, nothing left running), all 19 checks on the PR, and every
stable check on main, including the new `molecule ssm_parameter` (aws run
[41351804358](https://github.com/git-cgallagher/ansible-collection-aws/actions/runs/41351804358)).
It isn't a required check there yet (aws AGENTS.md TODO).

## Order

1. ✅ Publish this repo and tag `v0.1.0` (2026-10-31).
2. ✅ v0.2.0 (`platform_defaults`, `constraints`), then isc PR #18, released as 2.0.1 (2026-10-31).
3. ✅ orchestration PR #15 (2026-11-01).
4. ✅ windows PR #23, after its PRs 20 (wsfc) and 22 (IIS modules) (2026-11-01).
5. ✅ aws PR #4, its first scenario (2026-11-01).
6. Galaxy and PyPI publishing stay optional: consumers pin the action's SHA, which installs the
   driver and the collection from that same commit.
