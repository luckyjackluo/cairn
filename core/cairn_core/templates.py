"""Document templates — frontmatter skeletons for new notes.

``create_file(..., template="paper", fields={...})`` stamps out a document with
its metadata pre-filled, so a captured arXiv link starts life as a well-formed
read-later note instead of a bare URL. Templates are plain text with ``{field}``
placeholders; unknown placeholders resolve to empty strings.

Two built-ins ship (``note`` and ``paper``); a workspace can add or override any
template by dropping ``<workspace>/.cairn/templates/<name>.md`` on disk.
"""

from __future__ import annotations

import datetime
from typing import Any

from .workspace import Workspace

TEMPLATES_SUBDIR = ".cairn/templates"

_BUILTINS: dict[str, str] = {
    "note": (
        "---\n"
        "title: {title}\n"
        "date: {date}\n"
        "category: {category}\n"
        "project: {project}\n"
        "tags: [{tags}]\n"
        "status: {status}\n"
        "---\n\n"
        "{body}\n"
    ),
    "paper": (
        "---\n"
        "title: {title}\n"
        "arxiv: {arxiv}\n"
        "url: {url}\n"
        "status: {status}\n"
        "date_saved: {date}\n"
        "date_read:\n"
        "category: paper\n"
        "project: {project}\n"
        "tags: [{tags}]\n"
        "authors: [{authors}]\n"
        "---\n\n"
        "## Abstract\n{abstract}\n\n"
        "## Why I saved it\n{why}\n\n"
        "## My Notes\n{notes}\n"
    ),
}

# Per-template default field values (overridden by caller-supplied fields).
_DEFAULTS: dict[str, dict[str, str]] = {
    "note": {"status": "draft"},
    "paper": {"status": "to-read"},
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # unknown placeholder → empty
        return ""


def _today() -> str:
    return datetime.date.today().isoformat()


def _flatten(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def available(ws: Workspace | None = None) -> list[str]:
    """Names of all templates — built-ins plus any workspace overrides."""
    names = set(_BUILTINS)
    if ws is not None:
        d = ws.root / TEMPLATES_SUBDIR
        if d.is_dir():
            names.update(p.stem for p in d.glob("*.md"))
    return sorted(names)


def render(name: str, fields: dict[str, Any] | None = None, ws: Workspace | None = None) -> str:
    """Render template ``name`` with ``fields`` merged over its defaults.

    ``date`` defaults to today when not supplied. A workspace-local
    ``.cairn/templates/<name>.md`` takes precedence over the built-in.
    """
    text: str | None = None
    if ws is not None:
        override = ws.root / TEMPLATES_SUBDIR / f"{name}.md"
        if override.is_file():
            text = override.read_text(encoding="utf-8")
    if text is None:
        text = _BUILTINS.get(name)
    if text is None:
        raise ValueError(f"Unknown template: {name!r}. Available: {', '.join(available(ws))}")

    merged: dict[str, Any] = {"date": _today()}
    merged.update(_DEFAULTS.get(name, {}))
    merged.update(fields or {})
    return text.format_map(_SafeDict({k: _flatten(v) for k, v in merged.items()}))
