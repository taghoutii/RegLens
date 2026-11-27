"""Chunking on small hand-built documents, with model-free tools.

Required test 1: chunking never cuts across an article boundary and fills every
metadata field (the same checks run on the real corpus in test_integration_chunks.py).
"""

from typing import Any

import pytest

from reglens.ingestion.chunking import (
    STRATEGIES,
    Chunker,
    ChunkParams,
    chunk_id,
    unit_contexts,
)
from reglens.ingestion.models import Block, ParsedDocument
from tests.chunk_stubs import required_fields_filled, stub_tools

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
    return Chunker(stub_tools(), PARAMS, acts=ACTS)


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


def test_chunks_carry_the_references_of_their_text() -> None:
    # the regex itself is tested in test_references.py; here: they reach the chunks
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


def test_annex_context_line() -> None:
    doc = ParsedDocument(
        doc_id="ai_act", source="a.html", type_doc="regulations", langue="en",
        reglement="AI Act", parser="eurlex", masked=True,
        blocks=[Block(block_id=f"ai_act#anx-III/t{i}", parent_id="ai_act#anx-III",
                      kind="text", text=text)
                for i, text in enumerate(["ANNEX III", "High-risk AI systems",
                                          "High-risk AI systems are the following."])],
    )  # fmt: skip
    assert unit_contexts(doc)["ai_act#anx-III"] == "AI Act — Annex III — High-risk AI systems"


def test_one_chunk_per_provider_in_every_strategy() -> None:
    line = ("Provider ID: PRV-001 | Provider name: Nimbus Cloud Services | Systems: "
            "payments-gateway | Exit plan: No | Contact person: <PERSON>")  # fmt: skip
    register = ParsedDocument(
        doc_id="register", source="r.csv", type_doc="register", langue="en",
        parser="register_csv", masked=True,
        blocks=[Block(block_id="register#PRV-001/row", parent_id="register#PRV-001",
                      kind="text", text=line),
                Block(block_id="register#PRV-002/row", parent_id="register#PRV-002",
                      kind="text", text=line.replace("PRV-001", "PRV-002"))],
    )  # fmt: skip
    for strategy in STRATEGIES:
        chunks = chunker().chunk(register, strategy)
        assert [c.parent_id for c in chunks] == ["register#PRV-001", "register#PRV-002"]
        assert all(c.kind == "provider" and c.context is None for c in chunks)
        assert chunks[0].text == line and all(required_fields_filled(c) for c in chunks)
