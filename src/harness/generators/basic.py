"""Generators for the small configs: autumn, BSL LS, compose, packagedef."""

from pathlib import Path

from harness.models import (
    AutumnProperties,
    BslLanguageServerConfig,
    ComposeFile,
    ComposeService,
    VRunnerSettings,
)
from harness.models.workspace import DEFAULT_ATLAS_IMAGE


def build_autumn(use_ibcmd: bool = True) -> AutumnProperties:
    """Build vanessa-runner 3.0 settings with global defaults.

    Args:
        use_ibcmd: Enable the fast `ibcmd` loader. Disable it when the installed
            platform ships no `ibcmd`.

    Returns:
        Validated settings model.
    """
    return AutumnProperties(vrunner=VRunnerSettings(ibcmd=use_ibcmd))


def build_bsl_ls() -> BslLanguageServerConfig:
    """Build the BSL Language Server configuration.

    Returns:
        Validated config model.
    """
    return BslLanguageServerConfig()


def build_compose(atlas_image: str = DEFAULT_ATLAS_IMAGE) -> ComposeFile:
    """Build the compose stack for bsl-atlas.

    The platform help server is not containerised here: it has no published
    image and runs natively from `python -m mcp_bsl_context`.

    Args:
        atlas_image: Docker image for bsl-atlas.

    Returns:
        Validated compose model; values reference `.env` variables.
    """
    return ComposeFile(
        services={
            "bsl-atlas": ComposeService(
                image=atlas_image,
                ports=["8000:8000"],
                volumes=[
                    "${SOURCE_PATH}:/data/source:ro",
                    "./data/chroma_db:/data/chroma_db",
                ],
                environment={
                    "INDEXING_MODE": "${INDEXING_MODE:-fast}",
                    "INDEXING_PROVIDER": "${INDEXING_PROVIDER:-ollama}",
                    "SEARCH_PROVIDER": "${SEARCH_PROVIDER:-ollama}",
                    "REINDEX_PROVIDER": "${REINDEX_PROVIDER:-ollama}",
                    "OLLAMA_BASE_URL": "${OLLAMA_BASE_URL:-http://host.docker.internal:11434}",
                    "OLLAMA_MODEL": "${OLLAMA_MODEL:-qwen3-embedding:8b}",
                },
            ),
        }
    )


def render_packagedef(project_name: str) -> str:
    """Render the OneScript `packagedef` marker file.

    Args:
        project_name: Workspace directory name used as package name.

    Returns:
        File text.
    """
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "-" for c in project_name)
    return (
        f'Описание.Имя("{safe or "project"}")\n    .Версия("0.1.0")\n    .Автор("1c-ai-harness");\n'
    )


def relative_posix(path: Path, root: Path) -> str:
    """Express a path relative to a root using forward slashes.

    Args:
        path: Absolute or relative path.
        root: Base directory.

    Returns:
        POSIX-style relative path.
    """
    return path.relative_to(root).as_posix()
