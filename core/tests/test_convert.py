"""Document ingestion tests.

Stdlib-only paths (txt/csv/code/markdown-degraded) always run; parser-backed
formats (docx) run only when the optional dependency is installed.
"""

from __future__ import annotations

import csv

import pytest

from cairn_core import FileError, FileService, Workspace, convert


@pytest.fixture()
def fs(tmp_path):
    return FileService(Workspace(tmp_path))


def test_can_convert_and_format():
    assert convert.can_convert("a.docx") and convert.original_format("a.docx") == "docx"
    assert convert.can_convert("a.py") and convert.original_format("a.py") == "code"
    assert convert.can_convert("a.md") and convert.original_format("a.md") == "markdown"
    assert not convert.can_convert("a.uni")
    assert not convert.can_convert("a.png")


def test_import_txt_and_code(fs):
    (fs.ws.root / "readme.txt").write_text("line one\nline two")
    item = fs.import_file("readme.txt")
    assert item["path"] == "readme.uni"
    assert item["originalFormat"] == "text"
    assert fs.read_detail("readme.uni")["text"] == "line one\nline two"
    # original is kept by default
    assert (fs.ws.root / "readme.txt").exists()


def test_import_csv_becomes_table(fs):
    with (fs.ws.root / "d.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["a", "b"]); w.writerow(["1", "2"])
    fs.import_file("d.csv")
    content = fs.read_detail("d.uni")["content"]
    assert "<table>" in content and "<th>a</th>" in content and "<td>1</td>" in content


def test_import_replace_removes_original(fs):
    (fs.ws.root / "x.txt").write_text("hi")
    fs.import_file("x.txt", keep_original=False)
    assert not (fs.ws.root / "x.txt").exists()
    assert (fs.ws.root / "x.uni").exists()


def test_import_errors(fs):
    (fs.ws.root / "img.png").write_bytes(b"\x89PNG")
    with pytest.raises(FileError, match="Cannot convert"):
        fs.import_file("img.png")
    fs.create_file("", "already.uni", "hi")
    with pytest.raises(FileError, match="already a .uni"):
        fs.import_file("already.uni")


def test_import_tree_summary(fs):
    (fs.ws.root / "a.txt").write_text("alpha")
    (fs.ws.root / "b.png").write_bytes(b"\x89PNG")
    summary = fs.import_tree()
    assert summary["imported"] == 1
    assert summary["skipped"] == 1
    assert summary["skips"][0]["path"] == "b.png"


def test_import_docx_when_available(fs):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_heading("Title", 0)
    d.add_paragraph("body text here")
    d.save(str(fs.ws.root / "doc.docx"))
    fs.import_file("doc.docx")
    assert "body text here" in fs.read_detail("doc.uni")["text"]
