"""Check of the "Total" rows of report tables (a diagnostic: it flags nothing).

A row labelled "Total ..." should equal the sum of the rows it totals. Which rows those
are is not marked in the grid, so several candidate sets are tried (the rows just above
it, back to the previous total or section, without "of which" rows; or the earlier total
rows) and the set that matches the most columns is kept. When that set matches at least
half of the total's numeric columns, the row is "checkable" and every other column is a
mismatch: a value in the wrong column, a lost value or a misread number. Tolerance: each
rounded value is off by at most half a unit of its last digit.

Measured on the corpus (results/2026-11-23_table_rows.json): the failures it finds are
mostly sign conventions and split tables, not extraction errors, so it is used only as a
diagnostic.
"""

import re
from dataclasses import dataclass, field

from reglens.ingestion.models import TableData
from reglens.ingestion.numbers import Number, parse_number
from reglens.ingestion.table_rows import ROW_REF, Row, data_rows

_OF_WHICH = re.compile(r"^(?:of which|o/w|dont|whereof)\b", re.IGNORECASE)


@dataclass
class TotalCheck:
    row: int  # grid row of the "Total" line
    label: str
    components: list[int]  # grid rows summed
    matched: list[int]  # columns where the sum equals the total
    mismatched: list[int]  # columns where it does not (only when checkable)
    checkable: bool


@dataclass
class TableTotals:
    table_id: str
    total_rows: int = 0
    checks: list[TotalCheck] = field(default_factory=list)

    @property
    def checkable(self) -> bool:
        return any(c.checkable for c in self.checks)

    @property
    def failed(self) -> bool:
        return any(c.checkable and c.mismatched for c in self.checks)


def _numbers(row: Row, langue: str) -> dict[int, Number]:
    out = {}
    for c, text in row.values.items():
        n = parse_number(text, langue)
        if n is not None and not n.percent:
            out[c] = n
    return out


def _is_total(row: Row) -> bool:
    words = [w for w in row.own_label.split() if not ROW_REF.match(w)]
    return bool(words) and words[0].lower().startswith("total")


def _compare(
    total: dict[int, Number], parts: list[dict[int, Number]]
) -> tuple[list[int], list[int]]:
    matched: list[int] = []
    mismatched: list[int] = []
    for c, t in total.items():
        values = [p[c] for p in parts if c in p]
        if not values:
            continue
        # each printed value is off by at most half a unit of its own last digit
        tolerance = 0.5 * sum(10.0**-n.decimals for n in [t, *values]) + 1e-9
        ok = abs(sum(v.value for v in values) - t.value) <= tolerance
        (matched if ok else mismatched).append(c)
    return matched, mismatched


def _children(rows: list[Row], numbers: list[dict[int, Number]], j: int, end: int) -> int:
    """If row j is the sum of the rows just below it (a subtotal such as "Loans and
    advances" over its counterparties, or "Europe" over its countries), return the end
    of those rows (exclusive); otherwise j + 1."""
    best_end, best_matched = j + 1, 0
    for k in range(j + 3, end + 1):  # at least 2 children
        members = [m for m in range(j + 1, k) if not _OF_WHICH.match(rows[m].own_label)]
        if len(members) < 2 or not numbers[j]:
            continue
        matched, mismatched = _compare(numbers[j], [numbers[m] for m in members])
        if len(matched) > best_matched and len(matched) > len(mismatched):
            best_end, best_matched = k, len(matched)
    return best_end


def _top_level(
    rows: list[Row], numbers: list[dict[int, Number]], start: int, end: int
) -> list[int]:
    """Rows of [start, end) that are not inside a detected subtotal (nor "of which")."""
    out, j = [], start
    while j < end:
        if not _OF_WHICH.match(rows[j].own_label):
            out.append(j)
        j = _children(rows, numbers, j, end)
    return out


def check_totals(table: TableData, langue: str) -> TableTotals:
    rows = data_rows(table, langue)
    result = TableTotals(table.table_id)
    numbers = [_numbers(r, langue) for r in rows]
    previous_totals: list[int] = []  # positions in `rows`
    block_start = 0
    for i, row in enumerate(rows):
        if not _is_total(row):
            continue
        result.total_rows += 1
        total = numbers[i]
        best: TotalCheck | None = None
        candidates = [_top_level(rows, numbers, block_start, i)]  # hierarchical tables
        for start in range(i - 1, block_start - 1, -1):  # rows just above, growing upwards
            members = [j for j in range(start, i) if not _OF_WHICH.match(rows[j].own_label)]
            candidates.append(members)
        if len(previous_totals) >= 2:
            candidates.append(list(previous_totals))
        for members in candidates:
            if len(members) < 2 or not total:
                continue
            matched, mismatched = _compare(total, [numbers[j] for j in members])
            if best is None or len(matched) > len(best.matched):
                best = TotalCheck(row.index, row.own_label, [rows[j].index for j in members],
                                  matched, mismatched, False)  # fmt: skip
        if best is not None and best.matched:
            compared = len(best.matched) + len(best.mismatched)
            best.checkable = len(best.matched) * 2 >= compared
            if not best.checkable:
                best.mismatched = []
            result.checks.append(best)
        previous_totals.append(i)
        block_start = i + 1
    return result
