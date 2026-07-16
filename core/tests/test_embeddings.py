"""Embeddings retrieval pipeline tests, using a deterministic offline embedder.

No network: a bag-of-words hashing embedder gives reproducible vectors so we can
verify indexing, cosine ranking, incremental refresh, and pruning end-to-end.
The lexical-fallback default is covered in test_core.py.
"""

from __future__ import annotations

import hashlib

import pytest

from cairn_core import FileService, Workspace, retrieval

pytest.importorskip("numpy")

DIMS = 128


class FakeEmbedder:
    model = "fake-v1"

    def embed(self, texts):
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str):
        v = [0.0] * DIMS
        for tok in text.lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % DIMS] += 1.0
        return v


@pytest.fixture()
def fs(tmp_path):
    return FileService(Workspace(tmp_path))


def test_embedding_ranks_semantically(fs):
    fs.create_file("", "cats.uni", "cats are wonderful feline companions")
    fs.create_file("", "cars.uni", "engines wheels roads highways traffic")
    hits = retrieval.semantic_retrieve(fs.ws, "feline cats", k=2, embedder=FakeEmbedder())
    assert hits[0]["path"] == "cats.uni"
    assert hits[0]["score"] > hits[1]["score"]


def test_reindex_counts_and_incremental(fs):
    fs.create_file("", "a.uni", "alpha beta gamma")
    fs.create_file("", "b.uni", "delta epsilon")
    emb = FakeEmbedder()
    first = retrieval.reindex(fs.ws, embedder=emb)
    assert first["indexed"] and first["updated"] == 2 and first["total"] == 2
    # No changes → nothing re-embedded.
    second = retrieval.reindex(fs.ws, embedder=emb)
    assert second["updated"] == 0 and second["total"] == 2
    # Add one → only it is embedded.
    fs.create_file("", "c.uni", "zeta")
    third = retrieval.reindex(fs.ws, embedder=emb)
    assert third["updated"] == 1 and third["total"] == 3


def test_reindex_prunes_deleted(fs):
    fs.create_file("", "keep.uni", "keep me")
    fs.create_file("", "drop.uni", "remove me")
    emb = FakeEmbedder()
    retrieval.reindex(fs.ws, embedder=emb)
    fs.delete_item("drop.uni")
    stats = retrieval.reindex(fs.ws, embedder=emb)
    assert stats["removed"] == 1 and stats["total"] == 1


def test_reindex_without_embedder_is_noop(fs):
    result = retrieval.reindex(fs.ws, embedder=None)
    assert result["indexed"] is False


def test_falls_back_to_lexical_on_embedder_error(fs):
    fs.create_file("", "doc.uni", "quantum entanglement physics")

    class Broken:
        model = "broken"

        def embed(self, texts):
            raise RuntimeError("endpoint down")

    hits = retrieval.semantic_retrieve(fs.ws, "quantum physics", k=1, embedder=Broken())
    assert hits and hits[0]["path"] == "doc.uni"  # lexical fallback still returns
