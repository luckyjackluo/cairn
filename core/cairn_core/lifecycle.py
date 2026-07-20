"""Attention lifecycle over the workspace — the layer that makes reminding possible.

Tasks, bills and papers are not three subsystems; they are three *kinds* of one
thing: a note with an attention lifecycle. Every such note is either **open** —
waiting on the user to act (an unfinished task, an unread paper, an unpaid bill)
— or **closed** — done and archived, retrieved only when asked for by name. That
single open/closed distinction, plus a couple of time triggers, is the whole of
what "task", "bill" and "paper" have in common, and it is all this module owns::

    ---
    category: paper
    status: to-read        # open
    date_saved: 2026-07-01
    ---

Four pieces, deliberately small:

* :data:`KINDS` — a declarative registry. A *kind* is configuration, not a
  module: its open/closed status vocabulary, an optional due-date field, the
  frontmatter dates that anchor its age, and how long it may sit open before it
  is stale. Adding a kind is adding a :class:`KindSpec`; everything below then
  applies to it unchanged.
* :func:`state_of` — derive ``open`` / ``closed`` from a note's ``status`` using
  its kind's vocabulary (``None`` for non-lifecycle notes, ``unknown`` for a
  status the kind doesn't declare).
* :func:`stamp_status` — the one surgical writer that records *when* a status
  last changed (``status_changed``), so staleness is measurable. The existing
  ``tasks``/``bills`` writers are meant to route through this next.
* :func:`attention` — one sweep of the workspace returning everything that needs
  the user right now, bucketed: overdue, due today, upcoming, or gone stale.

Nothing here reaches into a kind's domain logic (bill splitting, paper fetching,
checklist harvest); those stay in their own modules. This layer is only the
cross-cutting lifecycle those modules share.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Any

from . import frontmatter, query, uni
from .files import FileError, _is_text
from .workspace import Workspace


# --- the kind registry -----------------------------------------------------

@dataclass(frozen=True)
class KindSpec:
    """How one kind of note behaves in the attention lifecycle.

    ``open_statuses`` / ``closed_statuses`` are the kind's status vocabulary —
    the union defines every status it recognizes. ``due_field`` names the
    frontmatter date a deadline lives in (``None`` for kinds with no deadline).
    ``anchor_fields`` are the dates used to age an item when it has no
    ``status_changed`` stamp yet, tried in order. ``stale_after_days`` is how
    many days an item may sit open, untouched, before it is surfaced as stale
    (``None`` disables staleness — the kind nags only via its due date).
    ``remind_every_days`` is the default reminder cadence for the kind (how many
    days between digests that re-surface the same item); a note may override it
    with a ``remind_every`` frontmatter field, and urgency (overdue / due today)
    escalates any cadence back to daily.
    """

    category: str
    open_statuses: tuple[str, ...]
    closed_statuses: tuple[str, ...]
    due_field: str | None = None
    anchor_fields: tuple[str, ...] = ()
    stale_after_days: int | None = None
    remind_every_days: int = 1
    template: str = ""
    default_dir: str = ""

    def state(self, status: str | None) -> str:
        """``open`` / ``closed`` / ``unknown`` for a raw status value."""
        s = str(status or "").strip().lower()
        if s in self.open_statuses:
            return "open"
        if s in self.closed_statuses:
            return "closed"
        return "unknown"


# The built-in kinds. Bill lifecycle is note-level (open/settled); the per-person
# unpaid detail stays in :mod:`cairn_core.bills`. Tasks nag by due date only, so
# an undated backlog item never spams the digest (stale_after_days=None).
KINDS: dict[str, KindSpec] = {
    "task": KindSpec(
        category="task",
        open_statuses=("todo", "doing", "blocked"),
        closed_statuses=("done",),
        due_field="due",
        anchor_fields=("created",),
        stale_after_days=None,
        template="task",
        default_dir="tasks",
    ),
    "paper": KindSpec(
        category="paper",
        open_statuses=("to-read",),
        closed_statuses=("read", "skipped"),
        due_field=None,
        anchor_fields=("date_saved", "date"),
        stale_after_days=14,
        template="paper",
        default_dir="research/papers",
    ),
    "bill": KindSpec(
        category="bill",
        open_statuses=("open",),
        closed_statuses=("settled",),
        due_field=None,
        anchor_fields=("date",),
        stale_after_days=30,
        template="bill",
        default_dir="personal/bills",
    ),
}


def register(spec: KindSpec) -> None:
    """Add or replace a kind. The one call a future kind makes to join the sweep."""
    KINDS[spec.category] = spec


def kind_of(meta: dict[str, Any]) -> KindSpec | None:
    """The kind a note belongs to — by ``category``, else a matching tag.

    Returns ``None`` for a note that is not a lifecycle kind at all, so the sweep
    silently ignores ordinary notes.
    """
    cat = str(meta.get("category", "")).strip().lower()
    if cat in KINDS:
        return KINDS[cat]
    tags = {str(t).strip().lower() for t in meta.get("tags", [])}
    for name, spec in KINDS.items():
        if name in tags:
            return spec
    return None


def state_of(meta: dict[str, Any]) -> str | None:
    """``open`` / ``closed`` / ``unknown`` for a note, or ``None`` if not a kind."""
    spec = kind_of(meta)
    if spec is None:
        return None
    return spec.state(meta.get("status"))


# --- dates & aging ---------------------------------------------------------

def _parse_date(value: Any) -> datetime.date | None:
    """Best-effort ISO date parse; ``None`` for empty or malformed values."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def reminder_interval(meta: dict[str, Any], spec: KindSpec) -> int:
    """Days between reminders for an item — its ``remind_every``, else the kind default.

    A per-note ``remind_every: N`` frontmatter field (set when the note is
    created, or edited later) wins; otherwise the kind's ``remind_every_days``
    applies. Anything unparseable or below 1 falls back to the kind default, so a
    typo never silences an item.
    """
    raw = meta.get("remind_every")
    if raw not in (None, ""):
        try:
            n = int(str(raw).strip())
            if n >= 1:
                return n
        except (TypeError, ValueError):
            pass
    return max(1, spec.remind_every_days)


def item_age_days(meta: dict[str, Any], spec: KindSpec, ref: datetime.date) -> int | None:
    """Days since this item was last touched, for staleness.

    Prefers the ``status_changed`` stamp (written by :func:`stamp_status`); until
    a note has one, falls back to the kind's ``anchor_fields`` in order. ``None``
    when no usable date exists — an item we can't age is never called stale.
    """
    anchored = _parse_date(meta.get("status_changed"))
    if anchored is None:
        for f in spec.anchor_fields:
            anchored = _parse_date(meta.get(f))
            if anchored is not None:
                break
    if anchored is None:
        return None
    return (ref - anchored).days


# --- the sweep -------------------------------------------------------------

def _iter_meta(ws: Workspace, path: str):
    """Yield ``(relpath, meta)`` for every metadata-bearing file under ``path``.

    Skips dotfiles/dotfolders (``.cairn`` state, templates, git) exactly as the
    task and bill readers do, so the sweep sees the same document set they do.
    """
    root = ws.resolve(path)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        meta = query.doc_meta(p)
        if meta is not None:
            yield ws.relpath(p), meta


def list_items(
    ws: Workspace,
    kind: str | None = None,
    state: str = "open",
    path: str = "",
) -> list[dict[str, Any]]:
    """The unified read side: lifecycle notes filtered by kind and state.

    ``kind`` restricts to one category (``None`` = every kind). ``state`` is
    ``open`` (the default working set — what's waiting on the user), ``closed``
    (the archive, only when asked), or ``all``. This is the generic form of
    ``list_tasks`` / ``list_bills``; those keep their kind-specific derived
    fields, this gives every kind — including future ones — a read for free.
    """
    want_state = state.strip().lower()
    out: list[dict[str, Any]] = []
    for rel, meta in _iter_meta(ws, path):
        spec = kind_of(meta)
        if spec is None or (kind and spec.category != kind):
            continue
        st = spec.state(meta.get("status"))
        if want_state != "all" and st != want_state:
            continue
        out.append({
            "path": rel,
            "kind": spec.category,
            "title": meta.get("title") or rel,
            "status": str(meta.get("status") or "") or None,
            "state": st,
        })
    out.sort(key=lambda r: (r["kind"], r["path"]))
    return out


def attention(
    ws: Workspace,
    kinds: list[str] | None = None,
    today: str | None = None,
    upcoming_days: int = 3,
    path: str = "",
) -> dict[str, Any]:
    """One sweep for everything that needs the user right now.

    Considers only *open* items. Each is scored on two independent axes — its
    deadline (``overdue`` / ``due_today`` / ``upcoming`` within ``upcoming_days``)
    and its staleness (open longer than its kind's ``stale_after_days`` without a
    status change) — and kept if any fires. Every kept item lands in exactly one
    bucket by priority (overdue > due_today > stale > upcoming) but carries the
    full list of ``reasons`` it was flagged for.

    Returns ``{as_of, count, buckets, items}`` where ``buckets`` maps each bucket
    name to its items (most urgent first within each) and ``items`` is the flat
    list. This is the single primitive a reminder cron reads.
    """
    ref = _parse_date(today) or datetime.date.today()
    horizon = ref + datetime.timedelta(days=max(0, upcoming_days))
    want = {k.strip().lower() for k in kinds} if kinds else None

    items: list[dict[str, Any]] = []
    for rel, meta in _iter_meta(ws, path):
        spec = kind_of(meta)
        if spec is None or (want is not None and spec.category not in want):
            continue
        if spec.state(meta.get("status")) != "open":
            continue

        due = _parse_date(meta.get(spec.due_field)) if spec.due_field else None
        age = item_age_days(meta, spec, ref)

        overdue = bool(due and due < ref)
        due_today = bool(due and due == ref)
        upcoming = bool(due and ref < due <= horizon)
        stale = bool(
            spec.stale_after_days is not None
            and age is not None
            and age > spec.stale_after_days
            and not (overdue or due_today)  # a dated nag already covers it
        )
        if not (overdue or due_today or upcoming or stale):
            continue

        reasons = [r for r, on in (
            ("overdue", overdue), ("due_today", due_today),
            ("stale", stale), ("upcoming", upcoming),
        ) if on]
        items.append({
            "path": rel,
            "kind": spec.category,
            "title": meta.get("title") or rel,
            "status": str(meta.get("status") or "") or None,
            "due": due.isoformat() if due else None,
            "age_days": age,
            "reasons": reasons,
            "bucket": reasons[0],
            "remind_every": reminder_interval(meta, spec),
        })

    # Within a bucket, lead with the most pressing: most overdue / oldest first.
    def _urgency(it: dict[str, Any]) -> tuple:
        due = _parse_date(it["due"])
        days_over = (ref - due).days if due else -1
        return (-days_over, -(it["age_days"] or 0), it["kind"], it["path"])

    items.sort(key=_urgency)
    buckets: dict[str, list[dict[str, Any]]] = {
        b: [it for it in items if it["bucket"] == b]
        for b in ("overdue", "due_today", "stale", "upcoming")
    }
    return {
        "as_of": ref.isoformat(),
        "count": len(items),
        "buckets": buckets,
        "items": items,
    }


# --- reminder cadence ------------------------------------------------------

DEFAULT_LEDGER = ".cairn/reminder_state.json"


def _read_ledger(ws: Workspace, state_file: str) -> dict[str, str]:
    p = ws.resolve(state_file)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}  # a corrupt ledger must never block a reminder


def _write_ledger(ws: Workspace, state_file: str, ledger: dict[str, str]) -> None:
    p = ws.resolve(state_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True),
                 encoding="utf-8")


def reminder_digest(
    ws: Workspace,
    today: str | None = None,
    upcoming_days: int = 3,
    path: str = "",
    record: bool = True,
    state_file: str = DEFAULT_LEDGER,
) -> dict[str, Any]:
    """The cadence-filtered reminder: only what is *due to be reminded* today.

    Where :func:`attention` returns everything open that needs the user,
    ``reminder_digest`` is what a daily cron should send: each attention item is
    kept only if it hasn't been reminded within its own cadence
    (:func:`reminder_interval`), read from a small ledger of last-reminded dates.
    Urgency overrides cadence — an ``overdue`` or ``due_today`` item is escalated
    to daily whatever its ``remind_every`` says. When ``record`` (the default),
    the ledger is stamped for every item sent and pruned of items that have since
    closed, so the next run's cadence is correct; pass ``record=False`` to preview
    without consuming a reminder.

    Same shape as :func:`attention` — ``{as_of, count, buckets, items}`` — filtered
    to the due set. A ``count`` of 0 is the signal to stay silent.
    """
    ref = _parse_date(today) or datetime.date.today()
    att = attention(ws, today=today, upcoming_days=upcoming_days, path=path)
    ledger = _read_ledger(ws, state_file)

    due: list[dict[str, Any]] = []
    for it in att["items"]:
        urgent = bool({"overdue", "due_today"} & set(it["reasons"]))
        interval = 1 if urgent else it["remind_every"]
        last = _parse_date(ledger.get(it["path"]))
        if last is None or (ref - last).days >= interval:
            due.append(it)

    if record:
        stamp = ref.isoformat()
        for it in due:
            ledger[it["path"]] = stamp
        # Drop items no longer needing attention, so a reopened item nags at once.
        active = {it["path"] for it in att["items"]}
        ledger = {k: v for k, v in ledger.items() if k in active}
        _write_ledger(ws, state_file, ledger)

    buckets = {
        b: [it for it in due if it["bucket"] == b]
        for b in ("overdue", "due_today", "stale", "upcoming")
    }
    return {"as_of": ref.isoformat(), "count": len(due), "buckets": buckets, "items": due}


# --- the shared writer -----------------------------------------------------

def write_fields(
    ws: Workspace,
    path: str,
    updates: dict[str, Any],
    when: str | None = None,
) -> dict[str, Any]:
    """Surgically write metadata ``updates`` to a note — the one low-level writer.

    ``.uni`` metadata is merged; markdown frontmatter is rewritten field-by-field
    (:func:`cairn_core.frontmatter.set_field`) so the body and hand-authored
    fields survive byte-for-byte. Whenever ``updates`` changes ``status`` without
    setting ``status_changed`` itself, the change is dated automatically (``when``
    or today) — so *every* status write, whichever kind module made it, leaves the
    stamp staleness depends on. Returns the file's refreshed unified metadata.
    """
    p = ws.resolve(path)
    if not p.is_file():
        raise FileError(f"Not a file: {path!r}")
    updates = dict(updates)
    if "status" in updates and "status_changed" not in updates:
        updates["status_changed"] = when or datetime.date.today().isoformat()

    if uni.is_uni(p):
        obj = uni.read_uni(p)
        obj.setdefault("metadata", {}).update(updates)
        uni.write_uni(p, obj)
    elif _is_text(p):
        text = p.read_text(encoding="utf-8", errors="replace")
        for key, value in updates.items():
            text = frontmatter.set_field(text, key, value)
        p.write_text(text, encoding="utf-8")
    else:
        raise FileError(f"Cannot update a binary file: {path!r}")
    return query.doc_meta(p) or {}


def stamp_status(
    ws: Workspace,
    path: str,
    status: str,
    extra: dict[str, Any] | None = None,
    when: str | None = None,
) -> dict[str, Any]:
    """Set a note's ``status`` (validated against its kind) and stamp the change.

    The kind-aware entry point over :func:`write_fields`: it refuses a status the
    note's kind doesn't declare — surfacing a typo instead of writing a note into
    a state nothing recognizes — then delegates the surgical write and the
    ``status_changed`` stamp. Kind-specific writers (``complete_task``, ``settle``,
    marking a paper read) are meant to route through here.

    Returns ``{path, kind, status, state, status_changed}``.
    """
    p = ws.resolve(path)
    if not p.is_file():
        raise FileError(f"Not a file: {path!r}")
    spec = kind_of(query.doc_meta(p) or {})
    if spec is None:
        raise FileError(f"Not a lifecycle note (no known kind): {path!r}")
    new_status = str(status).strip().lower()
    if spec.state(new_status) == "unknown":
        vocab = ", ".join(spec.open_statuses + spec.closed_statuses)
        raise FileError(f"Invalid status {status!r} for {spec.category}. Use: {vocab}")

    updates: dict[str, Any] = {"status": new_status}
    if extra:
        updates.update(extra)
    meta = write_fields(ws, path, updates, when=when)
    return {
        "path": ws.relpath(p),
        "kind": spec.category,
        "status": new_status,
        "state": spec.state(new_status),
        "status_changed": meta.get("status_changed"),
    }
