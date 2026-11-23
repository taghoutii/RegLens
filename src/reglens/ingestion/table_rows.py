"""Report tables as self-describing rows, and their header rows.

1. Header rows. The leading rows
   without numbers (years allowed) are taken as the header, up to a configured maximum
   (`infer_header_rows`).

2. Row serialization. A table chunk cut into row groups loses the header rows that
   name its columns. Each data row is therefore also written as one line that carries
   its own labels:
       "<section> > <row label> — <column header path> = <value>; ..."
       
   The header path of a column joins its labels from every header row, top to bottom. 
    A row with a label and no value is a section: it prefixes the labels of the rows under it.
    The Markdown grid is kept for display; these lines are for embedding and the LLM.
"""

import re
from dataclasses import dataclass

from reglens.ingestion.models import TableData
from reglens.ingestion.numbers import is_year, parse_number
from reglens.ingestion.text_norm import clean

# A row number such as "1", "4a", "EU 7d", "5.1": part of a row's label, not a value.
ROW_REF = re.compile(r"^(?:EU[\s-]?)?\d{1,3}[a-z]?(?:\.\d{1,2})?$", re.IGNORECASE)


def header_like(row: list[str], langue: str) -> bool:
    """A row that can be a column header: text in a column other than the first (not
    just the first cell's text repeated by a span), and no number except years ("2025"
    is a header in many reports)."""
    first = clean(row[0])
    if not any(clean(x) and clean(x) != first for x in row[1:]):
        return False  # only a first-column label (maybe repeated by a span): a section
    for x in row:
        n = parse_number(clean(x), langue) if clean(x) else None
        if n is not None and not is_year(n):
            return False
    return True


def infer_header_rows(table: TableData, langue: str, max_rows: int) -> int:
    """Header rows of a table: Docling's when it detected some; otherwise the leading
    rows without numbers (years allowed), at most `max_rows`, and only if a row with
    numbers follows (a table of text only keeps 0)."""
    if table.header_rows:
        return table.header_rows
    n = 0
    while n < min(max_rows, table.n_rows - 1) and header_like(table.cells[n], langue):
        n += 1
    has_numbers = any(not header_like(row, langue) and any(clean(x) for x in row[1:])
                      for row in table.cells[n:])  # fmt: skip
    return n if has_numbers else 0


def with_header(table: TableData, langue: str, max_rows: int) -> TableData:
    """The table with its header rows set by `infer_header_rows` (a copy, flagged
    `header_inferred` when they were inferred)."""
    n = infer_header_rows(table, langue, max_rows)
    if n == table.header_rows:
        return table
    return table.model_copy(update={"header_rows": n, "header_inferred": True})


def header_paths(table: TableData) -> list[str]:
    """Full header path of every column ("" when the column has no label): its labels
    from all header rows, top to bottom, without repeating a label already in the path."""
    paths = []
    for c in range(table.n_cols):
        parts: list[str] = []
        for r in range(min(table.header_rows, len(table.cells))):
            row = table.cells[r]
            label = clean(row[c]) if c < len(row) else ""
            if label and label not in parts:
                parts.append(label)
        paths.append(" > ".join(parts))
    return paths


def label_columns(table: TableData, langue: str) -> int:
    """How many leading columns hold the row label: up to the last of the first three
    columns whose data cells are mostly text (a column of row numbers "1", "EU 1a"
    before a text column belongs to the label)."""
    data = table.cells[table.header_rows :]
    n = 0
    for c in range(min(3, table.n_cols - 1)):
        cells = [clean(row[c]) for row in data if clean(row[c])]
        if not cells:
            continue
        text = [x for x in cells if parse_number(x, langue) is None and not ROW_REF.match(x)]
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
        own = " ".join(dict.fromkeys(x for x in map(clean, cells[:k]) if x))
        values = {c: clean(cells[c]) for c in range(k, table.n_cols) if clean(cells[c])}
        if own and not values:
            section = own
            continue
        if not own and not values:
            continue
        label = f"{section} > {own}" if section and own else own or section
        rows.append(Row(r, label or f"row {r + 1}", own, values))
    return rows


def row_cells(row: Row, paths: list[str]) -> list[str]:
    """ "header path = value" for every value of a row ("column N" when unlabelled)."""
    return [f"{paths[c] or f'column {c + 1}'} = {v}" for c, v in sorted(row.values.items())]


def row_lines(table: TableData, langue: str) -> list[str]:
    """One self-describing line per data row: "label — path = value; path = value"."""
    paths = header_paths(table)
    return [
        f"{row.label} — " + "; ".join(row_cells(row, paths)) for row in data_rows(table, langue)
    ]
