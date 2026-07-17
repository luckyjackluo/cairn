"""Tagging — reads/writes tags stored *inside* documents.

Two document shapes carry tags, and this module treats them uniformly:

* ``.uni`` files — tags live in the JSON ``tags`` array (see :mod:`cairn_core.uni`).
* plain-text files (``.md`` &c.) — tags live in the YAML ``tags:`` frontmatter
  field (see :mod:`cairn_core.frontmatter`).

Either way the "tag database" is just the workspace: tags travel with the file,
and the tag *tree* is derived on demand by scanning, with no sync step.
"""

from __future__ import annotations

from typing import Any

from . import frontmatter, uni
from .files import FileError, _is_text
from .workspace import Workspace


def _normalize(tags: Any) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        s = str(t).strip() if not isinstance(t, dict) else str(t.get("name", "")).strip()
        if s and s not in out:
            out.append(s)
    return out


def get_tags(ws: Workspace, path: str) -> list[str]:
    p = ws.resolve(path)
    if not p.is_file():
        raise FileError(f"Not a file: {path!r}")
    if uni.is_uni(p):
        return [str(t) for t in uni.read_uni(p).get("tags", [])]
    if _is_text(p):
        return frontmatter.get_tags(p.read_text(encoding="utf-8", errors="replace"))
    raise FileError(f"Cannot read tags from a binary file: {path!r}")


def set_tags(ws: Workspace, path: str, tags: list[str]) -> dict[str, Any]:
    p = ws.resolve(path)
    if not p.is_file():
        raise FileError(f"Not a file: {path!r}")
    normalized = _normalize(tags)
    if uni.is_uni(p):
        obj = uni.read_uni(p)
        obj["tags"] = normalized
        uni.write_uni(p, obj)
    elif _is_text(p):
        text = p.read_text(encoding="utf-8", errors="replace")
        p.write_text(frontmatter.set_field(text, "tags", normalized), encoding="utf-8")
    else:
        raise FileError(f"Cannot set tags on a binary file: {path!r}")
    return {"path": ws.relpath(p), "tags": normalized}


def _tags_of(p) -> list[str]:
    """Tags of a single file (``.uni`` JSON or text frontmatter); [] otherwise."""
    try:
        if uni.is_uni(p):
            return [str(t) for t in uni.read_uni(p).get("tags", [])]
        if _is_text(p):
            return frontmatter.get_tags(p.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return []
    return []


def get_tag_tree(ws: Workspace) -> dict[str, list[str]]:
    """Return ``{tag: [file paths]}`` aggregated across the workspace.

    Covers both ``.uni`` documents and frontmatter-tagged text files.
    """
    tree: dict[str, list[str]] = {}
    for p in ws.root.rglob("*"):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        rel = ws.relpath(p)
        for tag in _tags_of(p):
            tree.setdefault(str(tag), []).append(rel)
    return dict(sorted(tree.items()))
