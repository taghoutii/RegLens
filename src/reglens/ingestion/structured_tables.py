"""Replace report tables by their exact version from the bank's spreadsheet appendix.

Table ids never change, so the spreadsheet only *replaces the content* of a
table already extracted from the PDF, and only when the match is certain

The new content keeps the table id, caption and PDF page (citations still point to the
page), and records `content_source = "spreadsheet"`.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl

from reglens.corpus.sources import Language
from reglens.ingestion.models import ParsedDocument, TableData
from reglens.ingestion.numbers import Number, is_year, parse_number
from reglens.ingestion.table_rows import data_rows, header_like
from reglens.ingestion.text_norm import clean


@dataclass
class SheetBlock:
    sheet: str
    index: int  # n-th block of the sheet (1-based)
    first_row: int  # 1-based spreadsheet row
    rows: list[list[object]]  # cell values (dates as None: they are labels, not values)
    texts: list[list[str]] = field(default_factory=list)  # cells as displayed

    @property
    def name(self) -> str:
        return f"{self.sheet}/{self.index}"

    def numbers(self) -> list[float]:
        return [v for row in self.rows for v in _row_numbers(row)]


def _row_numbers(row: list[object]) -> list[float]:
    return [float(v) for v in row if isinstance(v, int | float) and not isinstance(v, bool)]


def display(value: object, number_format: str) -> str:
    """A cell as the spreadsheet shows it: its number format (decimals, thousands
    separator, percentage, brackets for negatives, "-" for zero) applied to the value."""
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return clean(str(value))
    sections = number_format.split(";")
    if number_format == "General":
        return f"{value:.10g}"
    section = sections[0]
    if value < 0 and len(sections) > 1:
        section = sections[1]
    elif value == 0 and len(sections) > 2:
        section = sections[2]
        if "0" not in section and "#" not in section:
            return "-"
    percent = "%" in section
    shown = abs(value) * (100 if percent else 1)
    decimals = 0
    if "." in section:
        after = section.split(".", 1)[1]
        decimals = len(after) - len(after.lstrip("0#"))
    text = f"{shown:,.{decimals}f}" if "," in section else f"{shown:.{decimals}f}"
    if percent:
        text += "%"
    if value < 0:
        brackets = len(sections) > 1 and "(" in sections[1]
        text = f"({text})" if brackets else f"-{text}"
    return text


def read_blocks(path: Path) -> list[SheetBlock]:
    """Every block of every sheet. A block ends at two empty rows in a row, or where a
    new "Template ..." title starts. Merged cells repeat their text in every slot they
    cover, as in the PDF tables."""
    workbook = openpyxl.load_workbook(path, data_only=True)
    blocks: list[SheetBlock] = []
    try:
        for sheet in workbook.worksheets:
            merged: dict[tuple[int, int], tuple[int, int]] = {}
            for rng in sheet.merged_cells.ranges:
                for r in range(rng.min_row, rng.max_row + 1):
                    for c in range(rng.min_col, rng.max_col + 1):
                        merged[r, c] = (rng.min_row, rng.min_col)
            current: list[list[object]] = []
            texts: list[list[str]] = []
            start, empty, n = 0, 0, 0
            for r, cells in enumerate(sheet.iter_rows(), start=1):
                row = [None if isinstance(c.value, datetime | date) else c.value for c in cells]
                shown = []
                for c, cell in enumerate(cells, start=1):
                    top = merged.get((r, c))
                    src = sheet.cell(*top) if top else cell
                    shown.append(display(src.value, src.number_format))
                filled = [x for x in shown if x]
                title = bool(filled) and filled[0].lower().startswith("template")
                if (title or empty >= 2) and any(_row_numbers(x) for x in current):
                    n += 1
                    blocks.append(SheetBlock(sheet.title, n, start, current, texts))
                    current, texts = [], []
                if not filled:
                    empty += 1
                    continue
                if not current:
                    start = r
                empty = 0
                current.append(row)
                texts.append(shown)
            if any(_row_numbers(x) for x in current):
                blocks.append(SheetBlock(sheet.title, n + 1, start, current, texts))
    finally:
        workbook.close()
    return blocks


def is_informative(n: Number | None) -> bool:
    """A value that identifies a table: non-zero, and not a bare small integer (row
    numbers, counts and years appear in many tables)."""
    if n is None or n.value == 0 or is_year(n):
        return False
    return n.percent or n.decimals > 0 or abs(n.value) >= 100


def _informative(texts: list[str], langue: str) -> list[Number]:
    return [
        n for n in (parse_number(t, langue) for t in texts) if n is not None and is_informative(n)
    ]


def informative_numbers(table: TableData, langue: str) -> list[Number]:
    """The informative values of a table's data rows."""
    return [
        n
        for row in data_rows(table, langue)
        for n in _informative(list(row.values.values()), langue)
    ]


Keys = set[tuple[int, bool, float]]


def printed_forms(values: list[float]) -> Keys:
    """Every way a spreadsheet value can be printed: rounded to 0-4 decimals, as is or
    as a percentage (stored as a fraction), sign dropped (the PDF may print -187 as
    187 under a "negative" header)."""
    keys: Keys = set()
    for v in values:
        for d in range(5):
            keys.add((d, False, round(abs(v), d)))
            keys.add((d, True, round(abs(v) * 100, d)))
    return keys


def _key(n: Number, percent: bool) -> tuple[int, bool, float]:
    d = min(n.decimals, 4)
    return d, percent, round(abs(n.value), d)


def _found(n: Number, keys: Keys) -> bool:
    return _key(n, True) in keys or _key(n, False) in keys


def block_match(numbers: list[Number], keys: Keys) -> float:
    """Share of the table's informative numbers found among a block's printed forms (a
    percentage column may print "13.1" without the % sign)."""
    if not numbers:
        return 0.0
    return sum(1 for n in numbers if _found(n, keys)) / len(numbers)


@dataclass
class TableMatch:
    table_id: str
    numbers: int  # informative numbers in the PDF table
    best: str | None  # "<sheet>/<block>"
    best_share: float
    second: str | None
    second_share: float
    certain: bool


def match_table(
    table: TableData,
    langue: str,
    blocks: list[SheetBlock],
    keys: dict[str, Keys],
    *,
    min_numbers: int,
    min_share: float,
    margin: float,
) -> TableMatch:
    """Best block for one table. Certain = at least `min_numbers` informative numbers,
    at least `min_share` of them in the best block, and the runner-up at least `margin`
    lower (otherwise two blocks, e.g. 2025 and 2024 of one template, are not told apart).
    `keys` holds printed_forms(block.numbers()) per block name, computed once."""
    numbers = informative_numbers(table, langue)
    scored = sorted(((block_match(numbers, keys[b.name]), b.name) for b in blocks), reverse=True)
    (s1, b1), (s2, b2) = scored[0], scored[1] if len(scored) > 1 else (0.0, None)
    certain = len(numbers) >= min_numbers and s1 >= min_share and s1 - s2 >= margin
    return TableMatch(table.table_id, len(numbers), b1 if s1 else None, round(s1, 3),
                      b2 if s2 else None, round(s2, 3), certain)  # fmt: skip


@dataclass
class RowAlignment:
    rows: int  # PDF data rows with at least one informative number
    aligned: int  # ... whose numbers all sit in one spreadsheet row of the block
    in_order: bool  # the aligned spreadsheet rows follow the PDF row order
    pairs: list[tuple[int, int]] = field(default_factory=list)  # (grid row, block row)

    @property
    def complete(self) -> bool:
        return self.rows > 0 and self.aligned == self.rows and self.in_order


def align_rows(table: TableData, langue: str, block: SheetBlock) -> RowAlignment:
    """Row-level check of a table-to-block match: every PDF row must find the one
    spreadsheet row that holds all of its informative numbers, in the same order."""
    forms = [printed_forms(_row_numbers(row)) for row in block.rows]
    out = RowAlignment(0, 0, True)
    last = -1
    for row in data_rows(table, langue):
        numbers = _informative(list(row.values.values()), langue)
        if not numbers:
            continue
        out.rows += 1
        hits = [i for i, keys in enumerate(forms) if all(_found(n, keys) for n in numbers)]
        if not hits:
            continue
        out.aligned += 1
        after = [i for i in hits if i > last]
        if not after:
            out.in_order = False
            continue
        last = after[0]
        out.pairs.append((row.index, last))
    return out


def _header_end(block: SheetBlock, langue: str) -> tuple[int, int]:
    """(first header row, first body row) of a block: leading "Template ..." title rows
    are skipped, then the header is the run of header-like rows (no number but years)."""
    start = 0
    while start < len(block.texts) and (
        next((x for x in block.texts[start] if x), "").lower().startswith("template")
    ):
        start += 1
    end = start
    while end < len(block.texts) and header_like(block.texts[end], langue):
        end += 1
    return start, end


def spreadsheet_table(
    table: TableData, langue: str, block: SheetBlock, alignment: RowAlignment
) -> TableData | None:
    """The PDF table rebuilt from its spreadsheet block: the block's header rows, then the
    block rows the table covers. The covered rows are the aligned ones, widened by the
    PDF rows before the first and after the last aligned row. Empty columns are dropped."""
    if not alignment.pairs:
        return None
    h_start, h_end = _header_end(block, langue)
    grid_rows = [r.index for r in data_rows(table, langue)]
    (g_first, b_first), (g_last, b_last) = alignment.pairs[0], alignment.pairs[-1]
    first_data = min(grid_rows) if grid_rows else g_first
    body_start = max(h_end, b_first - (g_first - first_data))
    body_end = min(len(block.texts) - 1, b_last + (table.n_rows - 1 - g_last))
    rows = block.texts[h_start:h_end] + block.texts[body_start : body_end + 1]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [c for c in range(width) if any(r[c] for r in rows)]
    cells = [[r[c] for c in keep] for r in rows]
    if len(keep) < 2:
        return None
    return table.model_copy(update={
        "cells": cells, "n_rows": len(cells), "n_cols": len(keep),
        "header_rows": h_end - h_start, "header_inferred": False,
        "low_confidence": [], "header_issues": [], "content_source": "spreadsheet",
    })  # fmt: skip


def same_numbers(pdf: TableData, new: TableData, langue: str) -> bool:
    """True if every informative number of the PDF table is printed in the new table
    (same value and decimals), at least as many times. The % sign is not compared: the
    PDF often prints "0.84" under a "(%)" header where the spreadsheet shows "0.84%"."""

    def counts(t: TableData) -> Counter[tuple[float, int]]:
        return Counter((abs(n.value), n.decimals) for n in informative_numbers(t, langue))

    return not (counts(pdf) - counts(new))


@dataclass
class Replacement:
    table_id: str
    match: TableMatch
    rows_aligned: str  # "aligned/rows"
    replaced: bool
    reason: str


def apply_spreadsheet(
    doc: ParsedDocument, xlsx: Path, *, min_numbers: int, min_share: float, margin: float
) -> list[Replacement]:
    """Replace, in place, the tables of `doc` that its spreadsheet matches with certainty.
    Runs before masking, like every parsing step."""
    blocks = read_blocks(xlsx)
    by_name = {b.name: b for b in blocks}
    keys = {b.name: printed_forms(b.numbers()) for b in blocks}
    langue: Language = doc.langue
    out = []
    for b in doc.blocks:
        table = b.table
        if table is None or not table.cells:
            continue
        m = match_table(table, langue, blocks, keys,
                        min_numbers=min_numbers, min_share=min_share, margin=margin)  # fmt: skip
        if not m.certain or m.best is None:
            out.append(Replacement(table.table_id, m, "", False, "no certain block"))
            continue
        alignment = align_rows(table, langue, by_name[m.best])
        aligned = f"{alignment.aligned}/{alignment.rows}"
        if not alignment.complete:
            out.append(Replacement(table.table_id, m, aligned, False, "rows not aligned"))
            continue
        new = spreadsheet_table(table, langue, by_name[m.best], alignment)
        if new is None or not same_numbers(table, new, langue):
            out.append(Replacement(table.table_id, m, aligned, False, "values differ"))
            continue
        b.table = new
        out.append(Replacement(table.table_id, m, aligned, True, "replaced"))
    return out


def summary(replacements: list[Replacement]) -> dict[str, Any]:
    return dict(Counter(r.reason for r in replacements))
