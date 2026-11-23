"""Chunking: parsed (masked) documents -> chunks with the canonical metadata

Three strategies, to compare (structure_semantic, recursive, semantic). They differ only
on running text; tables, policy sections and incidents are chunked the same way in all three

Other types (all strategies): 1 policy section = 1 chunk - 1 incident = 1 chunk (timeline,
no monitoring noise) - tables:
    a table that fits (summary + its row lines <= max_tokens) is 1 chunk;
    a larger table is cut into row groups (whole rows, each group <= max_tokens);
    a row wider than that alone is cut into column groups.
    text = the Markdown rows (display, citations); context = the template summary, plus the
    header rows for a row group; row_lines = one self-describing line per row
    ("label — header path = value; ..."), which the embedding reads instead of the Markdown.

`context` says where a chunk comes from. It is kept apart from `text`, so `text` stays an
exact slice of the parsed unit (column groups excepted); the embedding reads context +
newline + text (+ row lines instead of text for tables). 
"""

import re
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from reglens.corpus.sources import DocType, Language
from reglens.ingestion.models import Block, ParsedDocument, TableData
from reglens.ingestion.references import find_references, most_cited, ref_key
from reglens.ingestion.table_rows import (
    Row,
    data_rows,
    header_paths,
    label_columns,
    row_cells,
)
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
ChunkKind = Literal["text", "table", "policy_section", "incident"]

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
    # Table chunks only (None for every other kind).
    row_lines: str | None = None  # self-describing rows: embedded instead of the Markdown
    table_source: Literal["pdf", "spreadsheet"] | None = None  # where the values come from
    header_uncertain: bool | None = None
    header_issues: list[str] | None = None
    has_fused_rows: bool | None = None

    def embedding_text(self, *, with_context: bool = True) -> str:
        """What the embedding model reads for this chunk (row lines for a table)."""
        body = self.row_lines if self.row_lines is not None else self.text
        if with_context and self.context:
            return self.context + "\n" + body
        return body


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
    table_context_max_tokens: int = 256  # a row group's context is shortened beyond this


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
    titles: dict[str, str] = field(default_factory=dict)  # doc_id -> human title
    cuts: list[dict[str, object]] = field(default_factory=list)  # semantic cuts made

    # ---------- entry point ----------

    def chunk(self, doc: ParsedDocument, strategy: Strategy) -> list[Chunk]:
        if not doc.masked:
            raise ValueError(f"{doc.doc_id}: refusing to chunk an unmasked document")
        if doc.type_doc == "policies":
            return self._policy_chunks(doc, strategy)
        if doc.type_doc == "logs":
            return self._incident_chunks(doc, strategy)
        text_blocks = [b for b in doc.blocks if b.kind in ("title", "text") and b.text.strip()]
        if doc.type_doc == "reports":
            # A report section with a heading and no text ("CONTENTS", a heading above a
            # table) would be a chunk of a few tokens; its name already appears in the
            # summaries of the tables under it.
            with_text = {b.parent_id for b in text_blocks if b.kind == "text"}
            text_blocks = [b for b in text_blocks if b.parent_id in with_text]
        if strategy == "structure_semantic":
            chunks = self._structure_chunks(doc, text_blocks)
        else:
            chunks = self._stream_chunks(doc, text_blocks, strategy)
        if doc.type_doc == "regulations":
            contexts = unit_contexts(doc)
            for c in chunks:
                c.context = contexts.get(c.parent_id)
        return chunks + self._table_chunks(doc, strategy)

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

    def _table_chunks(self, doc: ParsedDocument, strategy: Strategy) -> list[Chunk]:
        chunks = []
        for b in doc.blocks:
            if b.kind != "table" or b.table is None or not b.table.cells:
                continue
            t = b.table
            summary = table_summary(t, b.section, b.page_number,
                                    self.titles.get(doc.doc_id, doc.doc_id))  # fmt: skip
            groups = table_groups(t, doc.langue, summary, self.tools.tokens,
                                  self.params.max_tokens,
                                  self.params.table_context_max_tokens)  # fmt: skip
            for position, g in enumerate(groups):
                chunk = self._make(doc, strategy, "table", g.text, t.table_id, position,
                                   b.page_number, None)  # fmt: skip
                chunk.context = g.context
                chunk.row_lines = g.lines
                chunk.table_source = t.content_source
                chunk.header_uncertain = t.header_uncertain
                chunk.header_issues = list(t.header_issues)
                chunk.has_fused_rows = bool(t.low_confidence)
                chunks.append(chunk)
        return chunks

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


@dataclass(frozen=True)
class TableGroup:
    text: str  # Markdown rows
    context: str  # summary (+ header rows for a row group)
    lines: str  # self-describing row lines, embedded


def table_groups(
    table: TableData,
    langue: Language,
    summary: str,
    tokens: Tokens,
    max_tokens: int,
    context_max_tokens: int,
) -> list[TableGroup]:
    """Cut a table into chunks of whole rows (see the module docstring). Token sizes of
    a group are the sum of its lines' sizes plus one per line break (an upper bound)."""
    rows = data_rows(table, langue)
    paths = header_paths(table)
    lines = [f"{r.label} — " + "; ".join(row_cells(r, paths)) for r in rows]
    markdown = table.to_markdown().split("\n")
    sep = max(table.header_rows, 1)  # to_markdown puts its separator after this many rows

    def md_line(r: int) -> str:
        return markdown[r + 1] if r >= sep else markdown[r]

    def size(texts: list[str]) -> int:
        return sum(tokens.count(x) for x in texts) + max(len(texts) - 1, 0)

    whole = "\n".join(markdown)
    if not rows or (size([summary, *lines]) <= max_tokens and tokens.count(whole) <= max_tokens):
        return [TableGroup(whole, summary, "\n".join(lines))]

    header = "\n".join(markdown[: sep + 1]) if table.header_rows else ""
    title = "\n".join(summary.split("\n")[:2])  # "Table: ..." and "Source: ..."
    options = [x for x in (summary + "\n" + header if header else summary,
                           title + "\n" + header if header else None, title) if x]  # fmt: skip
    context = next((c for c in options if tokens.count(c) <= context_max_tokens), title)
    budget = max_tokens - tokens.count(context) - 1
    k = label_columns(table, langue)

    groups: list[TableGroup] = []
    current: list[tuple[Row, str]] = []

    def flush() -> None:
        if current:
            first, last = current[0][0].index, current[-1][0].index
            text = "\n".join(md_line(r) for r in range(first, last + 1))
            groups.append(TableGroup(text, context, "\n".join(x for _, x in current)))
            current.clear()

    for row, line in zip(rows, lines, strict=True):
        if tokens.count(line) > budget:  # too wide: one chunk per column group
            flush()
            groups.extend(_column_groups(table, row, paths, k, context, budget, tokens))
            continue
        candidate = [x for _, x in current] + [line]
        md = [md_line(r) for r in range(current[0][0].index if current else row.index,
                                        row.index + 1)]  # fmt: skip
        if current and (size(candidate) > budget or size(md) > max_tokens):
            flush()
        current.append((row, line))
    flush()
    return groups


def _column_groups(
    table: TableData, row: Row, paths: list[str], k: int, context: str, budget: int,
    tokens: Tokens,
) -> list[TableGroup]:  # fmt: skip
    """A row too wide for one chunk, cut into consecutive groups of columns. Its text is
    the Markdown of the label cells and that group's cells (not an exact slice)."""
    prefix = f"{row.label} — "
    cells = row_cells(row, paths)
    columns = sorted(row.values)
    groups, start = [], 0
    while start < len(cells):
        end = start + 1
        while (
            end < len(cells) and tokens.count(prefix + "; ".join(cells[start : end + 1])) <= budget
        ):
            end += 1
        keep = list(range(k)) + columns[start:end]
        text = "| " + " | ".join(table.cells[row.index][c] for c in keep) + " |"
        groups.append(TableGroup(text, context, prefix + "; ".join(cells[start:end])))
        start = end
    return groups


def table_summary(table: TableData, section: str | None, page: int | None, title: str) -> str:
    """Template summary (no LLM): caption, where the table is, and its column headers
    (each distinct header path once)."""
    where = f"Source: {title}" + (f", page {page}" if page else "")
    where += f", section {section}" if section else ""
    labels = list(dict.fromkeys(p for p in header_paths(table) if p))
    return "\n".join([
        f"Table: {table.caption or '(no caption)'}",
        where,
        "Columns: " + ("; ".join(labels) if labels else "(no header row detected)"),
    ])  # fmt: skip
