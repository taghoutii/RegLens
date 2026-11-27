"""The on-disk sentence-vector cache, tested alone: no model needed."""

from pathlib import Path

import torch

from reglens.ingestion.encoders import VectorStore


def test_vectors_survive_a_restart_bit_for_bit(tmp_path: Path) -> None:
    store = VectorStore(tmp_path, "model-a")
    vec = torch.nn.functional.normalize(torch.randn(1024), dim=0)
    store.put("First sentence.", vec)
    store.save()
    again = VectorStore(tmp_path, "model-a")
    assert again.loaded == 1
    cached = again.get("First sentence.")
    assert cached is not None and torch.equal(cached, vec)
    assert again.get("Another sentence.") is None


def test_key_depends_on_the_model_and_the_exact_text(tmp_path: Path) -> None:
    store = VectorStore(tmp_path, "model-a")
    store.put("Same text.", torch.ones(4))
    store.save()
    assert VectorStore(tmp_path, "model-b").get("Same text.") is None
    assert VectorStore(tmp_path, "model-a").get("Same text. ") is None


def test_nothing_is_written_without_new_vectors(tmp_path: Path) -> None:
    VectorStore(tmp_path, "model-a").save()
    assert not (tmp_path / VectorStore.FILE).exists()


def test_memory_only_without_a_folder() -> None:
    store = VectorStore(None, "model-a")
    store.put("x", torch.ones(4))
    store.save()  # no file, no error
    assert store.get("x") is not None
