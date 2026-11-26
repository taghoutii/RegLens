"""Model-free stand-ins for the chunking tools (no bge-m3, no spaCy).

tokens        1 word = 1 token
sentences     split after ". " / "? " / "! "
similarities  1.0 if two sentences start with the same word, else 0.0 (a "topic change")

Also the chunk check shared by the unit and the integration tests (required_fields_filled).
"""

import re

from reglens.corpus.sources import Language
from reglens.ingestion.chunking import Chunk, ChunkTools
from reglens.ingestion.text_split import Span

_WORD = re.compile(r"\S+")


class WordTokens:
    def count(self, text: str) -> int:
        return len(text.split())

    def tail_start(self, text: str, n: int) -> int:
        starts = [m.start() for m in _WORD.finditer(text)]
        return starts[-n] if 0 < n < len(starts) else 0

    def head_end(self, text: str, n: int) -> int:
        ends = [m.end() for m in _WORD.finditer(text)]
        return ends[n - 1] if 0 < n < len(ends) else len(text)


def regex_sentences(text: str, langue: Language = "en") -> list[Span]:
    return [(m.start(), m.end()) for m in re.finditer(r"\S.*?(?:[.!?](?=\s)|$)", text)]


def first_word_similarity(sentences: list[str]) -> list[float]:
    first = [s.split()[0] if s.split() else "" for s in sentences]
    return [1.0 if a == b else 0.0 for a, b in zip(first, first[1:], strict=False)]


def stub_tools() -> ChunkTools:
    return ChunkTools(tokens=WordTokens(), sentences=regex_sentences,
                      similarities=first_word_similarity)  # fmt: skip


def required_fields_filled(c: Chunk) -> bool:
    common = all([c.chunk_id, c.text.strip(), c.n_tokens > 0, c.doc_id, c.source, c.langue,
                  c.parent_id.startswith(c.doc_id + "#")])  # fmt: skip
    if c.type_doc == "regulations":
        is_article = "#art-" in c.parent_id
        common = common and c.reglement is not None and (c.article is not None) == is_article
    return common and c.page_number is None  # no paginated source in the corpus
