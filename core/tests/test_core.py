"""End-to-end exercise of the core file operations against a temp workspace."""

from __future__ import annotations

import pytest

from cairn_core import FileError, FileService, Workspace, retrieval, tags
from cairn_core.workspace import PathEscapeError


@pytest.fixture()
def fs(tmp_path):
    return FileService(Workspace(tmp_path))


def test_create_and_read_uni(fs):
    item = fs.create_file("", "note.uni", "Hello world")
    assert item["path"] == "note.uni"
    detail = fs.read_detail("note.uni")
    assert detail["type"] == "uni"
    assert "Hello world" in detail["content"]
    assert detail["text"] == "Hello world"


def test_tree_and_folders(fs):
    fs.create_folder("", "docs")
    fs.create_file("docs", "a.uni", "alpha")
    tree = fs.get_tree()
    docs = next(n for n in tree if n["name"] == "docs")
    assert docs["type"] == "folder"
    assert docs["children"][0]["name"] == "a.uni"


def test_multi_edit_unique(fs):
    fs.create_file("", "doc.uni", "one two three")
    fs.multi_edit("doc.uni", "two", "TWO")
    assert "TWO" in fs.read_detail("doc.uni")["content"]


def test_multi_edit_ambiguous_errors(fs):
    fs.create_file("", "doc.uni", "aa aa")
    with pytest.raises(FileError, match="ambiguous"):
        fs.multi_edit("doc.uni", "aa", "bb")


def test_move_rename_delete(fs):
    fs.create_folder("", "inbox")
    fs.create_folder("", "archive")
    fs.create_file("inbox", "x.uni", "content")
    fs.move_item("inbox/x.uni", "archive")
    assert fs.read_detail("archive/x.uni")["name"] == "x.uni"
    fs.rename_item("archive/x.uni", "y.uni")
    assert fs.delete_item("archive/y.uni")["deleted"] is True


def test_grep_and_search(fs):
    fs.create_file("", "a.uni", "the quick brown fox")
    fs.create_file("", "b.uni", "lazy dog")
    assert fs.search_files("a.uni")[0]["path"] == "a.uni"
    hits = fs.grep("quick")
    assert hits and hits[0]["path"] == "a.uni" and hits[0]["line"] == 1


def test_tags_roundtrip(fs):
    ws = fs.ws
    fs.create_file("", "t.uni", "tagged")
    tags.set_tags(ws, "t.uni", ["work", "urgent", "work"])
    assert tags.get_tags(ws, "t.uni") == ["work", "urgent"]
    assert "work" in tags.get_tag_tree(ws)


def test_retrieval_ranks_relevant(fs):
    ws = fs.ws
    fs.create_file("", "cats.uni", "cats are wonderful feline companions")
    fs.create_file("", "cars.uni", "engines and wheels and roads")
    tags.set_tags(ws, "cats.uni", ["animals"])
    results = retrieval.semantic_retrieve(ws, "feline cats", k=2)
    assert results[0]["path"] == "cats.uni"


def test_path_escape_blocked(fs):
    with pytest.raises(PathEscapeError):
        fs.read_detail("../../etc/passwd")
