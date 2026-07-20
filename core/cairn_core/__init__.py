"""cairn_core — the local-first file-organization engine.

Pure Python, standard library only. Everything the harness can do lives here
as plain functions over a :class:`Workspace` (a directory on disk). The MCP
server, a CLI, or a web backend are each thin adapters over this package.
"""

from __future__ import annotations

from . import bills, convert, digest, embeddings, frontmatter, lifecycle, query, reco, retrieval, tags, tasks, templates, uni
from .files import FileError, FileService
from .workspace import PathEscapeError, Workspace, WorkspaceError

__all__ = [
    "Workspace",
    "WorkspaceError",
    "PathEscapeError",
    "FileService",
    "FileError",
    "tags",
    "retrieval",
    "query",
    "bills",
    "tasks",
    "lifecycle",
    "reco",
    "digest",
    "templates",
    "uni",
    "frontmatter",
    "convert",
    "embeddings",
]

__version__ = "0.1.0"
