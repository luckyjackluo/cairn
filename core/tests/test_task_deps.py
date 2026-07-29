"""Task dependencies, priority, next-actions, and recurrence."""

from __future__ import annotations

import pytest

from cairn_core import tasks
from cairn_core.workspace import Workspace

TODAY = "2026-07-28"


@pytest.fixture()
def ws(tmp_path):
    return Workspace(tmp_path)


def _slug(rec):
    return rec["slug"]


# --- dependencies ----------------------------------------------------------

def test_blocked_by_gates_actionability(ws):
    a = tasks.add_task(ws, "Write draft")                       # slug: write-draft
    b = tasks.add_task(ws, "Submit paper", blocked_by=[a["slug"]])

    na = {r["slug"] for r in tasks.next_actions(ws, today=TODAY)}
    assert a["slug"] in na and b["slug"] not in na             # b waits on a

    bes = {r["slug"]: r for r in tasks.list_tasks(ws, today=TODAY)}
    assert bes[b["slug"]]["blocked"] is True
    assert bes[b["slug"]]["actionable"] is False
    assert bes[a["slug"]]["actionable"] is True


def test_completing_a_dep_unblocks_and_reports(ws):
    a = tasks.add_task(ws, "Write draft")
    b = tasks.add_task(ws, "Submit paper", blocked_by=[a["slug"]])

    done = tasks.complete_task(ws, a["path"], when=TODAY)
    assert [u["slug"] for u in done["unblocked"]] == [b["slug"]]  # a freed b

    na = {r["slug"] for r in tasks.next_actions(ws, today=TODAY)}
    assert b["slug"] in na                                       # b now actionable


def test_dangling_dep_does_not_block_forever(ws):
    t = tasks.add_task(ws, "Do thing", blocked_by=["a-task-that-was-deleted"])
    rec = {r["slug"]: r for r in tasks.list_tasks(ws, today=TODAY)}[t["slug"]]
    assert rec["blocked"] is False and rec["actionable"] is True


def test_manual_blocked_status_is_not_actionable(ws):
    t = tasks.add_task(ws, "Stuck thing", status="blocked")
    na = {r["slug"] for r in tasks.next_actions(ws, today=TODAY)}
    assert t["slug"] not in na


# --- ranking ---------------------------------------------------------------

def test_next_actions_ranks_overdue_then_priority_then_due(ws):
    tasks.add_task(ws, "later low", due="2026-08-10", priority="p3")
    tasks.add_task(ws, "overdue one", due="2026-07-20")            # overdue → first
    tasks.add_task(ws, "soon high", due="2026-08-01", priority="p1")
    tasks.add_task(ws, "soon low", due="2026-08-01", priority="p3")

    order = [r["title"] for r in tasks.next_actions(ws, today=TODAY)]
    assert order[0] == "overdue one"                              # overdue leads
    assert order.index("soon high") < order.index("soon low")     # p1 before p3
    assert order.index("soon low") < order.index("later low")     # earlier due first


def test_next_actions_project_filter(ws):
    tasks.add_task(ws, "amazon thing", project="amazon")
    tasks.add_task(ws, "gnn thing", project="gnn-agop")
    got = {r["project"] for r in tasks.next_actions(ws, project="amazon", today=TODAY)}
    assert got == {"amazon"}


# --- write round-trips + validation ---------------------------------------

def test_add_task_persists_priority_deps_recur(ws):
    a = tasks.add_task(ws, "First")
    b = tasks.add_task(ws, "Second", priority="p2", blocked_by=[a["slug"]], recur="weekly")
    assert b["priority"] == "p2"
    assert b["blocked_by"] == [a["slug"]]
    assert b["recur"] == "weekly"
    # frontmatter actually written, so a re-read agrees
    reread = {r["slug"]: r for r in tasks.list_tasks(ws, status="all", today=TODAY)}[b["slug"]]
    assert reread["priority"] == "p2" and reread["recur"] == "weekly"


def test_invalid_priority_and_recur_rejected(ws):
    with pytest.raises(Exception):
        tasks.add_task(ws, "bad prio", priority="p9")
    with pytest.raises(Exception):
        tasks.add_task(ws, "bad recur", recur="fortnightly")


# --- recurrence ------------------------------------------------------------

def test_completing_recurring_task_rolls_forward_in_place(ws):
    t = tasks.add_task(ws, "Weekly advisor sync", due="2026-07-27", recur="weekly")
    rec = tasks.complete_task(ws, t["path"], when=TODAY)

    assert rec["status"] == "todo"                       # not archived
    assert rec["recurred"]["next_due"] == "2026-08-03"   # 07-27 + 1w, past 07-28
    assert rec["recurred"]["occurrences"] == 1
    assert rec["due"] == "2026-08-03"
    # still exactly one task file, now open again at the new due date
    live = tasks.list_tasks(ws, today=TODAY)
    assert [r["slug"] for r in live] == [t["slug"]]


def test_recurring_completion_anchored_when_no_due(ws):
    t = tasks.add_task(ws, "Water plants", recur="3d")   # no due → float from completion
    rec = tasks.complete_task(ws, t["path"], when=TODAY)
    assert rec["recurred"]["next_due"] == "2026-07-31"   # 07-28 + 3d


def test_non_recurring_completion_archives(ws):
    t = tasks.add_task(ws, "One-off")
    rec = tasks.complete_task(ws, t["path"], when=TODAY)
    assert rec["status"] == "done" and "recurred" not in rec
    assert tasks.list_tasks(ws, today=TODAY) == []       # gone from the open set
