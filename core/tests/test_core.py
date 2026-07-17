"""End-to-end exercise of the core file operations against a temp workspace."""

from __future__ import annotations

import pytest

from cairn_core import FileError, FileService, Workspace, digest, query, retrieval, tags, templates
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


# --- frontmatter-aware .md notes ------------------------------------------

_NOTE = (
    "---\n"
    "title: TGN\n"
    "status: to-read\n"
    "arxiv: 2006.10637\n"
    "tags: [temporal-graph, tgn]\n"
    "---\n\n"
    "Temporal graph networks for dynamic graphs.\n"
)


def test_read_detail_exposes_md_frontmatter(fs):
    fs.create_file("", "paper.md", _NOTE)
    detail = fs.read_detail("paper.md")
    assert detail["type"] == "text"
    assert detail["tags"] == ["temporal-graph", "tgn"]
    assert detail["metadata"]["status"] == "to-read"
    assert detail["metadata"]["arxiv"] == "2006.10637"
    assert detail["text"] == _NOTE  # raw text preserved for editing


def test_tags_on_markdown_roundtrip(fs):
    ws = fs.ws
    fs.create_file("", "paper.md", _NOTE)
    assert tags.get_tags(ws, "paper.md") == ["temporal-graph", "tgn"]
    tags.set_tags(ws, "paper.md", ["tgn", "read-later"])
    assert tags.get_tags(ws, "paper.md") == ["tgn", "read-later"]
    # Other frontmatter fields survive a tag write.
    assert fs.read_detail("paper.md")["metadata"]["status"] == "to-read"


def test_tag_tree_spans_uni_and_markdown(fs):
    ws = fs.ws
    fs.create_file("", "a.uni", "alpha")
    tags.set_tags(ws, "a.uni", ["shared"])
    fs.create_file("", "b.md", "---\ntags: [shared, mdonly]\n---\nbody")
    tree = tags.get_tag_tree(ws)
    assert set(tree["shared"]) == {"a.uni", "b.md"}
    assert tree["mdonly"] == ["b.md"]


def test_find_by_meta_scalar_and_tag(fs):
    ws = fs.ws
    fs.create_file("", "p1.md", "---\nstatus: to-read\nproject: amazon\ntags: [tgn]\n---\na")
    fs.create_file("", "p2.md", "---\nstatus: read\nproject: amazon\ntags: [tgn]\n---\nb")
    fs.create_file("", "p3.md", "---\nstatus: to-read\nproject: phd\n---\nc")

    to_read = [r["path"] for r in query.find_by_meta(ws, {"status": "to-read"})]
    assert to_read == ["p1.md", "p3.md"]

    amazon_unread = query.find_by_meta(ws, {"status": "to-read", "project": "amazon"})
    assert [r["path"] for r in amazon_unread] == ["p1.md"]

    tagged = [r["path"] for r in query.find_by_meta(ws, {"tags": "tgn"})]
    assert tagged == ["p1.md", "p2.md"]


def test_find_by_meta_spans_uni(fs):
    ws = fs.ws
    fs.create_file("", "u.uni", "hi")
    tags.set_tags(ws, "u.uni", ["tgn"])
    fs.create_file("", "m.md", "---\ntags: [tgn]\n---\nhi")
    hits = {r["path"] for r in query.find_by_meta(ws, {"tags": "tgn"})}
    assert hits == {"u.uni", "m.md"}


def test_find_by_meta_case_insensitive(fs):
    ws = fs.ws
    fs.create_file("", "p.md", "---\nstatus: To-Read\n---\nx")
    assert len(query.find_by_meta(ws, {"status": "to-read"})) == 1


def test_digest_by_folder(fs):
    fs.create_file("papers", "p1.md", "---\ntitle: TGN\ndate: 2026-01-01\ntags: [tgn]\n---\nTemporal graph networks are great.")
    fs.create_file("ideas", "i1.md", "---\ntitle: Big Idea\n---\n## Heading\nThe actual gist line.")
    d = digest.build_digest(fs.ws, group_by="folder")
    assert set(d) == {"papers", "ideas"}
    p = d["papers"][0]
    assert p["title"] == "TGN" and p["tags"] == ["tgn"]
    assert p["summary"] == "Temporal graph networks are great."
    # Heading markers are skipped when summarizing.
    assert d["ideas"][0]["summary"] == "The actual gist line."


def test_digest_by_metadata_field(fs):
    fs.create_file("", "a.md", "---\ntitle: A\nstatus: to-read\n---\nx")
    fs.create_file("", "b.md", "---\ntitle: B\nstatus: read\n---\ny")
    d = digest.build_digest(fs.ws, group_by="status")
    assert [e["title"] for e in d["to-read"]] == ["A"]
    assert [e["title"] for e in d["read"]] == ["B"]


def test_digest_by_tag_repeats_entry(fs):
    fs.create_file("", "m.md", "---\ntitle: M\ntags: [x, y]\n---\nbody")
    d = digest.build_digest(fs.ws, group_by="tag")
    assert d["x"][0]["title"] == "M" and d["y"][0]["title"] == "M"


def test_paper_template_end_to_end(fs):
    ws = fs.ws
    fs.create_file(
        "research/papers", "tgn.md",
        template="paper",
        fields={
            "title": "Temporal Graph Networks",
            "arxiv": "2006.10637",
            "url": "https://arxiv.org/abs/2006.10637",
            "project": "amazon",
            "tags": ["temporal-graph", "tgn"],
            "abstract": "TGN for dynamic graphs.",
            "date": "2026-07-16",
        },
    )
    # The stamped note is immediately queryable as a read-later item...
    queue = query.find_by_meta(ws, {"status": "to-read"})
    assert [r["path"] for r in queue] == ["research/papers/tgn.md"]
    # ...and its frontmatter parses back cleanly.
    detail = fs.read_detail("research/papers/tgn.md")
    assert detail["tags"] == ["temporal-graph", "tgn"]
    assert detail["metadata"]["arxiv"] == "2006.10637"
    assert detail["metadata"]["date_saved"] == "2026-07-16"
    assert "TGN for dynamic graphs." in detail["text"]


def test_note_template_defaults_status(fs):
    fs.create_file("", "n.md", template="note", fields={"title": "T", "date": "2026-01-01"})
    assert fs.read_detail("n.md")["metadata"]["status"] == "draft"


def test_unknown_template_errors(fs):
    with pytest.raises(ValueError, match="Unknown template"):
        fs.create_file("", "x.md", template="nope")


def test_workspace_template_override(fs):
    d = fs.ws.root / templates.TEMPLATES_SUBDIR
    d.mkdir(parents=True)
    (d / "brief.md").write_text("---\nkind: brief\n---\n{body}\n", encoding="utf-8")
    assert "brief" in templates.available(fs.ws)
    fs.create_file("", "b.md", template="brief", fields={"body": "hello"})
    detail = fs.read_detail("b.md")
    assert detail["metadata"]["kind"] == "brief" and "hello" in detail["text"]


def test_retrieval_tag_boost_on_markdown(fs):
    ws = fs.ws
    fs.create_file("", "hit.md", "---\ntags: [feline]\n---\nsome unrelated prose")
    fs.create_file("", "miss.md", "feline appears once in the body only here")
    # The tag boost should lift the tagged doc above the body-only mention.
    results = retrieval.semantic_retrieve(ws, "feline", k=2, embedder=None)
    assert results[0]["path"] == "hit.md"
