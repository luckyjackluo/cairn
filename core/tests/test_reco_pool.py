"""Ranked-pool cache: the daily run must not re-query Semantic Scholar."""

from __future__ import annotations

import datetime
import json

import pytest

from cairn_core import reco
from cairn_core.workspace import Workspace


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / ".cairn").mkdir()
    (tmp_path / ".cairn" / "paper_reco.json").write_text(
        json.dumps({"projects": {"proj": {"queries": ["q1", "q2"], "tags": ["proj"]}}}),
        encoding="utf-8",
    )
    return Workspace(tmp_path)


@pytest.fixture()
def pool(ws, monkeypatch):
    """A stub ranker that counts how many times the S2 walk actually runs."""
    calls = {"n": 0}

    def fake_rank(pcfg, defaults, api_key):
        calls["n"] += 1
        return [{"paperId": "p1", "paper": {"title": "T"}, "matched_queries": ["q1"], "score": 1.0}]

    monkeypatch.setattr(reco, "_rank_candidates", fake_rank)
    cfg = reco.load_config(ws)
    defaults = reco._defaults(cfg)
    pcfg = reco._project_config(cfg, "proj")

    def build(**kw):
        return reco._ranked_pool(ws, "proj", pcfg, defaults, None, **kw)

    return build, calls, pcfg


def test_repeat_runs_reuse_the_cached_pool(pool):
    build, calls, _ = pool
    first = build()
    assert build() == first and build() == first
    assert calls["n"] == 1  # three daily runs, one S2 walk


def test_refresh_forces_a_rebuild(pool):
    build, calls, _ = pool
    build()
    build(refresh=True)
    assert calls["n"] == 2


def test_editing_queries_invalidates_the_pool(pool):
    build, calls, pcfg = pool
    build()
    pcfg["queries"] = ["q1", "q2", "q3"]  # user retunes the project
    build()
    assert calls["n"] == 2


def test_pool_expires_after_ttl(ws, pool):
    build, calls, _ = pool
    build()
    cached = json.loads((ws.root / reco.POOL_PATH).read_text(encoding="utf-8"))
    stale = datetime.date.today() - datetime.timedelta(days=reco._DEFAULTS["pool_ttl_days"] + 1)
    cached["proj"]["built"] = stale.isoformat()
    (ws.root / reco.POOL_PATH).write_text(json.dumps(cached), encoding="utf-8")
    build()
    assert calls["n"] == 2


def test_corrupt_cache_is_a_miss_not_a_crash(ws, pool):
    build, calls, _ = pool
    build()
    (ws.root / reco.POOL_PATH).write_text("{ not json", encoding="utf-8")
    build()  # must not raise
    assert calls["n"] == 2
