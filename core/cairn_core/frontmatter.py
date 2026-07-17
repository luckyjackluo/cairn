"""YAML-frontmatter support for plain-text documents (stdlib only).

Markdown notes commonly carry a leading ``---`` frontmatter block::

    ---
    title: Temporal Graph Networks
    status: to-read
    arxiv: 2006.10637
    tags: [temporal-graph, tgn]
    ---
    body text...

Cairn's native ``.uni`` format keeps tags/metadata in a JSON block; this module
lets the *same* tag and metadata machinery see the frontmatter of ``.md`` (and
any text) files, so notes stay markdown-native — no conversion to ``.uni``.

Only the small YAML subset that shows up in real frontmatter is supported:
scalars, quoted strings, inline lists ``[a, b]``, and block lists (``- item``).
Writes are *surgical* (:func:`set_field` rewrites a single field and leaves the
rest of the file byte-for-byte intact) so hand-authored frontmatter is never
reformatted out from under the user.
"""

from __future__ import annotations

import re
from typing import Any

_FENCE = "---"
# A field line inside the block: ``key: rest`` (rest may be empty).
_FIELD_RE = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$")
# A block-list item line: ``- value`` (any indent).
_ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(.*)$")


def has_frontmatter(text: str) -> bool:
    return _split(text) is not None


def _split(text: str) -> tuple[list[str], str] | None:
    """Return ``(block_lines, body)`` if ``text`` opens with a frontmatter
    fence, else ``None``. ``block_lines`` excludes the fences."""
    if not text.startswith(_FENCE):
        return None
    lines = text.split("\n")
    # First line must be exactly the fence (allow trailing whitespace).
    if lines[0].strip() != _FENCE:
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            body = "\n".join(lines[i + 1 :])
            return lines[1:i], body
    return None  # unterminated → not valid frontmatter


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_unquote(p) for p in inner.split(",") if p.strip()]
    return _unquote(raw)


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split ``text`` into ``(metadata, body)``.

    Files with no frontmatter yield ``({}, text)`` unchanged.
    """
    split = _split(text)
    if split is None:
        return {}, text
    block, body = split

    meta: dict[str, Any] = {}
    last_key: str | None = None
    for line in block:
        item = _ITEM_RE.match(line)
        if item and last_key is not None:
            # Continuation of a block list under ``last_key``.
            cur = meta.get(last_key)
            if not isinstance(cur, list):
                cur = [] if cur in ("", None) else [cur]
                meta[last_key] = cur
            cur.append(_unquote(item.group(1)))
            continue
        field = _FIELD_RE.match(line)
        if not field:
            continue  # blank line or unsupported construct → skip
        key, rest = field.group(1), field.group(2)
        if rest.strip() == "":
            # Possibly a block list header; stays "" if nothing follows.
            meta[key] = ""
        else:
            meta[key] = _parse_scalar(rest)
        last_key = key
    # Normalize any dangling "" that never got block items into empty string.
    return meta, body


def get_tags(text: str) -> list[str]:
    """Tags declared in frontmatter, as a list of strings (empty if none)."""
    meta, _ = parse(text)
    val = meta.get("tags", [])
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)] if val not in ("", None) else []


def _render_list(values: list[str]) -> str:
    return "[" + ", ".join(str(v) for v in values) + "]"


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return _render_list(value)
    s = str(value)
    # Quote only when a bare scalar would be ambiguous.
    if s == "" or s[0] in ("[", "{", '"', "'", "#", "-", " ") or ": " in s or s.endswith(":"):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def set_field(text: str, key: str, value: Any) -> str:
    """Return ``text`` with frontmatter field ``key`` set to ``value``.

    Surgical: only the target field is rewritten. If the file has no
    frontmatter, a minimal block is prepended. Block-list forms of the field
    (``key:`` followed by ``- item`` lines) are collapsed to an inline list.
    """
    rendered = f"{key}: {_render_value(value)}"
    split = _split(text)
    if split is None:
        return f"{_FENCE}\n{rendered}\n{_FENCE}\n\n{text}"

    block, body = split
    out: list[str] = []
    replaced = False
    i = 0
    while i < len(block):
        line = block[i]
        field = _FIELD_RE.match(line)
        if field and field.group(1) == key:
            out.append(rendered)
            replaced = True
            i += 1
            # Swallow any block-list continuation lines belonging to this key.
            if field.group(2).strip() == "":
                while i < len(block) and _ITEM_RE.match(block[i]):
                    i += 1
            continue
        out.append(line)
        i += 1
    if not replaced:
        out.append(rendered)

    return f"{_FENCE}\n" + "\n".join(out) + f"\n{_FENCE}\n{body}"
