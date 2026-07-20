"""Shared-bill layer over the workspace.

A bill is an ordinary frontmatter document with ``category: bill`` — the same
stance :mod:`cairn_core.tasks` takes, and for the same reason: the metadata
layer already stores, queries and digests these documents, so a ledger of who
owes what needs no store of its own.

    ---
    title: Bistro X
    category: bill
    date: 2026-07-14
    place: Bistro X
    total: 120.00
    currency: USD
    people: [alex:40.00:unpaid, sam:40.00:unpaid]
    status: open          # open | settled
    ---

Each entry in ``people`` is a ``name:amount:state`` triple, kept flat on
purpose: :mod:`cairn_core.frontmatter` parses scalars and flat lists only, and a
flat list stays greppable and hand-editable in a way nested YAML would not. The
three states are ``unpaid``, ``paid`` (they settled up) and ``waived`` (you
decided to stop chasing it) — collapsing those two into one would lose the
distinction between money recovered and money written off.

The read side layers on what raw metadata can't express: :func:`list_bills`
derives each bill's outstanding balance, and :func:`who_owes` inverts the
bill-major store into the person-major view a reminder actually needs — one row
per debtor, totalled across bills, aged by the oldest one.

The write side stays thin, as in :mod:`cairn_core.tasks`: :func:`add_bill`
stamps the ``bill`` template through :class:`~cairn_core.files.FileService`, and
:func:`settle` is a single-field frontmatter rewrite, so hand-written notes on
the bill survive byte-for-byte.
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from . import lifecycle, query
from .files import FileError, FileService
from .workspace import Workspace

# Per-person settlement states. "unpaid" is the only one that owes you money.
UNPAID, PAID, WAIVED = "unpaid", "paid", "waived"
VALID_STATES = (UNPAID, PAID, WAIVED)
# Bill-level lifecycle: a bill settles itself once nobody is still unpaid.
OPEN, SETTLED = "open", "settled"
# The tag/category markers that identify a document as a bill.
BILL_MARKERS = ("bill",)
# Default folder new bills are filed into.
DEFAULT_DIR = "personal/bills"
DEFAULT_CURRENCY = "USD"

_CENTS = Decimal("0.01")
_STOP = {"a", "an", "the", "of", "at", "in", "on", "for", "and", "to"}


# --- shared helpers --------------------------------------------------------

def _is_bill(meta: dict[str, Any]) -> bool:
    if str(meta.get("category", "")).lower() == "bill":
        return True
    tags = {str(t).lower() for t in meta.get("tags", [])}
    return any(m in tags for m in BILL_MARKERS)


def _parse_date(value: Any) -> datetime.date | None:
    """Best-effort ISO date parse; ``None`` for empty or malformed values."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _money(value: Any) -> Decimal:
    """Parse a money-ish value to cents, tolerating ``$``/``,`` decoration."""
    raw = re.sub(r"[^0-9.\-]", "", str(value or "").strip())
    if not raw:
        raise FileError(f"Not an amount: {value!r}")
    try:
        return Decimal(raw).quantize(_CENTS, rounding=ROUND_HALF_UP)
    except Exception as exc:  # malformed decimal
        raise FileError(f"Not an amount: {value!r}") from exc


def _fmt(amount: Decimal) -> str:
    return f"{amount:.2f}"


def _slugify(title: str, limit: int = 6) -> str:
    words = [w for w in re.sub(r"[^a-z0-9\s]", "", title.lower()).split()
             if w and w not in _STOP]
    return "-".join(words[:limit]) or "bill"


def _norm_name(name: str) -> str:
    return " ".join(str(name or "").split())


def _parse_person(token: str) -> dict[str, Any] | None:
    """Parse one ``name:amount:state`` entry from the ``people`` list.

    Tolerates the shorthands a human is likely to hand-write: a bare ``name``
    (amount unknown, assumed unpaid) and ``name:amount`` (state assumed unpaid).
    """
    parts = [p.strip() for p in str(token).split(":")]
    name = _norm_name(parts[0]) if parts else ""
    if not name:
        return None
    owes: Decimal | None = None
    if len(parts) > 1 and parts[1]:
        try:
            owes = _money(parts[1])
        except FileError:
            owes = None
    state = parts[2].lower() if len(parts) > 2 and parts[2] else UNPAID
    if state not in VALID_STATES:
        state = UNPAID
    return {"name": name, "owes": owes, "state": state}


def _render_person(person: dict[str, Any]) -> str:
    owes = person.get("owes")
    return f"{person['name']}:{_fmt(owes) if owes is not None else ''}:{person['state']}"


def _people(meta: dict[str, Any]) -> list[dict[str, Any]]:
    raw = meta.get("people", [])
    if not isinstance(raw, list):
        raw = [raw] if raw not in ("", None) else []
    return [p for p in (_parse_person(t) for t in raw) if p is not None]


def _record(path: str, meta: dict[str, Any], ref: datetime.date) -> dict[str, Any]:
    """Build a bill result record from unified metadata."""
    people = _people(meta)
    date = _parse_date(meta.get("date"))
    outstanding = sum((p["owes"] or Decimal(0) for p in people
                       if p["state"] == UNPAID), Decimal(0))
    unpaid = [p["name"] for p in people if p["state"] == UNPAID]
    return {
        "path": path,
        "title": meta.get("title") or path,
        "place": meta.get("place") or meta.get("title") or None,
        "date": date.isoformat() if date else None,
        "total": str(meta.get("total") or "") or None,
        "currency": meta.get("currency") or DEFAULT_CURRENCY,
        "people": [{"name": p["name"],
                    "owes": _fmt(p["owes"]) if p["owes"] is not None else None,
                    "state": p["state"]} for p in people],
        "unpaid": unpaid,
        "outstanding": _fmt(outstanding),
        "status": SETTLED if not unpaid else OPEN,
        "age_days": (ref - date).days if date else None,
    }


# --- read ------------------------------------------------------------------

def list_bills(
    ws: Workspace,
    status: str | None = None,
    person: str | None = None,
    path: str = "",
    today: str | None = None,
) -> list[dict[str, Any]]:
    """Return bills in the workspace, oldest first.

    ``status`` defaults to ``open`` (at least one person still unpaid); pass
    ``"all"`` to include settled bills, or ``"settled"`` for just those.
    ``person`` keeps only bills that person is named on (case-insensitive).
    ``today`` overrides the reference date used for ``age_days``.

    Each result carries ``{path, title, place, date, total, currency, people,
    unpaid, outstanding, status, age_days}``.
    """
    ref = _parse_date(today) or datetime.date.today()
    want = (status or OPEN).strip().lower()
    if want not in (OPEN, SETTLED, "all", ""):
        raise FileError(f"Invalid status {status!r}. Use: open, settled, all")
    who = _norm_name(person).lower() if person else None

    root = ws.resolve(path)
    out: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        meta = query.doc_meta(p)
        if meta is None or not _is_bill(meta):
            continue

        rec = _record(ws.relpath(p), meta, ref)
        if want in (OPEN, SETTLED) and rec["status"] != want:
            continue
        if who is not None and who not in {n.lower() for n in
                                           (x["name"] for x in rec["people"])}:
            continue
        out.append(rec)

    # Oldest first — the debt you've been carrying longest leads the reminder.
    out.sort(key=lambda b: (b["date"] is None, b["date"] or "", b["path"]))
    return out


def who_owes(
    ws: Workspace,
    path: str = "",
    today: str | None = None,
) -> dict[str, Any]:
    """Invert open bills into the person-major view a reminder needs.

    Returns ``{people, total, currency, bill_count}`` where ``people`` is one
    row per debtor — ``{name, owes, bills, oldest_days}`` — sorted by the age of
    their oldest unpaid bill, so the most overdue person leads. Amounts are
    summed per currency only when the workspace is single-currency; mixed
    currencies fall back to reporting the dominant one and are flagged.
    """
    ref = _parse_date(today) or datetime.date.today()
    bills = list_bills(ws, status=OPEN, path=path, today=today)

    rows: dict[str, dict[str, Any]] = {}
    currencies: set[str] = set()
    for bill in bills:
        currencies.add(bill["currency"])
        for p in bill["people"]:
            if p["state"] != UNPAID:
                continue
            row = rows.setdefault(p["name"].lower(), {
                "name": p["name"], "owes": Decimal(0), "bills": [],
                "oldest_days": None, "unknown_amounts": 0,
            })
            if p["owes"] is None:
                row["unknown_amounts"] += 1
            else:
                row["owes"] += _money(p["owes"])
            row["bills"].append({
                "path": bill["path"], "place": bill["place"],
                "date": bill["date"], "owes": p["owes"],
                "age_days": bill["age_days"],
            })
            age = bill["age_days"]
            if age is not None and (row["oldest_days"] is None or age > row["oldest_days"]):
                row["oldest_days"] = age

    people = sorted(
        rows.values(),
        key=lambda r: (r["oldest_days"] is None, -(r["oldest_days"] or 0), r["name"].lower()),
    )
    total = sum((r["owes"] for r in people), Decimal(0))
    return {
        "people": [{**r, "owes": _fmt(r["owes"])} for r in people],
        "total": _fmt(total),
        "currency": sorted(currencies)[0] if len(currencies) == 1 else DEFAULT_CURRENCY,
        "mixed_currency": len(currencies) > 1,
        "bill_count": len(bills),
        "as_of": ref.isoformat(),
    }


# --- write -----------------------------------------------------------------

def _split_even(total: Decimal, names: list[str], shares: dict[str, Any] | None,
                include_self: bool) -> list[dict[str, Any]]:
    """Split ``total`` across ``names`` (plus you, when ``include_self``).

    Explicit ``shares`` win; whatever is left over is divided evenly among the
    remaining people. You absorb the rounding remainder — never a guest, who
    would otherwise be asked for a cent more than their share.
    """
    shares = {_norm_name(k).lower(): _money(v) for k, v in (shares or {}).items()}
    fixed = {n: shares[n.lower()] for n in names if n.lower() in shares}
    rest = [n for n in names if n.lower() not in shares]

    remainder = total - sum(fixed.values(), Decimal(0))
    if remainder < 0:
        raise FileError(
            f"Explicit shares ({_fmt(sum(fixed.values(), Decimal(0)))}) "
            f"exceed the total ({_fmt(total)})."
        )
    heads = len(rest) + (1 if include_self else 0)
    each = ((remainder / heads).quantize(_CENTS, rounding=ROUND_HALF_UP)
            if heads and rest else Decimal(0))

    people = [{"name": n, "owes": fixed.get(n, each), "state": UNPAID} for n in names]
    return people


def add_bill(
    ws: Workspace,
    place: str,
    total: Any,
    people: list[str],
    date: str | None = None,
    shares: dict[str, Any] | None = None,
    include_self: bool = True,
    currency: str = DEFAULT_CURRENCY,
    notes: str = "",
    dir: str = DEFAULT_DIR,
) -> dict[str, Any]:
    """Create a shared-bill file from the ``bill`` template and return its record.

    ``people`` are the others on the bill — not you. By default the total splits
    evenly across them plus you (``include_self``); set it false when you were
    only fronting the money and owe nothing. ``shares`` pins specific people to
    an exact amount (``{"alex": "52.30"}``) and the rest split what's left.
    """
    place = " ".join((place or "").split())
    if not place:
        raise FileError("A place (or what the bill was for) is required.")
    names = [_norm_name(n) for n in (people or []) if _norm_name(n)]
    if not names:
        raise FileError("A shared bill needs at least one other person.")
    dupes = {n.lower() for n in names}
    if len(dupes) != len(names):
        raise FileError("The same person is listed twice on this bill.")

    amount = _money(total)
    if amount <= 0:
        raise FileError("Bill total must be positive.")
    on = _parse_date(date) or datetime.date.today()
    split = _split_even(amount, names, shares, include_self)

    fs = FileService(ws)
    slug = _slugify(place)
    name = f"{on.isoformat()}-{slug}.md"
    probe = f"{dir}/{name}" if dir else name
    if ws.resolve(probe).exists():
        name = f"{on.isoformat()}-{slug}-{ws.next_uuid()}.md"
    fields = {
        "title": place,
        "place": place,
        "date": on.isoformat(),
        "total": _fmt(amount),
        "currency": currency or DEFAULT_CURRENCY,
        "people": [_render_person(p) for p in split],
        "status": OPEN,
        "tags": ["bill"],
        "notes": notes,
    }
    item = fs.create_file(dir, name, template="bill", fields=fields)
    p = ws.resolve(item["path"])
    meta = query.doc_meta(p) or {}
    return _record(item["path"], meta, datetime.date.today())


def _write_meta(
    ws: Workspace, path: str, updates: dict[str, Any], when: str | None = None,
) -> dict[str, Any]:
    """Apply metadata ``updates`` to a bill and return its record.

    Delegates to :func:`cairn_core.lifecycle.write_fields`, the shared writer, so
    closing a bill out (``status: settled``) stamps ``status_changed`` — the same
    ageing signal every kind uses. ``when`` dates that stamp so it agrees with the
    ``settled`` date the caller recorded.
    """
    meta = lifecycle.write_fields(ws, path, updates, when=when)
    return _record(ws.relpath(ws.resolve(path)), meta, datetime.date.today())


def settle(
    ws: Workspace,
    person: str,
    path: str | None = None,
    state: str = PAID,
    when: str | None = None,
) -> dict[str, Any]:
    """Mark ``person`` paid (or waived) — on one bill, or on all of them.

    With ``path`` omitted this settles every open bill that person is on, which
    is the common case: "Alex paid me back" rarely means one dinner. ``state``
    is ``paid`` when the money arrived and ``waived`` when you've decided to
    stop chasing it — both stop the reminders, only one claims you were repaid.
    Returns ``{updated, count, person, state}``; settling someone who owes
    nothing is an error rather than a silent no-op.
    """
    state = (state or PAID).lower()
    if state not in (PAID, WAIVED):
        raise FileError(f"Invalid state {state!r}. Use: paid, waived")
    who = _norm_name(person)
    if not who:
        raise FileError("A person is required.")
    stamp = when or datetime.date.today().isoformat()

    targets = ([ws.relpath(ws.resolve(path))] if path
               else [b["path"] for b in list_bills(ws, status=OPEN, person=who)])
    if not targets:
        raise FileError(f"No open bill found with {who!r} on it.")

    updated: list[dict[str, Any]] = []
    for rel in targets:
        meta = query.doc_meta(ws.resolve(rel)) or {}
        if not _is_bill(meta):
            raise FileError(f"Not a bill: {rel!r}")
        people = _people(meta)
        match = [p for p in people if p["name"].lower() == who.lower()]
        if not match:
            if path:
                raise FileError(f"{who!r} is not on {rel!r}.")
            continue
        if all(p["state"] != UNPAID for p in match):
            if path:
                raise FileError(f"{who!r} already settled on {rel!r}.")
            continue
        for p in match:
            p["state"] = state
        updates: dict[str, Any] = {"people": [_render_person(p) for p in people]}
        # Close the bill out once nobody on it is still unpaid.
        if all(p["state"] != UNPAID for p in people):
            updates["status"] = SETTLED
            updates["settled"] = stamp
        updated.append(_write_meta(ws, rel, updates, when=stamp))

    if not updated:
        raise FileError(f"Nothing outstanding for {who!r}.")
    return {"updated": updated, "count": len(updated), "person": who, "state": state}


def add_person(
    ws: Workspace,
    path: str,
    person: str,
    owes: Any,
) -> dict[str, Any]:
    """Add someone to an existing bill for an explicit amount.

    Deliberately does *not* re-split the bill: the others were already told what
    they owe, and silently revising their share after the fact is the one thing
    a ledger must never do.
    """
    who = _norm_name(person)
    if not who:
        raise FileError("A person is required.")
    amount = _money(owes)
    rel = ws.relpath(ws.resolve(path))
    meta = query.doc_meta(ws.resolve(rel)) or {}
    if not _is_bill(meta):
        raise FileError(f"Not a bill: {rel!r}")
    people = _people(meta)
    if any(p["name"].lower() == who.lower() for p in people):
        raise FileError(f"{who!r} is already on {rel!r}.")
    people.append({"name": who, "owes": amount, "state": UNPAID})
    return _write_meta(ws, rel, {
        "people": [_render_person(p) for p in people], "status": OPEN,
    })
