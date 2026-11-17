"""Schema for vanessa-runner 3.0 `autumn-properties.json`."""

from pydantic import Field

from harness.models.base import StrictModel
from harness.models.workspace import DEFAULT_IBCONNECTION


class VRunnerSettings(StrictModel):
    """Global vrunner defaults.

    vrunner 3.0 resolves every option by walking up the command path:
    `vrunner.cf.load.ibcmd` -> `vrunner.cf.ibcmd` -> `vrunner.ibcmd`. Global keys
    therefore apply to every command the protocol runs; a flat 2.6 file
    (`{"ibconnection": ...}`) is silently ignored.
    """

    ibconnection: str = DEFAULT_IBCONNECTION
    src: str = "./src/cf"
    ibcmd: bool = True


class AutumnProperties(StrictModel):
    """vanessa-runner 3.0 settings file."""

    vrunner: VRunnerSettings = Field(default_factory=VRunnerSettings)
