"""The leak checker itself, on toy documents (no Presidio involved)."""

from reglens.evaluation.pii_leaks import Planted, check_leaks, how_it_leaks
from reglens.ingestion.models import Block, ParsedDocument


def _doc(doc_id: str, text: str) -> ParsedDocument:
    block = Block(block_id=f"{doc_id}#s1/t", parent_id=f"{doc_id}#s1", kind="text", text=text)
    return ParsedDocument(
        doc_id=doc_id, source="x", type_doc="policies", langue="en", parser="t", blocks=[block]
    )


def test_full_value_and_leftover_name_parts_are_leaks() -> None:
    assert how_it_leaks("Megan Taylor", "names", "Owner: Megan Taylor") == "full value"
    assert (
        how_it_leaks("Megan Taylor", "names", "Owner: <PERSON> Taylor") == "1 of 2 name parts left"
    )
    assert how_it_leaks("Megan Taylor", "names", "Owner: <PERSON>") is None


def test_titles_are_not_name_parts() -> None:
    assert how_it_leaks("Dr Lee Green", "names", "Dr <PERSON> paged") is None


def test_report_counts_and_never_contains_values() -> None:
    docs = {"POL-001": _doc("POL-001", "Owner: <PERSON>, key vlr_live_x")}
    planted = [
        Planted("POL-001", "names", "Megan Taylor"),
        Planted("POL-001", "secrets", "vlr_live_x"),
        Planted("POL-999", "names", "Not Parsed"),  # document not in the run: ignored
    ]
    report = check_leaks(docs, planted)
    assert dict(report.total) == {"names": 1, "secrets": 1}
    assert dict(report.leaked) == {"secrets": 1}
    assert "vlr_live_x" not in str(report.summary())
