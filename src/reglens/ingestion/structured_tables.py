"""Match the tables of a report's spreadsheet appendix to the tables Docling extracted.

Some banks publish their Pillar 3 templates twice: in the PDF and in an .xlsx file.
The spreadsheet holds exact values and explicit headers, but it is organised by template
(one sheet per EBA template, often with several blocks: 2025, 2024), not by the corpus's table ids.
Table ids never change, so the spreadsheet can only *replace the content* of a table
already extracted from the PDF, and only when the match is certain.

Matching uses the numbers themselves: a Docling table maps to the spreadsheet block that
contains (almost) all of its informative numbers, once each spreadsheet value is rounded
the way the PDF prints it. A table's template code ("EU CQ1") is a hint, never enough.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import openpyxl

from reglens.ingestion.models import TableData
from reglens.ingestion.table_rows import Number, data_rows, parse_number


@dataclass
class SheetBlock:
    sheet: str
    index: int  # n-th block of the sheet (1-based)
    first_row: int  # 1-based spreadsheet row
    rows: list[list[object]]

    @property
    def name(self) -> str:
        return f"{self.sheet}/{self.index}"

    def numbers(self) -> list[float]:
        return [
            float(v)
            for row in self.rows
            for v in row
            if isinstance(v, int | float) and not isinstance(v, bool)
        ]


def read_blocks(path: Path) -> list[SheetBlock]:
    """Every block of every sheet. A block ends at two empty rows in a row, or where a
    new "Template ..." title starts."""
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    blocks: list[SheetBlock] = []
    try:
        for sheet in workbook.worksheets:
            current: list[list[object]] = []
            start, empty, n = 0, 0, 0
            for r, raw in enumerate(sheet.iter_rows(values_only=True), start=1):
                row = [None if isinstance(v, datetime) else v for v in raw]
                filled = [v for v in row if v is not None and str(v).strip()]
                title = bool(filled) and str(filled[0]).strip().lower().startswith("template")
                if (title or empty >= 2) and any(_has_number(x) for x in current):
                    n += 1
                    blocks.append(SheetBlock(sheet.title, n, start, current))
                    current = []
                if not filled:
                    empty += 1
                    continue
                if not current:
                    start = r
                empty = 0
                current.append(row)
            if any(_has_number(x) for x in current):
                blocks.append(SheetBlock(sheet.title, n + 1, start, current))
    finally:
        workbook.close()
    return blocks


def _has_number(row: Iterable[object]) -> bool:
    return any(isinstance(v, int | float) and not isinstance(v, bool) and v for v in row)


def informative_numbers(table: TableData, langue: str) -> list[Number]:
    """Value cells that identify a table: non-zero, and not a bare small integer (row
    numbers, counts and years appear in many tables)."""
    out = []
    for row in data_rows(table, langue):
        for text in row.values.values():
            n = parse_number(text, langue)
            if n is None or n.value == 0:
                continue
            if n.decimals == 0 and not n.percent and abs(n.value) < 100:
                continue
            if not n.percent and n.decimals == 0 and 1990 <= n.value <= 2030:
                continue  # a year
            out.append(n)
    return out


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


def block_match(numbers: list[Number], keys: Keys) -> float:
    """Share of the table's informative numbers found among a block's printed forms (a
    percentage column may print "13.1" without the % sign)."""
    if not numbers:
        return 0.0
    found = sum(1 for n in numbers if _key(n, True) in keys or _key(n, False) in keys)
    return found / len(numbers)


@dataclass
class TableMatch:
    table_id: str
    numbers: int  # informative numbers in the Docling table
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
    min_numbers: int = 5,
    min_share: float = 0.95,
    margin: float = 0.2,
) -> TableMatch:
    """Best block for one table. Certain = at least `min_numbers` informative numbers,
    at least `min_share` of them in the best block, and the runner-up at least `margin`
    lower (otherwise two blocks, e.g. 2025 and 2024 of one template, are not told apart).
    `keys` holds printed_forms(block.numbers()) per block name, computed once."""
    numbers = informative_numbers(table, langue)
    scored = sorted(((block_match(numbers, keys[b.name]), b.name) for b in blocks), reverse=True)
    (s1, b1), (s2, b2) = scored[0], scored[1] if len(scored) > 1 else (0.0, None)
    certain = len(numbers) >= min_numbers and s1 >= min_share and s1 - s2 >= margin
    return TableMatch(
        table.table_id,
        len(numbers),
        b1 if s1 else None,
        round(s1, 3),
        b2 if s2 else None,
        round(s2, 3),
        certain,
    )


@dataclass
class RowAlignment:
    rows: int  # Docling data rows with at least one informative number
    aligned: int  # ... whose numbers all sit in one spreadsheet row of the block
    in_order: bool  # the aligned spreadsheet rows follow the Docling row order

    @property
    def complete(self) -> bool:
        return self.rows > 0 and self.aligned == self.rows and self.in_order


def align_rows(table: TableData, langue: str, block: SheetBlock) -> RowAlignment:
    """Row-level check of a table-to-block match: every Docling row must find the one
    spreadsheet row that holds all of its informative numbers, in the same order. This is
    what replacing the table's values from the spreadsheet would need."""
    forms = [
        printed_forms(
            [float(v) for v in row if isinstance(v, int | float) and not isinstance(v, bool)]
        )
        for row in block.rows
    ]
    rows = aligned = 0
    last, in_order = -1, True
    for row in data_rows(table, langue):
        numbers = [
            n
            for n in (parse_number(t, langue) for t in row.values.values())
            if n is not None and n.value and (abs(n.value) >= 100 or n.decimals)
        ]
        if not numbers:
            continue
        rows += 1
        hits = [
            i
            for i, keys in enumerate(forms)
            if all(_key(n, True) in keys or _key(n, False) in keys for n in numbers)
        ]
        if not hits:
            continue
        aligned += 1
        after = [i for i in hits if i > last]
        if not after:
            in_order = False
            continue
        last = after[0]
    return RowAlignment(rows, aligned, in_order)
