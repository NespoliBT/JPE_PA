"""Path sandboxing for filesystem tools.

Every path a tool touches is resolved and must stay inside the configured
workspace root. Symlinks are resolved first, so a link pointing outside the
root is rejected too.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from jpe.config import Settings, get_settings


class SandboxError(ValueError):
    """Raised when a path escapes the workspace or matches the deny list."""


def workspace_root(settings: Settings | None = None) -> Path:
    """Return the resolved workspace root."""
    settings = settings or get_settings()
    return Path(settings.workspace).expanduser().resolve()


def resolve_path(path: str, settings: Settings | None = None) -> Path:
    """Resolve ``path`` inside the workspace, or raise :class:`SandboxError`.

    Relative paths are interpreted from the workspace root. Absolute paths are
    accepted only when they resolve inside the root.
    """
    settings = settings or get_settings()
    root = workspace_root(settings)
    raw = Path(path).expanduser()
    candidate = (raw if raw.is_absolute() else root / raw).resolve()

    try:
        relative = candidate.relative_to(root)
    except ValueError:
        msg = f"Path {path!r} is outside the workspace ({root})"
        raise SandboxError(msg) from None

    _check_denied(relative, settings)
    return candidate


def _check_denied(relative: Path, settings: Settings) -> None:
    posix = relative.as_posix()
    for pattern in settings.deny_globs:
        if fnmatch.fnmatch(posix, pattern) or fnmatch.fnmatch(relative.name, pattern):
            msg = f"Path {posix!r} matches denied pattern {pattern!r}"
            raise SandboxError(msg)
