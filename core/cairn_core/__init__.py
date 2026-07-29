"""cairn_core — the local-first file-organization engine.

Pure Python, standard library only. Everything the harness can do lives here
as plain functions over a :class:`Workspace` (a directory on disk). The MCP
server, a CLI, or a web backend are each thin adapters over this package.
"""

from __future__ import annotations

from . import bills, convert, digest, embeddings, frontmatter, lifecycle, pulse, query, reco, retrieval, review, tags, tasks, templates, uni, waiting
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
    "pulse",
    "reco",
    "review",
    "digest",
    "templates",
    "uni",
    "frontmatter",
    "convert",
    "embeddings",
    "waiting",
]

__version__ = "0.3.1"
