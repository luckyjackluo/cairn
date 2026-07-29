"""Waiting-on kind: create, roll up by person, resolve, and lifecycle integration."""

from __future__ import annotations

import pytest

from cairn_core import lifecycle, waiting
from cairn_core.workspace import Workspace

TODAY = "2026-07-28"


@pytest.fixture()
def ws(tmp_path):
    return Workspace(tmp_path)


def test_add_and_list_longest_wait_first(ws):
    waiting.add_waiting(ws, "review the draft", "advisor", since="2026-07-20")   # 8d
    waiting.add_waiting(ws, "marketing feedback", "ryan", since="2026-07-26")    # 2d
    out = waiting.list_waiting(ws, today=TODAY)
    assert [w["person"] for w in out] == ["advisor", "ryan"]        # 8d before 2d
    assert out[0]["age_days"] == 8 and out[0]["status"] == "waiting"


def test_who_owes_me_groups_by_person(ws):
    waiting.add_waiting(ws, "review draft", "advisor", since="2026-07-20")
    waiting.add_waiting(ws, "sign the form", "advisor", since="2026-07-24")
    waiting.add_waiting(ws, "feedback", "ryan", since="2026-07-27")
    owed = waiting.who_owes_me(ws, today=TODAY)
    assert [g["person"] for g in owed] == ["advisor", "ryan"]       # advisor waited longest
    advisor = owed[0]
    assert advisor["count"] == 2 and advisor["oldest_age_days"] == 8


def test_mark_received_and_drop_close_the_item(ws):
    a = waiting.add_waiting(ws, "review", "advisor", since="2026-07-20")
    b = waiting.add_waiting(ws, "reply", "sam", since="2026-07-20")
    got = waiting.mark_received(ws, a["path"], when=TODAY)
    assert got["state"] == "closed" and got["status"] == "received"
    waiting.drop_waiting(ws, b["path"], when=TODAY)
    assert waiting.list_waiting(ws, today=TODAY) == []              # nothing open
    assert len(waiting.list_waiting(ws, state="closed", today=TODAY)) == 2


def test_waiting_surfaces_in_attention_when_stale(ws):
    waiting.add_waiting(ws, "review draft", "advisor", since="2026-07-20")  # 8d > stale_after 4
    att = lifecycle.attention(ws, today=TODAY)
    w = [i for i in att["items"] if i["kind"] == "waiting"]
    assert w and w[0]["bucket"] == "stale"


def test_due_makes_it_overdue(ws):
    w = waiting.add_waiting(ws, "signed form", "advisor", since="2026-07-25", due="2026-07-26")
    rec = waiting.list_waiting(ws, today=TODAY)[0]
    assert rec["overdue"] is True
    att = lifecycle.attention(ws, today=TODAY)
    assert any(i["kind"] == "waiting" and i["bucket"] == "overdue" for i in att["items"])


def test_kind_and_template_registered():
    from cairn_core import templates
    assert "waiting" in lifecycle.KINDS
    assert "waiting" in templates.available()
    spec = lifecycle.KINDS["waiting"]
    assert spec.state("waiting") == "open" and spec.state("received") == "closed"
