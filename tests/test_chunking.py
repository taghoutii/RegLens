"""Chunking on small hand-built documents, with model-free tools.

Required test 1: chunking never cuts across an article boundary and fills every
metadata field (the same checks run on the real corpus in test_integration_chunks.py).
"""

from typing import Any

import pytest

from reglens.ingestion.chunking import (
    STRATEGIES,
    Chunk,
    Chunker,
    ChunkParams,
    chunk_id,
    column_labels,
)
from reglens.ingestion.models import Block, ParsedDocument, TableData
from tests.chunk_stubs import stub_tools

PARAMS = ChunkParams(max_tokens=60, min_tokens=10, overlap=5, threshold=0.5)
ACTS = {"2022/2554": "DORA"}


def _sentences(word: str, n: int) -> str:
    return " ".join(f"{word} item {i} applies." for i in range(n))  # 4 tokens each


def regulation() -> ParsedDocument:
    def blk(art: str, suffix: str, kind: Any, text: str) -> Block:
        return Block(block_id=f"dora#art-{art}/{suffix}", parent_id=f"dora#art-{art}",
                     kind=kind, text=text, article=art)  # fmt: skip

    blocks = [
        blk("1", "title", "title", "Article 1 — Subject matter"),  # short: merged
        blk("1", "p1", "text", "1. " + _sentences("Scope", 5)),
        # 100 tokens, topic change halfway: must be cut semantically, inside article 2
        blk("2", "title", "title", "Article 2 — Definitions"),
        blk("2", "p1", "text", _sentences("Alpha", 12) + " " + _sentences("Beta", 13)),
        blk(
            "2",
            "p2",
            "text",
            "2. As referred to in Article 1, see Article 5 of Regulation (EU) 2022/2554.",
        ),  # fmt: skip
        Block(
            block_id="dora#rct-1/t",
            parent_id="dora#rct-1",
            kind="text",
            text="(1) " + _sentences("Recital", 4),
        ),  # fmt: skip
    ]
    return ParsedDocument(doc_id="dora", source="data/raw/regulations/dora_en.html",
                          type_doc="regulations", langue="en", reglement="DORA",
                          parser="eurlex", blocks=blocks, masked=True)  # fmt: skip


def unit_text(doc: ParsedDocument, parent_id: str) -> str:
    return "\n".join(b.text for b in doc.blocks if b.parent_id == parent_id)


def chunker() -> Chunker:
    return Chunker(stub_tools(), PARAMS, acts=ACTS, titles={"rep": "Bank report 2025"})


def required_fields_filled(c: Chunk) -> bool:
    common = all([c.chunk_id, c.text.strip(), c.n_tokens > 0, c.doc_id, c.source, c.langue,
                  c.parent_id.startswith(c.doc_id + "#")])  # fmt: skip
    if c.type_doc == "regulations":
        is_article = "#art-" in c.parent_id
        common = common and c.reglement is not None and (c.article is not None) == is_article
    if c.type_doc == "reports":
        common = common and c.page_number is not None
    tables = (c.header_uncertain, c.header_issues, c.has_fused_rows)
    if c.kind == "table":
        return common and all(v is not None for v in tables)
    return common and all(v is None for v in tables)


def test_required_1_no_chunk_crosses_an_article_and_metadata_is_filled() -> None:
    doc = regulation()
    chunks = chunker().chunk(doc, "structure_semantic")
    for c in chunks:
        assert c.text in unit_text(doc, c.parent_id), c.chunk_id  # a slice of ONE unit
        assert required_fields_filled(c), c
    art2 = [c for c in chunks if c.parent_id == "dora#art-2"]
    assert len(art2) >= 2  # the long paragraph was cut
    assert all(c.n_tokens <= PARAMS.max_tokens for c in chunks)
    assert all(c.article == "2" for c in art2)


def test_short_title_is_merged_with_its_paragraph() -> None:
    chunks = chunker().chunk(regulation(), "structure_semantic")
    art1 = [c for c in chunks if c.parent_id == "dora#art-1"]
    assert len(art1) == 1 and art1[0].text.startswith("Article 1 — Subject matter\n1. Scope")


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_article_chunks_carry_a_context_line(strategy: Any) -> None:
    chunks = chunker().chunk(regulation(), strategy)
    art2 = next(c for c in chunks if c.parent_id == "dora#art-2")
    assert art2.context == "DORA — Article 2 — Definitions"
    assert art2.embedding_text() == art2.context + "\n" + art2.text
    assert art2.embedding_text(with_context=False) == art2.text
    # recitals and annexes get none (recursive may absorb the short test recital)
    assert all(c.context is None for c in chunks if "#art-" not in c.parent_id)


def test_references_skip_headings() -> None:
    chunks = chunker().chunk(regulation(), "structure_semantic")
    refs = {r for c in chunks for r in c.references}
    assert refs == {"DORA:1", "DORA:5"}


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_strategy_fills_parent_id_from_the_parse(strategy: Any) -> None:
    doc = regulation()
    units = {b.parent_id for b in doc.blocks}
    chunks = chunker().chunk(doc, strategy)
    assert chunks and all(c.parent_id in units and required_fields_filled(c) for c in chunks)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_chunk_ids_are_deterministic_and_language_aware() -> None:
    a = [c.chunk_id for c in chunker().chunk(regulation(), "structure_semantic")]
    b = [c.chunk_id for c in chunker().chunk(regulation(), "structure_semantic")]
    assert a == b
    assert chunk_id("dora", "en", "dora#art-1", 0, "x") != chunk_id("dora", "fr", "dora#art-1", 0,
                                                                    "x")  # fmt: skip


def report() -> ParsedDocument:
    table = TableData(table_id="rep#tbl-1", caption="EU CC1 - Own funds", n_rows=3, n_cols=3,
                      header_rows=2,
                      cells=[["", "2025", "2025"], ["EUR m", "Total", "IRB"],
                             ["CET1", "1 571 898", "12"]],
                      low_confidence=[(2, 1)], header_issues=["same_label_cells"])  # fmt: skip
    blocks = [
        Block(
            block_id="rep#sec-1/title",
            parent_id="rep#sec-1",
            kind="title",
            text="Capital",
            page_number=3,
            section="Capital",
        ),  # fmt: skip
        Block(
            block_id="rep#sec-1/t1",
            parent_id="rep#sec-1",
            kind="text",
            page_number=3,
            text=_sentences("Capital", 4),
            section="Capital",
        ),  # fmt: skip
        Block(
            block_id="rep#tbl-1/table",
            parent_id="rep#tbl-1",
            kind="table",
            page_number=4,
            text="EU CC1 - Own funds",
            section="Capital",
            table=table,
        ),  # fmt: skip
    ]
    return ParsedDocument(doc_id="rep", source="data/raw/reports/rep.pdf", type_doc="reports",
                          langue="en", parser="docling", blocks=blocks, masked=True)  # fmt: skip


def test_table_chunk_carries_summary_and_quality_flags() -> None:
    chunks = chunker().chunk(report(), "structure_semantic")
    table = next(c for c in chunks if c.kind == "table")
    assert table.parent_id == "rep#tbl-1"  # = report_tables.table_id in PostgreSQL
    assert table.header_uncertain is True and table.header_issues == ["same_label_cells"]
    assert table.has_fused_rows is True
    assert table.text.startswith("Table: EU CC1 - Own funds\nSource: Bank report 2025, page 4")
    assert "Columns: EUR m; 2025 > Total; 2025 > IRB" in table.text
    assert "| CET1 | 1 571 898 | 12 |" in table.text
    text = next(c for c in chunks if c.kind == "text")
    assert text.header_uncertain is None and text.has_fused_rows is None


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_report_heading_without_text_is_dropped(strategy: Any) -> None:
    doc = report()
    heading = Block(block_id="rep#sec-2/title", parent_id="rep#sec-2", kind="title",
                    text="CONTENTS", page_number=2, section="CONTENTS")  # fmt: skip
    doc.blocks.insert(0, heading)
    chunks = chunker().chunk(doc, strategy)
    assert "rep#sec-2" not in {c.parent_id for c in chunks}
    assert all("CONTENTS" not in c.text for c in chunks)
    assert "rep#sec-1" in {c.parent_id for c in chunks}  # a heading with text is kept


def test_column_labels_without_header() -> None:
    t = TableData(table_id="x#tbl-1", n_rows=1, n_cols=2, cells=[["a", "b"]])
    assert column_labels(t) == []


def test_policy_and_incident_chunks() -> None:
    policy = ParsedDocument(
        doc_id="POL-001", source="p.md", type_doc="policies", langue="en", parser="policy_md",
        title="ICT Incident Policy — Payments", masked=True,
        blocks=[Block(block_id="POL-001#s0/t", parent_id="POL-001#s0", kind="title",
                      text="ICT Incident Policy — Payments"),
                Block(block_id="POL-001#s3/t", parent_id="POL-001#s3", kind="text",
                      section="Reporting", text="Report under DORA within 4 hours.")],
    )  # fmt: skip
    chunks = chunker().chunk(policy, "recursive")
    assert [c.parent_id for c in chunks] == ["POL-001#s0", "POL-001#s3"]
    assert chunks[0].context is None  # s0 already starts with the title
    assert chunks[1].text == "3. Reporting\nReport under DORA within 4 hours."
    assert chunks[1].context == "ICT Incident Policy — Payments"
    assert {c.reglement for c in chunks} == {"DORA"} and chunks[1].references == []

    events = [{"timestamp": "t1", "event": "status_change", "status": "detected",
               "message": "Alert raised"},
              {"timestamp": "t2", "event": "monitoring", "status": "investigating",
               "message": "CPU 93%"},
              {"timestamp": "t3", "event": "regulatory_report", "status": "reported",
               "message": "Initial notification sent"}]  # fmt: skip
    log = ParsedDocument(doc_id="incident_log", source="l.jsonl", type_doc="logs", langue="en",
                         parser="incident_jsonl", masked=True,
                         blocks=[Block(block_id="incident_log#INC-1/events",
                                       parent_id="incident_log#INC-1", kind="records",
                                       text="INC-1 | system x", records=events)])  # fmt: skip
    (inc,) = chunker().chunk(log, "structure_semantic")
    assert inc.kind == "incident" and "CPU 93%" not in inc.text
    assert inc.text.splitlines() == [
        "INC-1 | system x",
        "t1 status_change (detected): Alert raised",
        "t3 regulatory_report (reported): Initial notification sent",
    ]


def test_unmasked_documents_are_refused() -> None:
    doc = regulation().model_copy(update={"masked": False})
    with pytest.raises(ValueError, match="unmasked"):
        chunker().chunk(doc, "structure_semantic")
