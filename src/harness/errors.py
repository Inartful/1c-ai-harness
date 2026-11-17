"""Exception hierarchy for the harness."""


class HarnessError(Exception):
    """Base class for all expected harness errors."""


class ConfigError(HarnessError):
    """Raised when a configuration cannot be built or validated."""


class PortAllocationError(HarnessError):
    """Raised when no free IPC port is available in the allowed range."""


class ToolNotFoundError(HarnessError):
    """Raised when a required external executable is not on PATH."""


class CommandFailedError(HarnessError):
    """Raised when an external command exits with a non-zero code."""


class CommandTimeoutError(HarnessError):
    """Raised when an external command exceeds its timeout."""


class LinkError(HarnessError):
    """Raised when a symlink or junction cannot be created safely."""
