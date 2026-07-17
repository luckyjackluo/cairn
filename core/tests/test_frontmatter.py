"""Unit tests for the stdlib YAML-frontmatter parser/writer."""

from __future__ import annotations

from cairn_core import frontmatter as fm

DOC = """---
title: Temporal Graph Networks
status: to-read
arxiv: 2006.10637
tags: [temporal-graph, tgn]
authors:
  - Rossi
  - Chamberlain
---

## Abstract
Body text here.
"""


def test_parse_scalars_and_lists():
    meta, body = fm.parse(DOC)
    assert meta["title"] == "Temporal Graph Networks"
    assert meta["status"] == "to-read"
    assert meta["arxiv"] == "2006.10637"
    assert meta["tags"] == ["temporal-graph", "tgn"]
    assert meta["authors"] == ["Rossi", "Chamberlain"]  # block list
    assert body.lstrip().startswith("## Abstract")


def test_no_frontmatter_is_identity():
    text = "# Just markdown\n\nno frontmatter here"
    meta, body = fm.parse(text)
    assert meta == {}
    assert body == text


def test_unterminated_fence_is_not_frontmatter():
    text = "---\ntitle: x\n\nnever closed"
    meta, body = fm.parse(text)
    assert meta == {}
    assert body == text


def test_get_tags_quoted_and_inline():
    text = '---\ntags: ["a b", c]\n---\nbody'
    assert fm.get_tags(text) == ["a b", "c"]


def test_set_field_replaces_in_place_preserving_others():
    out = fm.set_field(DOC, "status", "read")
    meta, _ = fm.parse(out)
    assert meta["status"] == "read"
    assert meta["title"] == "Temporal Graph Networks"  # untouched
    assert meta["tags"] == ["temporal-graph", "tgn"]


def test_set_field_collapses_block_list():
    out = fm.set_field(DOC, "authors", ["Solo"])
    meta, _ = fm.parse(out)
    assert meta["authors"] == ["Solo"]
    # The old block-list "- Rossi / - Chamberlain" lines must be gone.
    assert "Rossi" not in out
    assert meta["title"] == "Temporal Graph Networks"


def test_set_field_inserts_new_key():
    out = fm.set_field(DOC, "project", "amazon")
    assert fm.parse(out)[0]["project"] == "amazon"


def test_set_field_creates_block_when_absent():
    out = fm.set_field("plain body", "tags", ["x", "y"])
    meta, body = fm.parse(out)
    assert meta["tags"] == ["x", "y"]
    assert body.strip() == "plain body"


def test_set_tags_roundtrips_through_parse():
    text = fm.set_field("hello", "tags", ["one"])
    text = fm.set_field(text, "tags", ["one", "two"])
    assert fm.get_tags(text) == ["one", "two"]
