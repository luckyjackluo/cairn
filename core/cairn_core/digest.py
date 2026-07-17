"""A generated, token-efficient map of a workspace.

A hand-maintained index (one line per document) is a great way to give an agent
the lay of the land without loading every file — but maintained by hand it rots.
This generates that map on demand: one entry per document (title, date, tags, and
a short summary), grouped by folder, tag, or any metadata field. It's the
scalable replacement for a hand-kept ``_INDEX.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import frontmatter, uni
from .files import _is_text
from .query import doc_meta
from .workspace import Workspace

_SUMMARY_CHARS = 160


def _body_summary(p: Path) -> str:
    """A one-line gist of a document's body (first real line of prose)."""
    try:
        if uni.is_uni(p):
            text = uni.html_to_text(uni.read_uni(p).get("content", ""))
        elif _is_text(p):
            _, text = frontmatter.parse(p.read_text(encoding="utf-8", errors="replace"))
        else:
            return ""
    except (ValueError, OSError):
        return ""
    fallback = ""  # a heading, used only if the doc is nothing but headings
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):  # prefer prose over a heading that echoes the title
            fallback = fallback or line.lstrip("#").strip()
            continue
        return line[:_SUMMARY_CHARS]
    return fallback[:_SUMMARY_CHARS]


def _entry(ws: Workspace, p: Path, meta: dict[str, Any]) -> dict[str, Any]:
    summary = meta.get("summary") or meta.get("description") or _body_summary(p)
    return {
        "path": ws.relpath(p),
        "title": str(meta.get("title") or p.stem),
        "date": meta.get("date") or meta.get("date_saved"),
        "status": meta.get("status"),
        "tags": meta.get("tags", []),
        "summary": str(summary),
    }


def build_digest(
    ws: Workspace, path: str = "", group_by: str = "folder"
) -> dict[str, list[dict[str, Any]]]:
    """Return ``{group: [entry, ...]}`` for every document under ``path``.

    ``group_by`` may be ``"folder"`` (top-level directory), ``"tag"`` (an entry
    repeats under each of its tags), or any metadata field name (e.g. ``status``,
    ``project``, ``category``). Groups and entries are sorted for stable output.
    """
    root = ws.resolve(path)
    groups: dict[str, list[dict[str, Any]]] = {}

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(ws.root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        meta = doc_meta(p)
        if meta is None:
            continue
        entry = _entry(ws, p, meta)

        if group_by == "folder":
            keys: list[str] = [rel_parts[0] if len(rel_parts) > 1 else "."]
        elif group_by == "tag":
            keys = [str(t) for t in meta.get("tags", [])] or ["(untagged)"]
        else:
            val = meta.get(group_by)
            keys = [str(val)] if val not in (None, "") else ["(none)"]

        for key in keys:
            groups.setdefault(key, []).append(entry)

    return {
        g: sorted(entries, key=lambda e: (e.get("date") or "", e["path"]))
        for g, entries in sorted(groups.items())
    }
