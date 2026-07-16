# cairn-core

The local-first file-organization engine behind the Cairn harness.

Pure Python, **standard library only**. A workspace is just a directory; the
`.uni` document format is plain JSON with tags stored in-file. Everything the
harness can do — list, read, search, grep, edit, move, tag, retrieve — is a
plain function over a `Workspace`, so the MCP server, a CLI, or a web backend
are each thin adapters over this one package.

```python
from cairn_core import Workspace, FileService, tags, retrieval

ws = Workspace("~/Documents/notes")
fs = FileService(ws)

fs.create_file("", "hello.uni", "Hello world")
fs.get_tree()
tags.set_tags(ws, "hello.uni", ["greeting"])
retrieval.semantic_retrieve(ws, "hello", k=3)
```

## Importing real documents

The core is stdlib-only, but the optional `convert` extra adds parsers to turn
`.docx`/`.pdf`/`.pptx`/`.csv`/`.xlsx`/`.md` files into editable `.uni` docs:

```bash
pip install "cairn-core[convert]"
```

```python
fs.import_file("report.docx")   # -> report.uni (original kept)
fs.import_tree()                # import every convertible file under the workspace
```

Missing a parser raises a `ConversionError` with an install hint — nothing
crashes silently.

## Semantic retrieval

`retrieval.semantic_retrieve` is lexical by default (zero-dep). Install the
`embeddings` extra and configure any OpenAI-compatible endpoint to upgrade it to
vector search — documents are embedded into a local SQLite index under
`<workspace>/.cairn/` (incremental by mtime), and ranked by cosine similarity:

```bash
pip install "cairn-core[embeddings]"
export CAIRN_EMBED_API_KEY=sk-...          # or OPENAI_API_KEY
export CAIRN_EMBED_BASE_URL=https://api.openai.com/v1   # or a local server
export CAIRN_EMBED_MODEL=text-embedding-3-small
```

Same call, better ranking — and it silently falls back to lexical if the
endpoint is unreachable. Nothing leaves your machine unless you configure an
endpoint.

No database, no object store, no cloud. The filesystem is the source of truth.
