"""Check Docling's tables against the PDF itself.

Two complementary checks:
  1. values (automatic): every non-empty cell Docling extracted must appear in the PDF's
     own text layer, inside the table's bounding box on that page. Catches merged,
     truncated or invented values. It does not prove a value sits in the right row/column.
  2. structure (manual): a review page shows, side by side, the PDF region rendered as an
     image and the extracted grid, so a person can check rows and columns.

Born-digital PDFs carry their text, so the text layer is an independent reference: Docling
uses it too, but the check reads it back through a different library (pypdfium2).
"""

import html
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table import TableItem

from reglens.ingestion.numbers import has_several_numbers, is_one_number
from reglens.ingestion.parse_pdf import table_data
from reglens.ingestion.table_quality import (
    header_rows,
    label_repeated_under_span,
    same_label_cells,
    same_label_slots,
)
from reglens.ingestion.text_norm import compact


@dataclass
class TableCheck:
    doc_id: str
    table_index: int  # n-th table of the document (1-based, matches "#tbl-<n>")
    pages: list[int]
    n_rows: int
    n_cols: int
    cells: int = 0  # non-empty cells checked
    found: int = 0
    missing: list[tuple[int, int]] = field(default_factory=list)  # (row, col) positions
    cells_by_kind: Counter[str] = field(default_factory=Counter)  # header | label | number
    missing_by_kind: Counter[str] = field(default_factory=Counter)
    flagged: int = 0  # cells flagged low-confidence (several numbers in one cell)
    missing_flagged: int = 0  # missing cells that the flag caught
    # Completeness (the reverse check): words of the PDF table region found in the cells.
    # A variant that drops text scores well on `found` but badly here.
    region_words: int = 0
    # Header checks (ingestion/table_quality.py) and spanning headers.
    header_rows: int = 0
    spanning_headers: int = 0  # header cells covering several columns
    spans_repeated: int = 0  # ... whose text is in every slot of the stored grid
    same_label_slots: bool = False
    same_label_cells: bool = False  # = the stored "header uncertain" flag
    label_repeated_under_span: bool = False
    covered_words: int = 0


def cell_kind(text: str, column_header: bool) -> str:
    """header (column header), number (only digits and signs) or label (other text)."""
    if column_header:
        return "header"
    return "number" if is_one_number(text) or has_several_numbers(text) else "label"


def tables_of(doc: DoclingDocument) -> list[TableItem]:
    """Tables in reading order, numbered like parse_pdf.from_docling does."""
    return [item for item, _ in doc.iterate_items() if isinstance(item, TableItem)]


def region_text(pdf: pdfium.PdfDocument, doc: DoclingDocument, table: TableItem) -> str:
    """Text of the PDF text layer inside the table's box(es), all pages it spans."""
    parts = []
    for prov in table.prov:
        page = pdf[prov.page_no - 1]
        height = doc.pages[prov.page_no].size.height
        box = prov.bbox.to_bottom_left_origin(height).expand_by_scale(0.02, 0.02)
        parts.append(page.get_textpage().get_text_bounded(box.l, box.b, box.r, box.t))
    return "".join(parts)


def check_table(
    pdf: pdfium.PdfDocument, doc: DoclingDocument, table: TableItem, doc_id: str, index: int
) -> TableCheck:
    region = region_text(pdf, doc, table)
    reference = compact(region)
    result = TableCheck(doc_id, index, sorted({p.page_no for p in table.prov}),
                        table.data.num_rows, table.data.num_cols)  # fmt: skip
    seen: set[int] = set()  # a merged cell appears in every slot it covers: check it once
    for r, row in enumerate(table.data.grid):
        for c, cell in enumerate(row):
            text = compact(cell.text)
            if not text or id(cell) in seen:
                continue
            seen.add(id(cell))
            kind = cell_kind(cell.text, cell.column_header)
            result.cells += 1
            result.cells_by_kind[kind] += 1
            flag = not cell.column_header and has_several_numbers(cell.text)
            result.flagged += flag
            if text in reference:
                result.found += 1
            else:
                result.missing.append((r, c))
                result.missing_by_kind[kind] += 1
                result.missing_flagged += flag
    extracted = compact("".join(cell.text for cell in table.data.table_cells))
    words = [w for w in (compact(w) for w in region.split()) if w]
    result.region_words = len(words)
    result.covered_words = sum(1 for w in words if w in extracted)

    grid = table.data.grid
    stored = table_data(table, "check", None).cells  # what PostgreSQL receives
    n = header_rows(grid)
    result.header_rows = n
    spans = {id(c): c for row in grid[:n] for c in row if c.col_span > 1 and c.text.strip()}
    result.spanning_headers = len(spans)
    result.spans_repeated = sum(
        all(stored[c.start_row_offset_idx][j] == c.text.strip()
            for j in range(c.start_col_offset_idx, c.end_col_offset_idx))
        for c in spans.values()
    )  # fmt: skip
    result.same_label_slots = same_label_slots(grid, n)
    result.same_label_cells = same_label_cells(grid, n)
    result.label_repeated_under_span = label_repeated_under_span(grid, n)
    return result


def pick_tables(
    docs: dict[str, DoclingDocument], n: int, seed: int, min_rows: int = 3, min_cols: int = 2
) -> list[tuple[str, int, TableItem]]:
    """A reproducible random sample of real tables (not 1-row layout boxes), spread over
    the documents: shuffled per document, then taken in turn from each one."""
    rng = random.Random(seed)
    per_doc: list[list[tuple[str, int, TableItem]]] = []
    for doc_id, doc in sorted(docs.items()):
        tables = [
            (doc_id, i, t)
            for i, t in enumerate(tables_of(doc), start=1)
            if t.data.num_rows >= min_rows and t.data.num_cols >= min_cols
        ]
        rng.shuffle(tables)
        per_doc.append(tables)
    picked: list[tuple[str, int, TableItem]] = []
    while len(picked) < n and any(per_doc):
        for tables in per_doc:
            if tables and len(picked) < n:
                picked.append(tables.pop(0))
    return picked


def matching_table(table: TableItem, doc: DoclingDocument) -> TableItem | None:
    """The table of `doc` on the same first page that overlaps `table` the most."""
    page, box = table.prov[0].page_no, table.prov[0].bbox
    best, best_iou = None, 0.0
    for other in tables_of(doc):
        if other.prov and other.prov[0].page_no == page:
            iou = box.intersection_over_union(other.prov[0].bbox)
            if iou > best_iou:
                best, best_iou = other, iou
    return best if best_iou >= 0.5 else None


def render_region(
    pdf: pdfium.PdfDocument, doc: DoclingDocument, table: TableItem, out_png: Path
) -> None:
    """PNG of the table's region on its first page (scale 2 = 144 dpi)."""
    prov = table.prov[0]
    page = pdf[prov.page_no - 1]
    height = doc.pages[prov.page_no].size.height
    box = prov.bbox.to_top_left_origin(height).expand_by_scale(0.03, 0.03)
    scale = 2
    image = page.render(scale=scale).to_pil()
    image.crop((box.l * scale, box.t * scale, box.r * scale, box.b * scale)).save(out_png)


def grid_html(table: TableItem) -> str:
    """Extracted grid with row/column numbers, header cells in bold."""
    rows = ["<tr><th></th>" + "".join(f"<th>c{c}</th>" for c in range(table.data.num_cols))]
    for r, row in enumerate(table.data.grid):
        cells = "".join(
            f"<td{' class=h' if cell.column_header else ''}>{html.escape(cell.text)}</td>"
            for cell in row
        )
        rows.append(f"<tr><th>r{r}</th>{cells}</tr>")
    return "<table class=grid>" + "\n".join(rows) + "</table>"


def summarize(checks: list[TableCheck]) -> dict[str, object]:
    """Totals over checked tables: cell rate, misses per cell kind, low-confidence flags."""
    cells = sum(c.cells for c in checks)
    found = sum(c.found for c in checks)
    missing = cells - found
    return {
        "tables": len(checks),
        "tables_with_a_miss": sum(1 for c in checks if c.missing),
        "cells": cells,
        "found": found,
        "rate": round(found / cells, 4) if cells else None,
        "by_kind": {
            k: {
                "cells": sum(c.cells_by_kind[k] for c in checks),
                "missing": sum(c.missing_by_kind[k] for c in checks),
            }
            for k in ("header", "label", "number")
        },  # fmt: skip
        "coverage": _ratio(
            sum(c.covered_words for c in checks), sum(c.region_words for c in checks)
        ),  # fmt: skip
        "flagged_low_confidence": sum(c.flagged for c in checks),
        "tables_with_fused_rows": sum(1 for c in checks if c.flagged),
        "spanning_headers": sum(c.spanning_headers for c in checks),
        "spanning_headers_repeated": sum(c.spans_repeated for c in checks),
        "header_uncertain (same_label_cells, stored)": sum(c.same_label_cells for c in checks),
        "header_rule_same_label_slots (measured only)": sum(c.same_label_slots for c in checks),
        "header_rule_label_repeated_under_span (measured only)": sum(
            c.label_repeated_under_span for c in checks
        ),
        "missing_caught_by_flag": f"{sum(c.missing_flagged for c in checks)}/{missing}",
    }


def table_row(c: TableCheck) -> dict[str, object]:
    return {
        "table": f"{c.doc_id}#tbl-{c.table_index}",
        "pages": c.pages,
        "shape": [c.n_rows, c.n_cols],
        "cells": c.cells,
        "found": c.found,
        "missing_by_kind": dict(c.missing_by_kind),
        "flagged": c.flagged,
        "header_rows": c.header_rows,
        "spanning_headers": f"{c.spans_repeated}/{c.spanning_headers} repeated",
        "same_label_slots": c.same_label_slots,
        "same_label_cells": c.same_label_cells,
        "header_uncertain": c.same_label_cells,
        "label_repeated_under_span": c.label_repeated_under_span,
        "coverage": _ratio(c.covered_words, c.region_words),
        "missing_positions": c.missing,
    }


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None
