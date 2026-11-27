"""PiiMasker plumbing: batching, deduplication and putting masked strings back in place.

The analyzer is replaced by a stub that "detects" the word Alice, so no spaCy model is
needed; the real anonymizer does the replacement.
"""

from typing import Any

from presidio_analyzer import RecognizerResult
from presidio_anonymizer import AnonymizerEngine

from reglens.ingestion.models import Block, ParsedDocument
from reglens.ingestion.pii import PiiMasker


class StubMasker(PiiMasker):
    def __init__(self) -> None:  # no Presidio analyzer, no model
        self.anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]
        self.analysed: list[str] = []

    def find_many(
        self, texts: list[str], language: Any, *, filtered: bool = True
    ) -> list[list[Any]]:
        self.analysed += texts
        return [
            [RecognizerResult("PERSON", t.index("Alice"), t.index("Alice") + 5, 0.9)]
            if "Alice" in t
            else []
            for t in texts
        ]


def _doc() -> ParsedDocument:
    blocks = [
        Block(block_id="r#s1/t", parent_id="r#s1", kind="text", text="Owner Alice",
              section="Alice section"),
        Block(block_id="r#s2/t", parent_id="r#s2", kind="text", text="Owner Alice"),
        Block(block_id="l#INC-1/r", parent_id="l#INC-1", kind="records", text="INC-1",
              records=[{"incident_id": "Alice", "message": "Alice acked"},
                       {"incident_id": "INC-1", "message": "Alice acked"}]),
    ]  # fmt: skip
    return ParsedDocument(doc_id="r", source="x", type_doc="policies", langue="en",
                          parser="t", title="By Alice", blocks=blocks)  # fmt: skip


def test_every_field_is_masked_in_place() -> None:
    raw = _doc()
    masked = StubMasker().mask_document(raw)
    assert masked.masked and masked.title == "By <PERSON>"
    text, again, rec = masked.blocks
    assert text.text == "Owner <PERSON>" and text.section == "<PERSON> section"
    assert again.text == "Owner <PERSON>"
    assert rec.records is not None
    assert [r["message"] for r in rec.records] == ["<PERSON> acked"] * 2
    assert rec.records[0]["incident_id"] == "Alice"  # structured field: never analysed


def test_raw_document_is_untouched_and_counts_are_per_occurrence() -> None:
    raw = _doc()
    masker = StubMasker()
    masked = masker.mask_document(raw)
    assert raw.title == "By Alice" and raw.blocks[0].text == "Owner Alice"
    assert (
        raw.blocks[2].records is not None and raw.blocks[2].records[0]["message"] == "Alice acked"
    )
    # 6 occurrences: title, 2 texts, section, 2 identical messages...
    assert masked.pii_counts == {"PERSON": 6}
    # ...but "Owner Alice" (x2) and "Alice acked" (x2) are analysed once each.
    assert masker.analysed.count("Alice acked") == 1
    assert masker.analysed.count("Owner Alice") == 1
