"""Waiting-on layer — the things other people owe you.

You already track who owes you *money* (:mod:`cairn_core.bills`); this tracks who
owes you an *action* — a review from your advisor, feedback from a collaborator, a
reply you're blocked on. Each is a ``category: waiting`` note (one person, one
thing) that flows through the attention lifecycle like any other kind: it ages from
when you started waiting (``since``) and nags every few days so a dropped ball
surfaces before it's forgotten.

This module is the thin person-oriented view over that kind — create one, list the
open set, and roll them up by person (the actions analog of ``bills.who_owes``).
Resolving is an ordinary lifecycle status change: ``received`` (they delivered) or
``dropped`` (you gave up on it), stamped like any other transition.
"""

from __future__ import annotations

import datetime
from typing import Any

from . import lifecycle, query, tasks
from .files import FileError, FileService
from .workspace import Workspace

CATEGORY = "waiting"
DEFAULT_DIR = "waiting"
RECEIVED, DROPPED = "received", "dropped"


def _spec() -> lifecycle.KindSpec:
    return lifecycle.KINDS[CATEGORY]


def _iter_meta(ws: Workspace, path: str = ""):
    root = ws.resolve(path)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        meta = query.doc_meta(p)
        if meta is not None:
            yield ws.relpath(p), meta


def _record(ws: Workspace, path: str, meta: dict[str, Any], ref: datetime.date) -> dict[str, Any]:
    spec = _spec()
    status = str(meta.get("status") or "").lower()
    due = tasks._parse_date(meta.get("due"))
    age = lifecycle.item_age_days(meta, spec, ref)
    return {
        "path": path,
        "title": meta.get("title") or path,
        "person": meta.get("person") or None,
        "status": status or None,
        "state": spec.state(status),
        "since": (tasks._parse_date(meta.get("since")).isoformat()
                  if tasks._parse_date(meta.get("since")) else None),
        "age_days": age,
        "due": due.isoformat() if due else None,
        "overdue": bool(due and due < ref and spec.state(status) == "open"),
    }


def list_waiting(
    ws: Workspace,
    person: str | None = None,
    state: str = "open",
    path: str = "",
    today: str | None = None,
) -> list[dict[str, Any]]:
    """Waiting-on items, oldest first (longest wait leads).

    ``state`` is ``open`` (still waiting, the default), ``closed`` (received or
    dropped), or ``all``. ``person`` filters case-insensitively. Each record
    carries ``{path, title, person, status, state, since, age_days, due, overdue}``.
    """
    ref = tasks._parse_date(today) or datetime.date.today()
    want = state.strip().lower()
    who = person.lower() if person else None
    out: list[dict[str, Any]] = []
    for rel, meta in _iter_meta(ws, path):
        if lifecycle.kind_of(meta) is not _spec():
            continue
        rec = _record(ws, rel, meta, ref)
        if want != "all" and rec["state"] != want:
            continue
        if who is not None and str(rec["person"] or "").lower() != who:
            continue
        out.append(rec)
    # Longest wait first; undated age sorts last.
    out.sort(key=lambda r: (r["age_days"] is None, -(r["age_days"] or 0), r["path"]))
    return out


def who_owes_me(ws: Workspace, today: str | None = None, path: str = "") -> list[dict[str, Any]]:
    """Open waiting items rolled up by person — the who-owes-me-actions view.

    Returns ``[{person, count, oldest_age_days, items:[{title, age_days, due,
    overdue, path}]}]``, the person you've waited on longest first. The lean call
    a nudge or a weekly review reads.
    """
    items = list_waiting(ws, state="open", path=path, today=today)
    by_person: dict[str, dict[str, Any]] = {}
    for it in items:
        name = it["person"] or "(unspecified)"
        grp = by_person.setdefault(name, {"person": name, "count": 0, "oldest_age_days": None, "items": []})
        grp["count"] += 1
        grp["items"].append({k: it[k] for k in ("title", "age_days", "due", "overdue", "path")})
        age = it["age_days"]
        if age is not None and (grp["oldest_age_days"] is None or age > grp["oldest_age_days"]):
            grp["oldest_age_days"] = age
    out = list(by_person.values())
    out.sort(key=lambda g: (g["oldest_age_days"] is None, -(g["oldest_age_days"] or 0), g["person"]))
    return out


def add_waiting(
    ws: Workspace,
    what: str,
    person: str,
    due: str | None = None,
    since: str | None = None,
    remind_every: int | None = None,
    notes: str = "",
    dir: str = DEFAULT_DIR,
) -> dict[str, Any]:
    """Start tracking something you're waiting on ``person`` for.

    ``what`` is the thing owed; ``since`` defaults to today (when the wait began);
    ``due`` is an optional "need it by" that makes it overdue past that date;
    ``remind_every`` overrides the kind's nudge cadence for this item.
    """
    what = " ".join((what or "").split())
    person = " ".join((person or "").split())
    if not what:
        raise FileError("Say what you're waiting for.")
    if not person:
        raise FileError("Say who you're waiting on.")

    fs = FileService(ws)
    slug = tasks._slugify(f"{person} {what}")
    name = f"{slug}.md"
    probe = f"{dir}/{name}" if dir else name
    if ws.resolve(probe).exists():
        name = f"{slug}-{ws.next_uuid()}.md"
    fields = {
        "title": what,
        "status": "waiting",
        "person": person,
        "since": since or datetime.date.today().isoformat(),
        "due": due or "",
        "remind_every": str(remind_every) if remind_every else "",
        "tags": ["waiting"],
        "notes": notes,
    }
    item = fs.create_file(dir, name, template="waiting", fields=fields)
    meta = query.doc_meta(ws.resolve(item["path"])) or {}
    return _record(ws, item["path"], meta, datetime.date.today())


def mark_received(ws: Workspace, path: str, when: str | None = None) -> dict[str, Any]:
    """They delivered — close the waiting item, stamping ``received``."""
    got_on = when or datetime.date.today().isoformat()
    lifecycle.stamp_status(ws, path, RECEIVED, extra={"received": got_on}, when=got_on)
    meta = query.doc_meta(ws.resolve(path)) or {}
    return _record(ws, ws.relpath(ws.resolve(path)), meta, datetime.date.today())


def drop_waiting(ws: Workspace, path: str, when: str | None = None) -> dict[str, Any]:
    """Stop waiting — close the item as ``dropped`` (you gave up on it)."""
    lifecycle.stamp_status(ws, path, DROPPED, when=when)
    meta = query.doc_meta(ws.resolve(path)) or {}
    return _record(ws, ws.relpath(ws.resolve(path)), meta, datetime.date.today())
