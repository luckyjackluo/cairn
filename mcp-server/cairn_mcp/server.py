"""Cairn MCP server — exposes the file-organization toolset over MCP.

This is the flagship, UI-less face of the product: point Claude Code, Codex,
Cursor, or any MCP client at a folder and it gains file-organization tools.
The client supplies the agent loop, the approval prompts (driven by the tool
annotations below), and the chat — so there is nothing of ours to run remotely
and no UI required.

Run:
    cairn-mcp-server --workspace ~/Documents/notes
    # or set CAIRN_WORKSPACE and run `cairn-mcp-server`
"""

from __future__ import annotations

import argparse
import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from cairn_core import (
    FileError,
    FileService,
    Workspace,
    WorkspaceError,
    digest,
    query,
    retrieval,
    tags,
    templates,
)

# --- workspace bootstrap ---------------------------------------------------

_root = os.environ.get("CAIRN_WORKSPACE") or os.getcwd()
WS = Workspace(_root)
FS = FileService(WS)

mcp = FastMCP("cairn")


def _guard(fn, *args, **kwargs):
    """Run a core call, translating core errors into clean MCP tool errors."""
    try:
        return fn(*args, **kwargs)
    except (FileError, WorkspaceError) as exc:
        raise ToolError(str(exc)) from exc


# Annotation presets. readOnly tools run freely (and in parallel) in clients
# like Claude Code; destructive tools trigger a confirmation prompt.
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


# --- read-only tools -------------------------------------------------------

@mcp.tool(annotations=READ)
def list_dir(path: str = "") -> list[dict]:
    """List files and folders directly under a workspace-relative directory."""
    return _guard(FS.list_dir, path)


@mcp.tool(annotations=READ)
def get_file_tree(path: str = "", max_depth: int = 12) -> list[dict]:
    """Return the nested file/folder tree under a directory (default: whole workspace)."""
    return _guard(FS.get_tree, path, max_depth)


@mcp.tool(annotations=READ)
def read_detail(path: str) -> dict:
    """Read a file. For .uni docs returns HTML content, flattened text, and tags;
    for text/.md files returns raw text plus any YAML frontmatter tags & metadata."""
    return _guard(FS.read_detail, path)


@mcp.tool(annotations=READ)
def search_files(query: str, path: str = "", limit: int = 100) -> list[dict]:
    """Find files/folders whose name contains `query` (case-insensitive)."""
    return _guard(FS.search_files, query, path, limit)


@mcp.tool(annotations=READ)
def grep(pattern: str, path: str = "", limit: int = 200) -> list[dict]:
    """Search file contents for `pattern`; returns {path, line, text} matches."""
    return _guard(FS.grep, pattern, path, limit)


@mcp.tool(annotations=READ)
def get_file_tags(path: str) -> list[str]:
    """Return a document's tags — from a .uni's tags array or a text file's YAML frontmatter."""
    return _guard(tags.get_tags, WS, path)


@mcp.tool(annotations=READ)
def get_tag_tree() -> dict:
    """Return {tag: [file paths]} aggregated across the workspace."""
    return _guard(tags.get_tag_tree, WS)


@mcp.tool(annotations=READ)
def find_by_meta(filters: dict, path: str = "") -> list[dict]:
    """Find documents whose metadata matches all `filters` (exact, not fuzzy).

    Reads .uni JSON fields and .md YAML frontmatter alike. Scalar fields match
    case-insensitively; `tags` (or any list field) matches when every requested
    value is present. Example: {"status": "to-read", "project": "amazon"}.
    """
    return _guard(query.find_by_meta, WS, filters, path)


@mcp.tool(annotations=READ)
def digest_workspace(path: str = "", group_by: str = "folder") -> dict:
    """Return a token-efficient map of the workspace: one entry (title, date,
    tags, summary) per document, grouped by `folder`, `tag`, or a metadata field
    like `status`/`project`. Read this before loading individual files."""
    return _guard(digest.build_digest, WS, path, group_by)


@mcp.tool(annotations=READ)
def list_templates() -> list[str]:
    """List available document templates (built-ins + workspace .cairn/templates)."""
    return _guard(templates.available, WS)


@mcp.tool(annotations=READ)
def semantic_retrieve(query: str, k: int = 5) -> list[dict]:
    """Return the documents most relevant to `query`.

    Uses embeddings when an embedding endpoint is configured (env), else
    lexical scoring.
    """
    return _guard(retrieval.semantic_retrieve, WS, query, k)


# --- additive / modifying tools -------------------------------------------

@mcp.tool(annotations=WRITE)
def create_file(
    path: str, name: str, content: str = "", template: str = "", fields: dict | None = None
) -> dict:
    """Create a file under `path`. If `name` ends in .uni, wraps content as a .uni doc.

    Pass `template` (e.g. "paper", "note" — see list_templates) with `fields` to
    stamp out a document with frontmatter pre-filled; `content` is then ignored.
    """
    return _guard(FS.create_file, path, name, content, template or None, fields)


@mcp.tool(annotations=WRITE)
def create_folder(path: str, name: str) -> dict:
    """Create a new folder `name` under `path`."""
    return _guard(FS.create_folder, path, name)


@mcp.tool(annotations=WRITE)
def update_file_tags(path: str, tags_list: list[str]) -> dict:
    """Replace a document's tags — in a .uni's tags array or a text file's YAML frontmatter."""
    return _guard(tags.set_tags, WS, path, tags_list)


@mcp.tool(annotations=WRITE)
def import_document(path: str, dest_dir: str = "") -> dict:
    """Convert a docx/pdf/pptx/csv/xlsx/md/text file into an editable .uni doc.

    The original file is kept. Returns the new .uni item.
    """
    return _guard(FS.import_file, path, dest_dir or None)


@mcp.tool(annotations=WRITE)
def import_folder(path: str = "") -> dict:
    """Import every convertible (non-.uni) file under a folder into .uni docs.

    Returns a summary with counts and per-file results (including skips).
    """
    return _guard(FS.import_tree, path)


@mcp.tool(annotations=WRITE)
def reindex() -> dict:
    """Build/refresh the embedding index (no-op if no embedder is configured)."""
    return _guard(retrieval.reindex, WS)


# --- destructive tools (client will confirm) ------------------------------

@mcp.tool(annotations=DESTRUCTIVE)
def multi_edit(path: str, old_string: str, new_string: str) -> dict:
    """Replace a single, unique occurrence of `old_string` with `new_string` in a file."""
    return _guard(FS.multi_edit, path, old_string, new_string)


@mcp.tool(annotations=DESTRUCTIVE)
def rename_item(path: str, new_name: str) -> dict:
    """Rename a file or folder in place (bare name, not a path)."""
    return _guard(FS.rename_item, path, new_name)


@mcp.tool(annotations=DESTRUCTIVE)
def move_item(path: str, target_dir: str) -> dict:
    """Move a file or folder into `target_dir`."""
    return _guard(FS.move_item, path, target_dir)


@mcp.tool(annotations=DESTRUCTIVE)
def delete_item(path: str) -> dict:
    """Delete a file or folder (recursive for folders)."""
    return _guard(FS.delete_item, path)


# --- resources -------------------------------------------------------------

@mcp.resource("workspace://tree")
def tree_resource() -> str:
    """The current workspace file tree, as JSON."""
    return json.dumps(FS.get_tree(), ensure_ascii=False, indent=2)


@mcp.resource("workspace://tags")
def tags_resource() -> str:
    """The current tag tree ({tag: [paths]}), as JSON."""
    return json.dumps(tags.get_tag_tree(WS), ensure_ascii=False, indent=2)


# --- prompts (surface as slash-commands in clients) ------------------------

@mcp.prompt()
def organize(path: str = "") -> str:
    """Ask the agent to organize a folder using the workspace tools."""
    where = f"the '{path}' folder" if path else "the workspace"
    return (
        f"Organize {where}. First call get_file_tree to understand the current "
        "layout, then propose a grouping and use move_item / create_folder to "
        "carry it out. Confirm destructive moves before applying them."
    )


@mcp.prompt()
def tag_all(path: str = "") -> str:
    """Ask the agent to tag every document under a folder."""
    where = f"'{path}'" if path else "the workspace"
    return (
        f"Read each .uni document under {where} (use get_file_tree then "
        "read_detail) and assign concise, consistent tags with update_file_tags."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Cairn MCP server")
    parser.add_argument(
        "--workspace",
        help="Workspace directory to operate on (default: $CAIRN_WORKSPACE or CWD)",
    )
    args = parser.parse_args()
    if args.workspace:
        global WS, FS
        WS = Workspace(args.workspace)
        FS = FileService(WS)
    mcp.run()


if __name__ == "__main__":
    main()
