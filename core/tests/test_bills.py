"""Shared bills: splitting, settling, and the person-major reminder view."""

from __future__ import annotations

import pytest

from cairn_core import FileService, Workspace, bills
from cairn_core.files import FileError


@pytest.fixture()
def fs(tmp_path):
    return FileService(Workspace(tmp_path))


def _bill(fs, place, total, people, date="2026-07-14", **kw):
    return bills.add_bill(fs.ws, place, total, people, date=date, **kw)


def _owed(rec, name):
    return next(p["owes"] for p in rec["people"] if p["name"] == name)


def test_bill_is_frontmatter_native(fs):
    _bill(fs, "Bistro X", "120.00", ["Alex", "Sam"])
    # Falls out of the generic metadata query with no bill-specific code.
    from cairn_core import query

    hits = query.find_by_meta(fs.ws, {"category": "bill"})
    assert [h["path"] for h in hits] == ["personal/bills/2026-07-14-bistro-x.md"]


def test_even_split_includes_you_and_excludes_you_from_people(fs):
    rec = _bill(fs, "Bistro X", "120.00", ["Alex", "Sam"])
    # Three heads at the table, but only the other two owe you.
    assert _owed(rec, "Alex") == "40.00"
    assert _owed(rec, "Sam") == "40.00"
    assert rec["outstanding"] == "80.00"
    assert [p["name"] for p in rec["people"]] == ["Alex", "Sam"]


def test_include_self_false_splits_only_among_guests(fs):
    rec = _bill(fs, "Ramen", "90.00", ["Alex", "Sam"], include_self=False)
    assert rec["outstanding"] == "90.00"
    assert _owed(rec, "Alex") == "45.00"


def test_rounding_remainder_falls_on_you_not_a_guest(fs):
    # 100/3 = 33.333...; guests pay 33.33 each and you absorb the extra cent.
    rec = _bill(fs, "Tacos", "100.00", ["Alex", "Sam"])
    assert _owed(rec, "Alex") == "33.33"
    assert rec["outstanding"] == "66.66"


def test_explicit_shares_pin_amounts_and_rest_splits_remainder(fs):
    rec = _bill(fs, "Sushi", "120.00", ["Alex", "Sam"], shares={"alex": "60.00"})
    # Alex is pinned; the leftover 60 splits between Sam and you.
    assert _owed(rec, "Alex") == "60.00"
    assert _owed(rec, "Sam") == "30.00"


def test_shares_exceeding_total_are_rejected(fs):
    with pytest.raises(FileError, match="exceed the total"):
        _bill(fs, "Sushi", "50.00", ["Alex"], shares={"alex": "80.00"})


def test_settle_marks_paid_and_closes_the_bill(fs):
    _bill(fs, "Bistro X", "120.00", ["Alex", "Sam"])
    out = bills.settle(fs.ws, "Alex")
    assert out["count"] == 1
    rec = out["updated"][0]
    # One down, one to go — the bill stays open until nobody is unpaid.
    assert rec["status"] == "open"
    assert rec["outstanding"] == "40.00"

    rec = bills.settle(fs.ws, "Sam")["updated"][0]
    assert rec["status"] == "settled"
    assert rec["outstanding"] == "0.00"
    assert bills.list_bills(fs.ws) == []


def test_waive_stops_reminders_but_is_distinct_from_paid(fs):
    _bill(fs, "Bistro X", "60.00", ["Alex"])
    rec = bills.settle(fs.ws, "Alex", state="waived")["updated"][0]
    assert rec["outstanding"] == "0.00"
    assert [p["state"] for p in rec["people"]] == ["waived"]


def test_settle_spans_every_open_bill_for_that_person(fs):
    _bill(fs, "Bistro X", "60.00", ["Alex"], date="2026-07-01")
    _bill(fs, "Ramen", "40.00", ["Alex", "Sam"], date="2026-07-10")
    out = bills.settle(fs.ws, "Alex")
    assert out["count"] == 2
    # Sam is untouched by Alex settling up.
    assert bills.who_owes(fs.ws)["people"][0]["name"] == "Sam"


def test_settling_someone_with_nothing_outstanding_is_an_error(fs):
    _bill(fs, "Bistro X", "60.00", ["Alex"])
    bills.settle(fs.ws, "Alex")
    with pytest.raises(FileError, match="No open bill"):
        bills.settle(fs.ws, "Alex")


def test_who_owes_totals_per_person_and_leads_with_the_oldest(fs):
    _bill(fs, "Bistro X", "120.00", ["Alex", "Sam"], date="2026-06-28")
    _bill(fs, "Ramen", "60.00", ["Alex"], date="2026-07-14")

    out = bills.who_owes(fs.ws, today="2026-07-19")
    assert out["total"] == "110.00"
    assert out["bill_count"] == 2
    alex, sam = out["people"]
    # Alex is first: their oldest debt has been outstanding longest.
    assert alex["name"] == "Alex"
    assert alex["owes"] == "70.00"
    assert alex["oldest_days"] == 21
    assert len(alex["bills"]) == 2
    assert sam["owes"] == "40.00"


def test_who_owes_is_empty_once_everyone_settles(fs):
    _bill(fs, "Bistro X", "120.00", ["Alex", "Sam"])
    bills.settle(fs.ws, "Alex")
    bills.settle(fs.ws, "Sam")
    out = bills.who_owes(fs.ws)
    assert out["people"] == [] and out["total"] == "0.00"


def test_hand_written_shorthand_people_entries_are_tolerated(fs):
    # A human editing the file by hand won't write the full triple.
    fs.create_file("", "b.md", "---\ncategory: bill\ndate: 2026-07-14\n"
                               "people: [Alex, Sam:25.00]\n---\n")
    rec = bills.list_bills(fs.ws, today="2026-07-19")[0]
    assert [p["state"] for p in rec["people"]] == ["unpaid", "unpaid"]
    # Alex's amount is unknown, so only Sam's counts toward the balance.
    assert rec["outstanding"] == "25.00"


def test_notes_on_a_bill_survive_settling(fs):
    rec = _bill(fs, "Bistro X", "60.00", ["Alex"], notes="Alex had the wine.")
    bills.settle(fs.ws, "Alex")
    assert "Alex had the wine." in fs.ws.resolve(rec["path"]).read_text()


def test_add_person_does_not_resplit_the_others(fs):
    rec = _bill(fs, "Bistro X", "120.00", ["Alex", "Sam"])
    out = bills.add_person(fs.ws, rec["path"], "Jo", "20.00")
    # Alex and Sam were already told 40 each; that must not change under them.
    assert _owed(out, "Alex") == "40.00"
    assert _owed(out, "Jo") == "20.00"


def test_duplicate_person_on_one_bill_is_rejected(fs):
    with pytest.raises(FileError, match="twice"):
        _bill(fs, "Bistro X", "60.00", ["Alex", "alex"])
