"""Docker daemon probe."""

from harness.errors import HarnessError
from harness.system.runner import CommandRunner, run

_DOCKER_TIMEOUT_S = 15.0


def docker_daemon_available(runner: CommandRunner = run) -> tuple[bool, str]:
    """Check whether the Docker daemon answers `docker info`.

    Args:
        runner: Command runner, injectable for tests.

    Returns:
        Tuple of availability flag and a short human-readable detail.
    """
    try:
        result = runner(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            timeout_s=_DOCKER_TIMEOUT_S,
        )
    except HarnessError as exc:
        return False, str(exc).splitlines()[0]
    return True, f"server {result.stdout.strip()}"
