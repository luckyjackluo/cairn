"""Tagging — reads/writes the ``tags`` array inside ``.uni`` documents.

Tags are stored in the files themselves (see :mod:`cairn_core.uni`), so the
"tag database" is just the workspace. The tag *tree* is derived on demand by
scanning ``.uni`` files, which keeps everything consistent with no sync step.
"""

from __future__ import annotations

from typing import Any

from . import uni
from .files import FileError
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
    if not (p.is_file() and uni.is_uni(p)):
        raise FileError(f"Not a .uni file: {path!r}")
    return uni.read_uni(p).get("tags", [])


def set_tags(ws: Workspace, path: str, tags: list[str]) -> dict[str, Any]:
    p = ws.resolve(path)
    if not (p.is_file() and uni.is_uni(p)):
        raise FileError(f"Not a .uni file: {path!r}")
    obj = uni.read_uni(p)
    obj["tags"] = _normalize(tags)
    uni.write_uni(p, obj)
    return {"path": ws.relpath(p), "tags": obj["tags"]}


def get_tag_tree(ws: Workspace) -> dict[str, list[str]]:
    """Return ``{tag: [file paths]}`` aggregated across the workspace."""
    tree: dict[str, list[str]] = {}
    for p in ws.root.rglob("*.uni"):
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        try:
            obj = uni.read_uni(p)
        except (ValueError, OSError):
            continue
        rel = ws.relpath(p)
        for tag in obj.get("tags", []):
            tree.setdefault(str(tag), []).append(rel)
    return dict(sorted(tree.items()))
