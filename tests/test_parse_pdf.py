"""Docling cache (a cached report is never converted again) and the header check, with
no Docling model and no PDF."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableCell, TableData

from reglens.ingestion.parse_pdf import (
    cache_paths,
    cached_conversion,
    from_docling,
    header_rows,
    is_cached,
    same_label_cells,
)

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


def _cell(r: int, c: int, text: str, span: int = 1, header: bool = True) -> TableCell:
    return TableCell(text=text, start_row_offset_idx=r, end_row_offset_idx=r + 1,
                     start_col_offset_idx=c, end_col_offset_idx=c + span, col_span=span,
                     column_header=header)  # fmt: skip


def _grid(labels: list[str]) -> list[list[TableCell]]:
    """Two years (each a span over 2 columns) above the given 4 column labels."""
    cells = [
        _cell(0, 1, "2025", span=2),
        _cell(0, 3, "2024", span=2),
        _cell(1, 0, "EUR m"),
        *[_cell(1, c, label) for c, label in enumerate(labels, start=1)],
        _cell(2, 0, "IRB", header=False),
        *[_cell(2, c, str(c), header=False) for c in range(1, 5)],
    ]
    return TableData(num_rows=3, num_cols=5, table_cells=cells).grid


def test_header_rows_ignore_empty_corner_cells() -> None:
    assert header_rows(_grid(["Total", "Collateral", "Gross", "Net"])) == 2  # empty (0, 0)


def test_same_label_in_two_header_cells_is_flagged() -> None:
    # a span repeated over its slots is one cell, so it does not count
    assert not same_label_cells(_grid(["Total", "Collateral", "Gross", "Net"]), 2)
    # "Total" once per year: two cells with one label (fires on correct tables too)
    assert same_label_cells(_grid(["Total", "Collateral", "Total", "Collateral"]), 2)
