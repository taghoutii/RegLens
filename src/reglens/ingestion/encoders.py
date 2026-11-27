"""Production tools for chunking: bge-m3 tokens and sentence similarities, spaCy sentences.

only the sentences of pieces that need a semantic cut are embedded, and
each distinct sentence is embedded once. Vectors are also kept on disk (VectorStore),
so a rebuild embeds only new or changed sentences.
"""

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

import spacy
import torch
from transformers import AutoModel, AutoTokenizer

from reglens.corpus.sources import Language
from reglens.ingestion.text_split import Span

logger = logging.getLogger(__name__)


class VectorStore:
    """Sentence vectors by key, saved as one file: sentence keys + one stacked tensor.

    The key is a hash of the model name and the exact sentence, so a vector is reused
    only for the same text and the same model. Values are stored exactly (float32): a
    cached vector is bit-identical to the one computed, so chunks do not depend on
    whether the cache was warm. Entries of sentences no longer in the corpus stay in the
    file (delete the folder to reset it)."""

    FILE = "sentence_vectors.pt"

    def __init__(self, folder: Path | None, model_name: str) -> None:
        self.path = folder / self.FILE if folder else None
        self.model_name = model_name
        self.vectors: dict[str, torch.Tensor] = {}
        self.unsaved = 0
        if self.path and self.path.exists():
            saved = torch.load(self.path, weights_only=True)
            self.vectors = dict(zip(saved["keys"], saved["vectors"], strict=True))
        self.loaded = len(self.vectors)

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model_name}\n{text}".encode()).hexdigest()

    def get(self, text: str) -> torch.Tensor | None:
        return self.vectors.get(self.key(text))

    def put(self, text: str, vector: torch.Tensor) -> None:
        self.vectors[self.key(text)] = vector
        self.unsaved += 1

    def save(self) -> None:
        """Write the file if new vectors were added (written aside, then swapped in)."""
        if self.path is None or not self.unsaved:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        keys = list(self.vectors)
        torch.save({"keys": keys, "vectors": torch.stack([self.vectors[k] for k in keys])}, tmp)
        tmp.replace(self.path)
        logger.info("vector cache: %d vectors saved (%d new)", len(keys), self.unsaved)
        self.unsaved = 0


class Bgem3:
    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int = 32,
        max_length: int = 512,
        cache_dir: Path | None = None,
    ) -> None:
        torch.set_num_threads(os.cpu_count() or 4)
        self.tokenizer: Any = AutoTokenizer.from_pretrained(model_name)
        self._model: Any = None  # loaded on first embedding (token counting needs no model)
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_length = max_length
        self.store = VectorStore(cache_dir, model_name)  # cache_dir None: memory only
        self.computed = 0  # vectors computed by the model in this run
        self.embed_seconds = 0.0
        self.load_seconds = 0.0

    # ---------- tokens ----------

    def _offsets(self, text: str) -> list[tuple[int, int]]:
        enc = self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True,
                             verbose=False)  # fmt: skip
        return [tuple(o) for o in enc["offset_mapping"]]

    def count(self, text: str) -> int:
        return len(self.tokenizer(text, add_special_tokens=False, verbose=False)["input_ids"])

    def tail_start(self, text: str, n: int) -> int:
        offsets = self._offsets(text)
        return offsets[-n][0] if 0 < n < len(offsets) else 0

    def head_end(self, text: str, n: int) -> int:
        offsets = self._offsets(text)
        return offsets[n - 1][1] if 0 < n < len(offsets) else len(text)

    # ---------- embeddings ----------

    @property
    def model(self) -> Any:
        if self._model is None:
            t0 = time.perf_counter()
            self._model = AutoModel.from_pretrained(self.model_name).eval()
            self.load_seconds = time.perf_counter() - t0
        return self._model

    def parameter_bytes(self) -> int:
        return sum(p.numel() * p.element_size() for p in self.model.parameters())

    def embed(self, texts: list[str]) -> torch.Tensor:
        """Normalised dense vectors, one row per text (cached by exact text)."""
        found: dict[str, torch.Tensor] = {}
        for t in dict.fromkeys(texts):
            vec = self.store.get(t)
            if vec is not None:
                found[t] = vec
        todo = sorted(set(texts) - set(found), key=len)
        t0 = time.perf_counter()
        with torch.inference_mode():
            for i in range(0, len(todo), self.batch_size):
                batch = todo[i : i + self.batch_size]
                enc = self.tokenizer(batch, padding=True, truncation=True,
                                     max_length=self.max_length, return_tensors="pt")  # fmt: skip
                cls = self.model(**enc).last_hidden_state[:, 0]
                for text, vec in zip(batch, torch.nn.functional.normalize(cls, dim=-1),
                                     strict=True):  # fmt: skip
                    self.store.put(text, vec.clone())  # own storage, not a view of the batch
                    found[text] = vec
                    self.computed += 1
        self.embed_seconds += time.perf_counter() - t0
        return torch.stack([found[t] for t in texts])

    def similarities(self, sentences: list[str]) -> list[float]:
        """Cosine similarity of each sentence with the next one (len - 1 values)."""
        if len(sentences) < 2:
            return []
        v = self.embed(sentences)
        return [float(x) for x in (v[:-1] * v[1:]).sum(dim=-1)]

    @property
    def embedded(self) -> int:
        """Vectors computed by the model in this run (not those read from the cache)."""
        return self.computed


class SpacySentences:
    """Sentence boundaries from spaCy's dependency parser (the md models used for PII)."""

    def __init__(self, models: dict[str, str]) -> None:
        self._nlp = {
            lang: spacy.load(name, exclude=["ner", "lemmatizer", "attribute_ruler"])
            for lang, name in models.items()
        }

    def __call__(self, text: str, langue: Language) -> list[Span]:
        doc = self._nlp[langue](text)
        return [(s.start_char, s.end_char) for s in doc.sents if s.text.strip()]
