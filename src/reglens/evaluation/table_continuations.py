"""Candidate tables continued across a page break (measurement only, nothing is merged).

A pair (A, B) is a candidate when:
  1. B is the next content item after A in reading order: only furniture (page header or
     footer, footnote, caption) lies between them, so A ends page N and B starts page N+1;
  2. B is on the page right after A;
  3. A and B have the same number of columns;
  4. B has no caption, or a caption that says it is continued ("continued", "suite", ...).
Optional filter (measured separately): drop the pair when A's last row is a "Total" row,
the pattern of every false positive in the first visual review (A complete, B another
table with the same layout).

The rules work on a flat list of `Item` records, so they are unit-tested without Docling.
"""

import re
from dataclasses import dataclass

from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.node import DocItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.labels import DocItemLabel

# Items that do not interrupt a table running onto the next page.
TRANSPARENT = {"page_header", "page_footer", "footnote", "caption"}
CONTINUED = re.compile(r"\b(?:continued|cont'd|cont\.|suite|\(suite\))", re.IGNORECASE)


@dataclass(frozen=True)
class Item:
    label: str  # Docling label: "table", "text", "section_header", "footnote", ...
    page: int
    table_index: int | None = None  # 1-based, as in "<doc_id>#tbl-<n>"; None for non-tables
    n_cols: int = 0
    caption: str | None = None
    top: float = 0.0  # distance from the top of the page, as a share of its height
    bottom: float = 0.0  # distance of the box's lower edge from the top, same unit
    ends_with_total: bool = False  # last non-empty row starts with "total" (tables only)


@dataclass(frozen=True)
class Candidate:
    first: int  # table index of A
    second: int  # table index of B
    page: int  # page of A (B is on page + 1)
    n_cols: int
    caption_says_continued: bool
    gap_below_first: float  # share of page N left under A
    gap_above_second: float  # share of page N+1 above B
    first_ends_with_total: bool = False


@dataclass(frozen=True)
class PairCheck:
    """Why an adjacent table pair on consecutive pages is or is not a candidate."""

    first: int
    second: int
    same_cols: bool
    cols_within_one: bool
    caption_ok: bool


def adjacent_pairs(items: list[Item]) -> list[tuple[Item, Item]]:
    """Table A followed by table B on the next page, with only furniture between them."""
    pairs = []
    last_table: Item | None = None
    for item in items:
        if item.label == "table":
            if last_table is not None and item.page == last_table.page + 1:
                pairs.append((last_table, item))
            last_table = item
        elif item.label not in TRANSPARENT:
            last_table = None  # real content between: B would not continue A
    return pairs


def check_pair(a: Item, b: Item) -> PairCheck:
    assert a.table_index is not None and b.table_index is not None
    caption_ok = not b.caption or bool(CONTINUED.search(b.caption))
    return PairCheck(a.table_index, b.table_index, a.n_cols == b.n_cols,
                     abs(a.n_cols - b.n_cols) <= 1, caption_ok)  # fmt: skip


def candidates(items: list[Item]) -> list[Candidate]:
    out = []
    for a, b in adjacent_pairs(items):
        check = check_pair(a, b)
        if check.same_cols and check.caption_ok:
            out.append(Candidate(check.first, check.second, a.page, a.n_cols,
                                 bool(b.caption and CONTINUED.search(b.caption)),
                                 round(1 - a.bottom, 3), round(b.top, 3),
                                 a.ends_with_total))  # fmt: skip
    return out


def last_row_is_total(rows: list[list[str]]) -> bool:
    """True when the first non-empty cell of the last non-empty row starts with "total"
    ("Total", "TOTAL LIABILITIES", "Total général")."""
    for row in reversed(rows):
        label = next((cell.strip() for cell in row if cell.strip()), "")
        if label:
            return label.lower().startswith("total")
    return False


def items_of(doc: DoclingDocument) -> list[Item]:
    """Body items in reading order, tables numbered like parse_pdf.from_docling."""
    items, n = [], 0
    for node, _level in doc.iterate_items():
        if not isinstance(node, DocItem) or not node.prov:
            continue
        prov = node.prov[0]
        height = doc.pages[prov.page_no].size.height
        box = prov.bbox.to_top_left_origin(height)
        top, bottom = box.t / height, box.b / height
        if isinstance(node, TableItem):
            n += 1
            rows = [[cell.text for cell in row] for row in node.data.grid]
            items.append(Item("table", prov.page_no, n, node.data.num_cols,
                              node.caption_text(doc) or None, top, bottom,
                              last_row_is_total(rows)))  # fmt: skip
        else:
            label = node.label.value if isinstance(node.label, DocItemLabel) else str(node.label)
            items.append(Item(label, prov.page_no, top=top, bottom=bottom))
    return items
