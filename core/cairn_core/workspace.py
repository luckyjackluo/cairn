"""Workspace: a local directory that the harness operates on.

A workspace is just a folder on disk. All paths handed to the core are
*relative* to the workspace root, and every access is resolved through
:meth:`Workspace.resolve`, which refuses to escape the root. There is no
database, no object store, and no cloud — the filesystem is the source of
truth.
"""

from __future__ import annotations

import os
from pathlib import Path


class WorkspaceError(Exception):
    """Base class for workspace errors."""


class PathEscapeError(WorkspaceError):
    """Raised when a relative path would resolve outside the workspace root."""


class Workspace:
    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel_path: str | None) -> Path:
        """Resolve a workspace-relative path to an absolute :class:`Path`.

        Raises :class:`PathEscapeError` if the result would land outside the
        workspace root (e.g. via ``..`` or an absolute path).
        """
        rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
        target = (self.root / rel).resolve()
        if target != self.root and not target.is_relative_to(self.root):
            raise PathEscapeError(f"Path escapes workspace: {rel_path!r}")
        return target

    def relpath(self, p: Path) -> str:
        """Return the workspace-relative POSIX path for an absolute path."""
        rel = p.resolve().relative_to(self.root)
        s = rel.as_posix()
        return "" if s == "." else s

    def next_uuid(self) -> int:
        """Monotonic per-workspace integer id, persisted in ``.uuid_counter``.

        Mirrors the original product's id scheme so ``.uni`` files keep a
        stable ordering. Cheap and lock-free for our expected write rate.
        """
        counter_file = self.root / ".uuid_counter"
        current = 0
        if counter_file.exists():
            try:
                current = int(counter_file.read_text(encoding="utf-8").strip() or 0)
            except (ValueError, OSError):
                current = 0
        current += 1
        try:
            counter_file.write_text(str(current), encoding="utf-8")
        except OSError:
            pass
        return current
