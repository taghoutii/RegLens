"""Report tables as self-describing rows, and a check of their "Total" rows.

1. Row serialization. A table chunk cut into row groups loses the header rows that
   name its columns. Each data row is therefore also written as one line that carries
   its own labels:
       "<section> > <row label> — <column header path> = <value>; ..."
   The header path of a column joins its labels from every header row, top to bottom
   ("31 December 2025 > Gross carrying amount > of which: defaulted"). Spanning headers
   are already repeated in every column they cover in the parsed grid (Docling), so
   joining the rows is enough. A row with a label and no value ("Assets",
   "Cash-Outflows") is a section: it prefixes the labels of the rows under it.
   The Markdown grid is kept for display; these lines are for embedding and the LLM.

2. Total check. A row labelled "Total ..." should equal the sum of the rows it
   totals. Which rows those are is not marked in the grid, so several candidate
   sets are tried (the rows just above it, back to the previous total or section,
   without "of which" rows; or the earlier total rows) and the set that matches the
   most columns is kept. When that set matches at least half of the total's numeric
   columns, the row is "checkable" and every other column is a mismatch: a value in
   the wrong column, a lost value or a misread number. Tolerance: each rounded value
   is off by at most half a unit of its last digit.
"""

import re
from dataclasses import dataclass, field

from reglens.ingestion.models import TableData

# One number, as printed in the reports: 1,234.5 / 1 234,5 (FR) / (1,043) / 12.5% / -
_EN = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{3})+|\d{1,3}(?: \d{3})+|\d+)(?:\.(\d+))?\)?\s?%?$")
_FR = re.compile(r"^\(?-?(?:\d{1,3}(?: \d{3})+|\d+)(?:,(\d+))?\)?\s?%?$")
_NIL = {"-", "–", "—"}
_OF_WHICH = re.compile(r"^(?:of which|o/w|dont|whereof)\b", re.IGNORECASE)
_ROW_REF = re.compile(r"^(?:EU[\s-]?)?\d{1,3}[a-z]?(?:\.\d{1,2})?$", re.IGNORECASE)
_SPACES = re.compile(r"[\s  ]+")


@dataclass(frozen=True)
class Number:
    value: float
    decimals: int
    percent: bool


def parse_number(text: str, langue: str) -> Number | None:
    """The number printed in a cell, or None if the cell is not one number (text,
    empty, or several numbers fused together)."""
    s = _SPACES.sub(" ", text).strip()
    if s in _NIL:
        return Number(0.0, 0, False)
    m = (_FR if langue == "fr" else _EN).match(s)
    if not m:
        return None
    digits = s.replace(" ", "").replace("%", "").strip("()")
    if langue == "fr":
        digits = digits.replace(",", ".")
    else:
        digits = digits.replace(",", "")
    try:
        value = float(digits)
    except ValueError:
        return None
    if s.startswith("(") and s.endswith(")") or s.startswith("(") and s.endswith(")%"):
        value = -abs(value)
    return Number(value, len(m.group(1) or ""), s.endswith("%"))


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


MAX_INFERRED_HEADER_ROWS = 4


def _is_year(n: Number) -> bool:
    return n.decimals == 0 and not n.percent and 1990 <= n.value <= 2035


def _header_like(row: list[str], langue: str) -> bool:
    """A row that can be a column header: text in a column other than the first (not
    just the first cell's text repeated by a span), and no number except years ("2025"
    is a header in many reports)."""
    first = _clean(row[0])
    if not any(_clean(x) and _clean(x) != first for x in row[1:]):
        return False  # only a first-column label (maybe repeated by a span): a section
    for x in row:
        n = parse_number(_clean(x), langue) if _clean(x) else None
        if n is not None and not _is_year(n):
            return False
    return True


def infer_header_rows(table: TableData, langue: str) -> int:
    """Header rows of a table, inferred when Docling detected none: the leading rows
    without numbers (years allowed), at most MAX_INFERRED_HEADER_ROWS, and only if a row
    with numbers follows (a table of text only keeps 0). Detected headers are kept."""
    if table.header_rows:
        return table.header_rows
    n = 0
    while n < min(MAX_INFERRED_HEADER_ROWS, table.n_rows - 1) and _header_like(
        table.cells[n], langue
    ):
        n += 1
    has_numbers = any(not _header_like(row, langue) and any(_clean(x) for x in row[1:])
                      for row in table.cells[n:])  # fmt: skip
    return n if has_numbers else 0


def with_header(table: TableData, langue: str) -> TableData:
    """The table with inferred header rows (a copy; the stored table is unchanged)."""
    n = infer_header_rows(table, langue)
    return table if n == table.header_rows else table.model_copy(update={"header_rows": n})


def header_paths(table: TableData) -> list[str]:
    """Full header path of every column: its labels from all header rows, top to
    bottom, without repeating a label already in the path."""
    paths = []
    for c in range(table.n_cols):
        parts: list[str] = []
        for r in range(table.header_rows):
            label = _clean(table.cells[r][c])
            if label and label not in parts:
                parts.append(label)
        paths.append(" > ".join(parts) or f"column {c + 1}")
    return paths


def label_columns(table: TableData, langue: str) -> int:
    """How many leading columns hold the row label: up to the last of the first three
    columns whose data cells are mostly text (a column of row numbers "1", "EU 1a"
    before a text column belongs to the label)."""
    data = table.cells[table.header_rows :]
    n = 0
    for c in range(min(3, table.n_cols - 1)):
        cells = [_clean(row[c]) for row in data if _clean(row[c])]
        if not cells:
            continue
        text = [x for x in cells if parse_number(x, langue) is None and not _ROW_REF.match(x)]
        if len(text) * 2 >= len(cells):  # mostly words: a label column
            n = c + 1
    return max(n, 1)


@dataclass
class Row:
    index: int  # row index in the grid
    label: str  # section-prefixed label
    own_label: str  # the row's own label, without the section
    values: dict[int, str]  # column -> cell text, non-empty value cells only


def data_rows(table: TableData, langue: str) -> list[Row]:
    """Data rows with their labels; a row with a label and no value is a section and
    prefixes the labels of the rows that follow it."""
    k = label_columns(table, langue)
    rows, section = [], ""
    for r in range(table.header_rows, table.n_rows):
        cells = table.cells[r]
        own = " ".join(dict.fromkeys(x for x in map(_clean, cells[:k]) if x))
        values = {c: _clean(cells[c]) for c in range(k, table.n_cols) if _clean(cells[c])}
        if own and not values:
            section = own
            continue
        if not own and not values:
            continue
        label = f"{section} > {own}" if section and own else own or section
        rows.append(Row(r, label or f"row {r + 1}", own, values))
    return rows


def row_lines(table: TableData, langue: str) -> list[str]:
    """One self-describing line per data row: "label — path = value; path = value"."""
    paths = header_paths(table)
    lines = []
    for row in data_rows(table, langue):
        cells = "; ".join(f"{paths[c]} = {v}" for c, v in sorted(row.values.items()))
        lines.append(f"{row.label} — {cells}")
    return lines


# ---------- total check ----------


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
    words = [w for w in row.own_label.split() if not _ROW_REF.match(w)]
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
