"""Attention lifecycle: one sweep unifies task/bill/paper open-vs-closed state.

These prove the cross-cutting layer, not any one kind: a single ``attention``
call surfaces an overdue task, a stale unread paper and a long-unpaid bill
together, while closed items and fresh ones stay silent — and that a status
change stamped through ``stamp_status`` is what moves an item out of the sweep.
"""

from __future__ import annotations

import pytest

from cairn_core import FileService, Workspace, bills, lifecycle, query, tasks, templates


@pytest.fixture()
def fs(tmp_path):
    return FileService(Workspace(tmp_path))


def _render(template, fields):
    return templates.render(template, fields)


def _task(fs, name, *, status="todo", due="", created=""):
    fs.create_file("tasks", name, _render(
        "task", {"title": name, "status": status, "due": due, "created": created}))


def _paper(fs, name, *, status="to-read", date_saved=""):
    fs.create_file("research/papers", name, _render(
        "paper", {"title": name, "status": status, "date": date_saved}))


def _bill(fs, name, *, status="open", date=""):
    fs.create_file("personal/bills", name, _render(
        "bill", {"title": name, "status": status, "date": date,
                 "place": name, "total": "20.00", "people": "alex:20.00:unpaid"}))


# --- state derivation ------------------------------------------------------

def test_state_of_maps_each_kind_to_open_or_closed():
    assert lifecycle.state_of({"category": "task", "status": "doing"}) == "open"
    assert lifecycle.state_of({"category": "task", "status": "done"}) == "closed"
    assert lifecycle.state_of({"category": "paper", "status": "to-read"}) == "open"
    assert lifecycle.state_of({"category": "paper", "status": "read"}) == "closed"
    assert lifecycle.state_of({"category": "bill", "status": "settled"}) == "closed"
    # A plain note is not a lifecycle kind at all.
    assert lifecycle.state_of({"category": "note", "status": "draft"}) is None
    # A recognized kind with an unrecognized status is surfaced, not guessed.
    assert lifecycle.state_of({"category": "task", "status": "bogus"}) == "unknown"


def test_kind_resolves_by_tag_when_category_absent():
    assert lifecycle.kind_of({"tags": ["bill"]}).category == "bill"
    assert lifecycle.kind_of({"tags": ["misc"]}) is None


# --- the sweep -------------------------------------------------------------

def test_attention_unifies_three_kinds_in_one_sweep(fs):
    _task(fs, "overdue.md", due="2026-07-10")            # overdue by deadline
    _task(fs, "future.md", due="2026-07-25")             # not yet due -> silent
    _task(fs, "done.md", status="done", due="2026-07-01")  # closed -> silent
    _paper(fs, "stale.md", date_saved="2026-06-01")      # unread 49d -> stale
    _paper(fs, "fresh.md", date_saved="2026-07-18")      # unread 1d -> silent
    _bill(fs, "old.md", date="2026-05-01")               # unpaid 80d -> stale
    _bill(fs, "recent.md", date="2026-07-15")            # unpaid 4d -> silent

    out = lifecycle.attention(fs.ws, today="2026-07-19")
    flagged = {it["path"]: it for it in out["items"]}

    assert set(flagged) == {"tasks/overdue.md", "research/papers/stale.md",
                            "personal/bills/old.md"}
    assert flagged["tasks/overdue.md"]["bucket"] == "overdue"
    assert flagged["research/papers/stale.md"]["bucket"] == "stale"
    assert flagged["personal/bills/old.md"]["bucket"] == "stale"
    assert out["count"] == 3
    assert [it["path"] for it in out["buckets"]["overdue"]] == ["tasks/overdue.md"]


def test_upcoming_bucket_catches_near_due_tasks(fs):
    _task(fs, "soon.md", due="2026-07-21")   # 2 days out, within horizon
    _task(fs, "later.md", due="2026-07-30")  # beyond the 3-day horizon

    out = lifecycle.attention(fs.ws, today="2026-07-19", upcoming_days=3)
    assert [it["path"] for it in out["buckets"]["upcoming"]] == ["tasks/soon.md"]


def test_kinds_filter_restricts_the_sweep(fs):
    _task(fs, "overdue.md", due="2026-07-10")
    _bill(fs, "old.md", date="2026-05-01")

    out = lifecycle.attention(fs.ws, kinds=["bill"], today="2026-07-19")
    assert [it["kind"] for it in out["items"]] == ["bill"]


# --- the shared writer -----------------------------------------------------

def test_stamp_status_moves_an_item_out_of_the_sweep(fs):
    _task(fs, "overdue.md", due="2026-07-10")
    assert lifecycle.attention(fs.ws, today="2026-07-19")["count"] == 1

    res = lifecycle.stamp_status(fs.ws, "tasks/overdue.md", "done")
    assert res["state"] == "closed"
    assert res["status_changed"]  # a date was stamped
    assert lifecycle.attention(fs.ws, today="2026-07-19")["count"] == 0


def test_stamp_status_stamp_defers_staleness(fs):
    # A paper saved long ago but just re-touched is not stale: status_changed wins.
    _paper(fs, "revived.md", date_saved="2026-06-01")
    assert lifecycle.attention(fs.ws, today="2026-07-19")["count"] == 1

    lifecycle.stamp_status(fs.ws, "research/papers/revived.md", "to-read",
                           when="2026-07-18")
    assert lifecycle.attention(fs.ws, today="2026-07-19")["count"] == 0


def test_stamp_status_rejects_unknown_status(fs):
    _task(fs, "t.md")
    with pytest.raises(Exception):
        lifecycle.stamp_status(fs.ws, "tasks/t.md", "nonsense")


def test_kind_writers_stamp_status_changed(fs):
    # The kind-specific writers now route through the shared writer, so completing
    # a task or settling a bill leaves the status_changed stamp staleness needs.
    _task(fs, "t.md", due="2026-07-10")
    tasks.complete_task(fs.ws, "tasks/t.md", when="2026-07-19")
    assert query.doc_meta(fs.ws.resolve("tasks/t.md"))["status_changed"] == "2026-07-19"

    _bill(fs, "b.md", date="2026-05-01")
    bills.settle(fs.ws, "alex", path="personal/bills/b.md", when="2026-07-19")
    meta = query.doc_meta(fs.ws.resolve("personal/bills/b.md"))
    assert meta["status"] == "settled" and meta["status_changed"] == "2026-07-19"


def _paper_every(fs, name, *, date_saved, remind_every):
    fs.create_file("research/papers", name, _render("paper", {
        "title": name, "status": "to-read", "date": date_saved,
    }))
    # remind_every isn't a paper template field; set it as generic frontmatter.
    lifecycle.write_fields(fs.ws, f"research/papers/{name}", {"remind_every": remind_every})


# --- reminder cadence ------------------------------------------------------

def test_reminder_respects_cadence_and_records(fs):
    # A weekly paper reminds today, then stays silent until 7 days pass.
    _paper_every(fs, "weekly.md", date_saved="2026-06-01", remind_every=7)

    first = lifecycle.reminder_digest(fs.ws, today="2026-07-19")
    assert [it["path"] for it in first["items"]] == ["research/papers/weekly.md"]
    # Next day: reminded 1 day ago, cadence is 7 -> silent.
    assert lifecycle.reminder_digest(fs.ws, today="2026-07-20")["count"] == 0
    # A week on: due again.
    assert lifecycle.reminder_digest(fs.ws, today="2026-07-26")["count"] == 1


def test_reminder_preview_does_not_consume(fs):
    _paper_every(fs, "weekly.md", date_saved="2026-06-01", remind_every=7)
    lifecycle.reminder_digest(fs.ws, today="2026-07-19", record=False)
    # Nothing was recorded, so the next real run still fires.
    assert lifecycle.reminder_digest(fs.ws, today="2026-07-20")["count"] == 1


def test_urgency_escalates_cadence_to_daily(fs):
    # A task set to weekly reminders still nags daily once it's overdue.
    _task(fs, "t.md", due="2026-07-10")
    lifecycle.write_fields(fs.ws, "tasks/t.md", {"remind_every": 7})

    assert lifecycle.reminder_digest(fs.ws, today="2026-07-19")["count"] == 1
    assert lifecycle.reminder_digest(fs.ws, today="2026-07-20")["count"] == 1  # not silenced


def test_list_items_open_by_default_closed_on_request(fs):
    _task(fs, "open.md")
    _task(fs, "done.md", status="done")

    assert [i["path"] for i in lifecycle.list_items(fs.ws)] == ["tasks/open.md"]
    assert [i["path"] for i in lifecycle.list_items(fs.ws, state="closed")] == ["tasks/done.md"]
    assert len(lifecycle.list_items(fs.ws, state="all")) == 2
