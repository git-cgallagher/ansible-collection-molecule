"""The testlab Molecule driver.

Create and destroy come from the git_cgallagher.testlab collection: the
playbooks bundled here only import git_cgallagher.testlab.create and
.destroy, which start each platform by its ``kind`` (docker or windows_vm)
and write the instance config. Connection details are read back from that
instance config, as with the delegated driver this subclasses.
"""

from __future__ import annotations

from pathlib import Path

from molecule.driver.delegated import Delegated


class Testlab(Delegated):
    """Docker containers and QEMU/KVM Windows VMs from molecule.yml platforms."""

    title = "Docker containers and QEMU/KVM Windows VMs (git_cgallagher.testlab)"

    def __init__(self, config=None) -> None:  # noqa: ANN001
        super().__init__(config)
        self._name = "molecule-testlab"

    @property
    def managed(self) -> bool:
        # Always managed: create/destroy are this driver's own playbooks.
        return True

    @property
    def login_cmd_template(self) -> str:
        return "docker exec -e COLUMNS={columns} -e LINES={lines} -e TERM=xterm -ti {instance} sh"

    def login_options(self, instance_name: str) -> dict[str, str]:
        return {"instance": instance_name}

    def schema_file(self) -> str:
        return str(Path(__file__).parent / "schema" / "driver.json")
