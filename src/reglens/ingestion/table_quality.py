"""Header checks on Docling tables

Docling's grid already repeats a spanning header in
every slot it covers, so each column carries its full header path. What goes wrong is
the span itself (too wide, or a spanning header fused into one column's text). These
checks flag such tables; they never change the cells.
"""

from collections import defaultdict

from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.table.table_data import TableCell

Grid = list[list[TableCell]]


def header_rows(grid: Grid) -> int:
    """Leading rows in which every non-empty cell is a column header (empty corners are
    ignored: a header row often starts with a blank cell above the row labels)."""
    n = 0
    for row in grid:
        filled = [c for c in row if c.text.strip()]
        if filled and all(c.column_header for c in filled):
            n += 1
        else:
            break
    return n


def _labels(row: list[TableCell]) -> dict[str, list[TableCell]]:
    found: dict[str, list[TableCell]] = defaultdict(list)
    for cell in row:
        if cell.text.strip():
            found[" ".join(cell.text.split())].append(cell)
    return found


def same_label_slots(grid: Grid, n_header: int) -> bool:
    return any(len(slots) > 1 for row in grid[:n_header] for slots in _labels(row).values())


def same_label_cells(grid: Grid, n_header: int) -> bool:
    for row in grid[:n_header]:
        for slots in _labels(row).values():
            if len({id(c) for c in slots}) > 1:
                return True
    return False


def label_repeated_under_span(grid: Grid, n_header: int) -> bool:
    for r in range(min(n_header, len(grid)) - 1):
        seen: set[int] = set()
        for cell in grid[r]:
            if cell.col_span < 2 or id(cell) in seen or not cell.text.strip():
                continue
            seen.add(id(cell))
            below = grid[r + 1][cell.start_col_offset_idx : cell.end_col_offset_idx]
            if any(len({id(c) for c in cells}) > 1 for cells in _labels(below).values()):
                return True
    return False


def header_issues(item: TableItem) -> list[str]:
    """Reasons to distrust the header (empty list = no warning)."""
    grid = item.data.grid
    n = header_rows(grid)
    return ["same_label_cells"] if same_label_cells(grid, n) else []
