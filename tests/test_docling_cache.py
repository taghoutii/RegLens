"""Docling cache: a cached report is never converted again (no Docling model needed)."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from docling_core.types.doc.document import DoclingDocument

from reglens.ingestion.numbers import has_several_numbers
from reglens.ingestion.parse_pdf import cache_paths, cached_conversion, from_docling, is_cached
from reglens.ingestion.text_norm import compact

CONFIG: dict[str, object] = {"do_ocr": False, "table_mode": "accurate", "do_cell_matching": True}


class FakeConverter:
    """Stands in for Docling's DocumentConverter and counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def convert(self, path: Path, **_: Any) -> SimpleNamespace:
        self.calls += 1
        doc = DoclingDocument(name="report")
        doc.add_text(label="text", text="Own funds requirements")  # type: ignore[arg-type]
        return SimpleNamespace(document=doc)


def test_second_run_reads_the_cache(tmp_path: Path) -> None:
    converter = FakeConverter()
    cache = tmp_path / "cache"
    first, _ = cached_conversion(tmp_path / "r.pdf", converter, cache, "rep", CONFIG)  # type: ignore[arg-type]
    second, _ = cached_conversion(tmp_path / "r.pdf", converter, cache, "rep", CONFIG)  # type: ignore[arg-type]
    assert converter.calls == 1
    assert second.export_to_dict() == first.export_to_dict()
    doc_file, meta_file = cache_paths(cache, "rep")
    assert doc_file.exists() and meta_file.exists()
    assert not list(cache.glob("*.part"))  # no half-written file left behind


def test_other_settings_convert_again(tmp_path: Path) -> None:
    converter = FakeConverter()
    cached_conversion(tmp_path / "r.pdf", converter, tmp_path, "rep", CONFIG)  # type: ignore[arg-type]
    other = {**CONFIG, "do_cell_matching": False}
    assert not is_cached(tmp_path, "rep", other)
    cached_conversion(tmp_path / "r.pdf", converter, tmp_path, "rep", other)  # type: ignore[arg-type]
    assert converter.calls == 2 and is_cached(tmp_path, "rep", other)


def test_cached_document_maps_to_blocks(tmp_path: Path) -> None:
    doc, _ = cached_conversion(tmp_path / "r.pdf", FakeConverter(), tmp_path, "rep", CONFIG)  # type: ignore[arg-type]
    parsed = from_docling(
        doc, doc_id="rep", langue="en", source="data/raw/reports/r.pdf", max_header_rows=4
    )
    assert [b.text for b in parsed.blocks] == ["Own funds requirements"]
    assert parsed.blocks[0].parent_id == "rep#sec-0" and not parsed.masked


def test_compact_ignores_whitespace_and_dash_variants() -> None:
    assert compact("1 234\n567 −12") == compact("1234567 -12") == "1234567-12"


def test_fused_numbers_are_flagged() -> None:
    for text in ["1,571 898 92", "33 34", "0.6% 0.4% 0.0%", "0,50% 1,00%"]:
        assert has_several_numbers(text), text
    for text in ["39,306", "(1,043)", "12,9 %", "1 234 567", "2.5", "-", "Total 767", "EU-7"]:
        assert not has_several_numbers(text), text
