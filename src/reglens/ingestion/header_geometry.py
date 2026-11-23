"""Rebuild a table's column headers from the PDF's geometry.

Docling's TableFormer predicts header spans from the page image and sometimes gets them
wrong: a year header covering one column too many, a spanning header fused into one
column's text, a missing spanning header. The PDF itself says where each header label
is (text positions) and, in most bank reports, how far it spans: a filled box behind it
or a rule drawn under it.

Method, for a table on one page:
1. Columns: the x-extent of each grid column, from Docling's body cell boxes.
2. Header region: from the table's top to the first body row (the first row with a
   value that is a number or "-"; small integers such as column numbers stay header).
3. Labels: the PDF characters in that region (pypdfium2), grouped into lines, phrases
   (split at wide gaps) and multi-line labels (lines stacked closely, no rule between).
4. Span of each label, in order of preference:
   a. the smallest filled box that contains the label and no other label;
   b. the rule just under the label, when no other label of the same line sits over it;
   c. otherwise the columns the label's own text overlaps.
5. Each column's header path is the labels whose span covers its centre, top to bottom.
Body rows are kept exactly as Docling extracted them; only the header rows are replaced.
When a step fails (multi-page table, missing boxes), the function returns None and the
caller keeps Docling's header.
"""

import ctypes
from dataclasses import dataclass, field

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from docling_core.types.doc.base import CoordOrigin
from docling_core.types.doc.items.table.table import TableItem

from reglens.ingestion.models import TableData
from reglens.ingestion.table_rows import label_columns, parse_number

PHRASE_GAP = 0.8  # gap between characters, in line heights, that starts a new phrase
WORD_GAP = 0.15  # gap that inserts a space
LINE_MERGE_GAP = 0.5  # vertical gap, in line heights, between lines of one label
RULE_DISTANCE = 1.5  # how far under a label (in line heights) its rule may be
BACKGROUND = 0.95  # a box wider than this share of the table is a background
_SOFT_HYPHENS = str.maketrans({"\x02": "-", "­": "-"})


@dataclass(frozen=True)
class Box:
    """A rectangle with a top-left origin (y grows downwards): t < b."""

    l: float  # noqa: E741 (left, as in Docling's BoundingBox)
    t: float
    r: float
    b: float

    @property
    def w(self) -> float:
        return self.r - self.l

    @property
    def h(self) -> float:
        return self.b - self.t

    @property
    def cx(self) -> float:
        return (self.l + self.r) / 2

    @property
    def cy(self) -> float:
        return (self.t + self.b) / 2

    def union(self, o: "Box") -> "Box":
        return Box(min(self.l, o.l), min(self.t, o.t), max(self.r, o.r), max(self.b, o.b))

    def contains(self, o: "Box", tol: float = 1.5) -> bool:
        return (
            self.l - tol <= o.l
            and o.r <= self.r + tol
            and self.t - tol <= o.t
            and o.b <= self.b + tol
        )


@dataclass
class Char:
    text: str
    box: Box
    space_before: bool = False  # the PDF text has a space before this character


@dataclass
class Label:
    lines: list[list[Char]] = field(default_factory=list)

    @property
    def box(self) -> Box:
        boxes = [c.box for line in self.lines for c in line]
        out = boxes[0]
        for b in boxes[1:]:
            out = out.union(b)
        return out

    @property
    def line_height(self) -> float:
        return max(c.box.h for c in self.lines[-1])

    @property
    def text(self) -> str:
        return " ".join(_line_text(line) for line in self.lines)


@dataclass
class PageGeometry:
    """Characters and drawn shapes of one page, read once and reused for its tables."""

    chars: list[Char]
    boxes: list[Box]  # filled rectangles
    rules: list[Box]  # horizontal lines (thin rectangles or stroked paths)


def read_page(page: pdfium.PdfPage) -> PageGeometry:
    height = page.get_height()
    textpage = page.get_textpage()
    chars = []
    space = False
    for i in range(textpage.count_chars()):
        ch = chr(pdfium_c.FPDFText_GetUnicode(textpage.raw, i)).translate(_SOFT_HYPHENS)
        if not ch.strip():
            space = True
            continue
        # loose box: the font's full line height and advance width, so letters of one
        # word touch and all letters of a line share the same top and bottom
        left, bottom, right, top = textpage.get_charbox(i, loose=True)
        if right <= left or top <= bottom:
            continue
        chars.append(Char(ch, Box(left, height - top, right, height - bottom), space))
        space = False
    boxes, rules = [], []
    for obj in page.get_objects(max_depth=15):
        if obj.type != pdfium_c.FPDF_PAGEOBJ_PATH:
            continue
        left, bottom, right, top = obj.get_bounds()
        box = Box(left, height - top, right, height - bottom)
        if box.h < 2 and box.w >= 6:
            rules.append(box)
        elif box.h >= 2 and box.w >= 2 and _filled(obj):
            boxes.append(box)
    return PageGeometry(chars, boxes, rules)


def _filled(obj: pdfium.PdfObject) -> bool:
    mode, stroke = ctypes.c_int(), ctypes.c_int()
    pdfium_c.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(mode), ctypes.byref(stroke))
    return mode.value != 0


def _line_text(line: list[Char]) -> str:
    out = line[0].text
    for prev, ch in zip(line, line[1:], strict=False):
        gap = ch.box.l - prev.box.r
        space = ch.space_before or gap > WORD_GAP * max(prev.box.h, ch.box.h)
        out += (" " if space else "") + ch.text
    return out


def _lines(chars: list[Char]) -> list[list[Char]]:
    """Group characters into text lines (vertical overlap), each sorted left to right."""
    lines: list[list[Char]] = []
    for ch in sorted(chars, key=lambda c: (c.box.cy, c.box.l)):
        for line in lines:
            ref = line[0].box
            if abs(ch.box.cy - ref.cy) < 0.5 * min(ch.box.h, ref.h):
                line.append(ch)
                break
        else:
            lines.append([ch])
    return [sorted(line, key=lambda c: c.box.l) for line in lines]


def _phrases(line: list[Char]) -> list[list[Char]]:
    out = [[line[0]]]
    for prev, ch in zip(line, line[1:], strict=False):
        if ch.box.l - prev.box.r > PHRASE_GAP * max(prev.box.h, ch.box.h):
            out.append([ch])
        else:
            out[-1].append(ch)
    return out


def _box_of(chars: list[Char]) -> Box:
    out = chars[0].box
    for c in chars[1:]:
        out = out.union(c.box)
    return out


def _rule_between(upper: Box, lower: Box, rules: list[Box]) -> bool:
    lo, hi = max(upper.l, lower.l), min(upper.r, lower.r)
    return any(upper.cy < r.cy < lower.cy and r.l < hi and r.r > lo for r in rules)


def labels_in(region: Box, geometry: PageGeometry) -> list[Label]:
    """Header labels inside a region: phrases, with closely stacked lines merged."""
    chars = [
        c
        for c in geometry.chars
        if region.contains(Box(c.box.cx, c.box.cy, c.box.cx, c.box.cy), tol=0)
    ]
    if not chars:
        return []
    phrases = [p for line in _lines(chars) for p in _phrases(line)]
    labels: list[Label] = []
    for phrase in sorted(phrases, key=lambda p: (_box_of(p).t, _box_of(p).l)):
        pbox = _box_of(phrase)
        target = None
        for label in labels:
            last = _box_of(label.lines[-1])
            overlap = min(last.r, pbox.r) - max(last.l, pbox.l)
            gap = pbox.t - last.b
            if (
                -0.3 * pbox.h <= gap < LINE_MERGE_GAP * pbox.h
                and overlap >= 0.5 * min(last.w, pbox.w)
                and not _rule_between(last, pbox, geometry.rules)
            ):
                target = label
                break
        if target is None:
            labels.append(Label([phrase]))
        else:
            target.lines.append(phrase)
    return labels


def label_span(label: Label, labels: list[Label], geometry: PageGeometry, table: Box) -> Box | None:
    """The horizontal extent a label heads: its own box, or the rule under it (see the
    module docstring); None when the PDF draws neither."""
    box = label.box
    others = [o.box for o in labels if o is not label]
    owned = [
        b
        for b in geometry.boxes
        if b.contains(box)
        and b.w < BACKGROUND * table.w
        and not any(b.contains(Box(o.cx, o.cy, o.cx, o.cy), tol=0) for o in others)
    ]
    if owned:
        return min(owned, key=lambda b: b.w * b.h)
    same_line = [o for o in others if o.t < box.b and o.b > box.t]
    below = [
        r
        for r in geometry.rules
        if box.b - 1 <= r.cy <= box.b + RULE_DISTANCE * label.line_height
        and r.l - 2 <= box.cx <= r.r + 2
        and not any(r.l < o.cx < r.r for o in same_line)
    ]
    if below:
        return min(below, key=lambda r: r.cy)
    return None


def _columns_under(span: Box, centres: list[float]) -> list[int]:
    return [c for c, x in enumerate(centres) if span.l - 2 <= x <= span.r + 2]


def _columns_overlapped(box: Box, extents: list[Box]) -> list[int]:
    hit = [c for c, e in enumerate(extents) if min(e.r, box.r) - max(e.l, box.l) > 0]
    if hit:
        return hit
    return [min(range(len(extents)), key=lambda c: abs(extents[c].cx - box.cx))]


def _is_value(text: str, langue: str) -> bool:
    n = parse_number(text, langue)
    if n is None:
        return False
    return n.value == 0 or n.percent or n.decimals > 0 or abs(n.value) >= 100


def first_body_row(table: TableData, langue: str) -> int | None:
    """First grid row with a value cell (a number other than a small integer, or "-")."""
    k = label_columns(table, langue)
    for r, row in enumerate(table.cells):
        if any(_is_value(x, langue) for x in row[k:] if x.strip()):
            return r
    return None


def geometric_header(
    item: TableItem, table: TableData, langue: str, geometry: PageGeometry, page_height: float
) -> TableData | None:
    """The table with its header rows rebuilt from the PDF (body rows unchanged), or
    None when the geometry is not usable."""
    if len({p.page_no for p in item.prov}) != 1 or table.n_cols < 2:
        return None
    body = first_body_row(table, langue)
    if not body:
        return None
    extents: list[Box | None] = [None] * table.n_cols
    body_top = None
    for cell in item.data.table_cells:
        if cell.bbox is None or cell.start_row_offset_idx < body:
            continue
        b = cell.bbox
        if b.coord_origin != CoordOrigin.TOPLEFT:
            b = b.to_top_left_origin(page_height)
        box = Box(b.l, b.t, b.r, b.b)
        if cell.start_row_offset_idx == body:
            body_top = box.t if body_top is None else min(body_top, box.t)
        if cell.end_col_offset_idx - cell.start_col_offset_idx == 1:
            c = cell.start_col_offset_idx
            extents[c] = box if extents[c] is None else extents[c].union(box)  # type: ignore[union-attr]
    if body_top is None or any(e is None for e in extents):
        return None
    cols: list[Box] = [e for e in extents if e is not None]
    tb = item.prov[0].bbox.to_top_left_origin(page_height)
    table_box = Box(tb.l, tb.t, tb.r, tb.b)
    region = Box(table_box.l - 2, table_box.t - 1, table_box.r + 2, body_top - 0.5)
    labels = labels_in(region, geometry)
    if not labels:
        return None

    centres = [e.cx for e in cols]
    paths: list[list[tuple[float, float, str]]] = [[] for _ in cols]
    for label in labels:
        span = label_span(label, labels, geometry, table_box)
        columns = _columns_under(span, centres) if span is not None else []
        if not columns:
            columns = _columns_overlapped(label.box, cols)
        for c in columns:
            paths[c].append((label.box.t, label.box.l, label.text))
    depth = max(len(p) for p in paths)
    header = [["" for _ in cols] for _ in range(depth)]
    for c, path in enumerate(paths):
        texts = [t for _, _, t in sorted(path)]
        for r, text in enumerate(texts[:-1]):
            header[r][c] = text
        if texts:
            header[depth - 1][c] = texts[-1]
    rows = header + [list(r) for r in table.cells[body:]]
    shift = depth - body
    return table.model_copy(
        update={
            "cells": rows,
            "n_rows": len(rows),
            "header_rows": depth,
            "low_confidence": [(r + shift, c) for r, c in table.low_confidence if r >= body],
        }
    )
