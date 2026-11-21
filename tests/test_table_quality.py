"""Header checks on small hand-built Docling tables (no PDF, no model)."""

from docling_core.types.doc.items.table.table_data import TableCell, TableData

from reglens.ingestion.table_quality import (
    header_rows,
    label_repeated_under_span,
    same_label_cells,
    same_label_slots,
)


def _cell(r: int, c: int, text: str, span: int = 1, header: bool = True) -> TableCell:
    return TableCell(text=text, start_row_offset_idx=r, end_row_offset_idx=r + 1,
                     start_col_offset_idx=c, end_col_offset_idx=c + span, col_span=span,
                     column_header=header)  # fmt: skip


def _grid(year_span: int) -> list[list[TableCell]]:
    """Two years x (Total, Collateral); the first year spans `year_span` columns."""
    cells = [
        _cell(0, 1, "2025", span=year_span),
        _cell(0, 1 + year_span, "2024", span=4 - year_span),
        _cell(1, 0, "EUR m"),
        *[_cell(1, c, label) for c, label in
          enumerate(["Total", "Collateral", "Total", "Collateral"], start=1)],
        _cell(2, 0, "IRB", header=False),
        *[_cell(2, c, str(c), header=False) for c in range(1, 5)],
    ]  # fmt: skip
    return TableData(num_rows=3, num_cols=5, table_cells=cells).grid


def test_header_rows_ignore_empty_corner_cells() -> None:
    assert header_rows(_grid(2)) == 2  # row 0 has an empty cell at column 0


def test_correct_spans_are_not_flagged() -> None:
    grid = _grid(2)
    assert not label_repeated_under_span(grid, 2)
    # Both "same label in one header row" rules fire on this *correct* table: the span
    # repeats "2025" over its slots, and "Total" exists once per year.
    assert same_label_slots(grid, 2) and same_label_cells(grid, 2)


def test_span_too_wide_is_flagged() -> None:
    grid = _grid(3)  # "2025" also covers the 2024 "Total" column (as in the BNP table)
    assert label_repeated_under_span(grid, 2)
    assert grid[0][3].text == "2025"  # the span is repeated in every slot it covers
