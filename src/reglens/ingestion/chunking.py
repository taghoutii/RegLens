"""Chunking: parsed (masked) documents -> chunks with the canonical metadata

Three strategies, to compare (structure_semantic, recursive, semantic). They differ only
on regulation text; policy sections, incidents and providers are chunked the same way in
all three.

Other types (all strategies): 1 policy section = 1 chunk - 1 incident = 1 chunk (timeline,
no monitoring noise) - 1 provider of the register = 1 chunk (its register line).

`context` says where a chunk comes from. It is kept apart from `text`, so `text` stays an
exact slice of the parsed unit; the embedding reads context + newline + text.
"""

import re
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from reglens.corpus.sources import DocType, Language
from reglens.ingestion.models import Block, ParsedDocument
from reglens.ingestion.references import find_references, most_cited, ref_key
from reglens.ingestion.text_split import (
    SentenceFinder,
    Similarities,
    Span,
    Tokens,
    recursive_split,
    semantic_split,
    sentence_spans,
)

Strategy = Literal["structure_semantic", "recursive", "semantic"]
STRATEGIES: tuple[Strategy, ...] = ("structure_semantic", "recursive", "semantic")
ChunkKind = Literal["text", "policy_section", "incident", "provider"]

# Fixed namespace: the same inputs give the same chunk_id on every machine and every run.
CHUNK_NAMESPACE = uuid.UUID("6f1d3c52-8a4e-4b7a-9c2e-0d5b7e1f2a90")


class Chunk(BaseModel):
    """One chunk and its canonical metadata."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    strategy: Strategy
    kind: ChunkKind
    position: int  # index of the chunk inside its parent unit
    text: str
    n_tokens: int
    doc_id: str
    parent_id: str
    page_number: int | None
    source: str
    type_doc: DocType
    reglement: str | None
    article: str | None
    langue: Language
    references: list[str]
    context: str | None = None  # embedded in front of the text (see the module docstring)

    def embedding_text(self, *, with_context: bool = True) -> str:
        """What the embedding model reads for this chunk."""
        if with_context and self.context:
            return self.context + "\n" + self.text
        return self.text


def chunk_id(doc_id: str, langue: str, parent_id: str, position: int, strategy: str) -> str:
    """Deterministic id (a UUID, as Qdrant requires): re-chunking gives the same ids, so
    re-indexing overwrites instead of duplicating. The language is part of the key because
    regulations exist in EN and FR with the same doc_id and parent_id."""
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{doc_id}|{langue}|{parent_id}|{position}|{strategy}"))


@dataclass(frozen=True)
class ChunkParams:
    max_tokens: int = 512
    min_tokens: int = 100
    overlap: int = 64
    threshold: float = 0.5  # semantic cut below this similarity (calibrated by the script)
    noise_events: tuple[str, ...] = ("monitoring",)


@dataclass
class ChunkTools:
    tokens: Tokens
    sentences: Callable[[str, Language], list[Span]]  # sentence spans of one line of text
    similarities: Similarities

    def finder(self, langue: Language) -> SentenceFinder:
        """The sentence finder for one language."""
        return lambda line: self.sentences(line, langue)


@dataclass
class _Piece:
    text: str
    page: int | None
    starts_block: bool = True  # False for the 2nd, 3rd... piece of a semantically cut block
    ends_block: bool = True


@dataclass
class Chunker:
    tools: ChunkTools
    params: ChunkParams
    acts: dict[str, str] = field(default_factory=dict)  # "2022/2554" -> "DORA"
    cuts: list[dict[str, object]] = field(default_factory=list)  # semantic cuts made

    # ---------- entry point ----------

    def chunk(self, doc: ParsedDocument, strategy: Strategy) -> list[Chunk]:
        if not doc.masked:
            raise ValueError(f"{doc.doc_id}: refusing to chunk an unmasked document")
        if doc.type_doc == "policies":
            return self._policy_chunks(doc, strategy)
        if doc.type_doc == "logs":
            return self._incident_chunks(doc, strategy)
        if doc.type_doc == "register":
            return self._provider_chunks(doc, strategy)
        text_blocks = [b for b in doc.blocks if b.kind in ("title", "text") and b.text.strip()]
        if strategy == "structure_semantic":
            chunks = self._structure_chunks(doc, text_blocks)
        else:
            chunks = self._stream_chunks(doc, text_blocks, strategy)
        if doc.type_doc == "regulations":
            contexts = unit_contexts(doc)
            for c in chunks:
                c.context = contexts.get(c.parent_id)
        return chunks

    # ---------- main strategy ----------

    def _structure_chunks(self, doc: ParsedDocument, blocks: list[Block]) -> list[Chunk]:
        p = self.params
        by_unit: dict[str, list[Block]] = defaultdict(list)
        for b in blocks:
            by_unit[b.parent_id].append(b)
        chunks = []
        for parent_id, unit in by_unit.items():
            pieces: list[_Piece] = []
            for b in unit:
                if self.tools.tokens.count(b.text) <= p.max_tokens:
                    pieces.append(_Piece(b.text, b.page_number))
                    continue
                spans = self._semantic(b.text, doc, parent_id, overlap=p.overlap)
                last = len(spans) - 1
                pieces.extend(_Piece(b.text[s:e], b.page_number, starts_block=i == 0,
                                     ends_block=i == last)
                              for i, (s, e) in enumerate(spans))  # fmt: skip
            for pos, piece in enumerate(self._merge_small(pieces)):
                chunks.append(self._make(doc, "structure_semantic", "text", piece.text,
                                         parent_id, pos, piece.page, unit[0].article))  # fmt: skip
        return chunks

    def _semantic(self, text: str, doc: ParsedDocument, where: str, *, overlap: int) -> list[Span]:
        p = self.params
        sentences = sentence_spans(text, self.tools.finder(doc.langue))
        spans, cuts = semantic_split(text, sentences, self.tools.tokens, self.tools.similarities,
                                     threshold=p.threshold, max_tokens=p.max_tokens,
                                     min_tokens=p.min_tokens, overlap=overlap)  # fmt: skip
        for cut in cuts:
            cut.update(doc_id=doc.doc_id, langue=doc.langue, where=where)
        self.cuts.extend(cuts)
        return spans

    def _merge_small(self, pieces: list[_Piece]) -> list[_Piece]:
        """Merge a piece under `min_tokens` with its neighbour, if the result still fits.
        Only across a block boundary (where the parsed text has a line break): pieces of
        one cut block overlap, so gluing them would repeat text."""
        count, p = self.tools.tokens.count, self.params
        out: list[_Piece] = []
        for piece in pieces:
            if out and out[-1].ends_block and piece.starts_block:
                a, b = count(out[-1].text), count(piece.text)
                if (a < p.min_tokens or b < p.min_tokens) and a + b <= p.max_tokens:
                    prev = out[-1]
                    out[-1] = _Piece(prev.text + "\n" + piece.text, prev.page,
                                     starts_block=prev.starts_block,
                                     ends_block=piece.ends_block)  # fmt: skip
                    continue
            out.append(piece)
        return out

    # ---------- comparison strategies ----------

    def _stream_chunks(
        self, doc: ParsedDocument, blocks: list[Block], strategy: Strategy
    ) -> list[Chunk]:
        """Whole document as one text; units are only used afterwards, for metadata."""
        text, block_spans = "", []
        for b in blocks:
            if text:
                text += "\n\n"
            block_spans.append((len(text), len(text) + len(b.text)))
            text += b.text
        p = self.params
        if strategy == "recursive":
            spans = recursive_split(text, self.tools.tokens, max_tokens=p.max_tokens,
                                    overlap=p.overlap)  # fmt: skip
        else:
            spans, _ = semantic_split(
                text,
                sentence_spans(text, self.tools.finder(doc.langue)),
                self.tools.tokens, self.tools.similarities, threshold=p.threshold,
                max_tokens=p.max_tokens, min_tokens=p.min_tokens, overlap=0,
            )  # fmt: skip
        chunks: list[Chunk] = []
        positions: dict[str, int] = defaultdict(int)
        for s, e in spans:
            overlaps = [(min(e, be) - max(s, bs), i)
                        for i, (bs, be) in enumerate(block_spans) if bs < e and be > s]  # fmt: skip
            if not overlaps:
                continue
            first = blocks[overlaps[0][1]]
            weight: dict[str, int] = defaultdict(int)
            for size, i in overlaps:
                weight[blocks[i].parent_id] += size
            parent_id = max(weight, key=lambda k: weight[k])  # unit holding most characters
            article = next(blocks[i].article for _, i in overlaps
                           if blocks[i].parent_id == parent_id)  # fmt: skip
            chunks.append(self._make(doc, strategy, "text", text[s:e], parent_id,
                                     positions[parent_id], first.page_number, article))  # fmt: skip
            positions[parent_id] += 1
        return chunks

    # ---------- other types ----------

    def _policy_chunks(self, doc: ParsedDocument, strategy: Strategy) -> list[Chunk]:
        names = [(short, number) for number, short in self.acts.items()]
        reglement = most_cited("\n".join(b.text for b in doc.blocks), names)
        chunks = []
        for b in doc.blocks:
            context = doc.title
            if b.section is None:  # s0: title + metadata line, already self-describing
                text, context = b.text, None
            else:
                number = b.parent_id.rsplit("#s", 1)[-1]
                text = f"{number}. {b.section}\n{b.text}"
            chunk = self._make(doc, strategy, "policy_section", text, b.parent_id, 0, None, None)
            chunk.reglement = reglement
            chunk.context = context
            chunks.append(chunk)
        return chunks

    def _incident_chunks(self, doc: ParsedDocument, strategy: Strategy) -> list[Chunk]:
        chunks = []
        for b in doc.blocks:
            lines = [b.text]
            for r in b.records or []:
                if r.get("event") in self.params.noise_events:
                    continue
                lines.append(f"{r['timestamp']} {r['event']} ({r['status']}): {r['message']}")
            chunks.append(self._make(doc, strategy, "incident", "\n".join(lines),
                                     b.parent_id, 0, None, None))  # fmt: skip
        return chunks

    def _provider_chunks(self, doc: ParsedDocument, strategy: Strategy) -> list[Chunk]:
        return [self._make(doc, strategy, "provider", b.text, b.parent_id, 0, None, None)
                for b in doc.blocks]  # fmt: skip

    # ---------- metadata ----------

    def _make(
        self,
        doc: ParsedDocument,
        strategy: Strategy,
        kind: ChunkKind,
        text: str,
        parent_id: str,
        position: int,
        page: int | None,
        article: str | None,
    ) -> Chunk:
        references: list[str] = []
        if doc.type_doc == "regulations":
            own = ref_key(doc.reglement) if doc.reglement else None
            references = find_references(text, own=own, acts=self.acts)
        return Chunk(
            chunk_id=chunk_id(doc.doc_id, doc.langue, parent_id, position, strategy),
            strategy=strategy,
            kind=kind,
            position=position,
            text=text,
            n_tokens=self.tools.tokens.count(text),
            doc_id=doc.doc_id,
            parent_id=parent_id,
            page_number=page,
            source=doc.source,
            type_doc=doc.type_doc,
            reglement=doc.reglement,
            article=article,
            langue=doc.langue,
            references=references,
        )


_ANNEX_HEADING = re.compile(r"^(ANNEXE?)\s+([IVXLC\d]+[A-Z]?)$", re.IGNORECASE)
ANNEX_TITLE_MAX_CHARS = 200  # a longer second line is body text, not the annex title


def unit_contexts(doc: ParsedDocument) -> dict[str, str]:
    """parent_id -> context line, for every article and annex of a regulation:
    "DORA — Article 19 — Reporting of ..." (the article's heading block already reads
    "Article 19 — <title>") and "AI Act — Annex III — High-risk AI systems ..." (an annex
    starts with an "ANNEX III" line, then its title). Recitals get no context line."""
    out: dict[str, str] = {}
    annexes: dict[str, list[Block]] = defaultdict(list)
    for b in doc.blocks:
        if "#anx-" in b.parent_id:
            annexes[b.parent_id].append(b)
        if b.article is None or "#art-" not in b.parent_id or b.parent_id in out:
            continue
        heading = b.text.strip() if b.kind == "title" else f"Article {b.article}"
        out[b.parent_id] = f"{doc.reglement} — {heading}" if doc.reglement else heading
    for parent_id, blocks in annexes.items():
        m = _ANNEX_HEADING.match(blocks[0].text.strip())
        number = parent_id.split("#anx-", 1)[1]
        heading = f"{m.group(1).capitalize()} {m.group(2)}" if m else f"Annex {number}"
        parts = [doc.reglement, heading] if doc.reglement else [heading]
        if m and len(blocks) > 1:
            title = blocks[1].text.strip()
            if title and "\n" not in title and len(title) <= ANNEX_TITLE_MAX_CHARS:
                parts.append(title)
        out[parent_id] = " — ".join(parts)
    return out
