"""Spreadsheet blocks matched to extracted tables by their numbers, and the replacement."""

from pathlib import Path
from typing import Any

import openpyxl

from reglens.ingestion.models import Block, ParsedDocument, TableData
from reglens.ingestion.structured_tables import (
    align_rows,
    apply_spreadsheet,
    display,
    match_table,
    printed_forms,
    read_blocks,
    subtitle,
)
from reglens.ingestion.table_rows import header_paths

RULE: dict[str, Any] = {"min_numbers": 5, "min_share": 0.95, "margin": 0.2}


def write_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CQ1"
    rows: list[list[object]] = [
        ["Template EU CQ1: Credit quality"], [2025, "Gross", "Impairment"],
        ["Loans", 7854.4, -187], ["Households", 2879, -28.2], ["Total", 10733.4, 0.1308],
        [], [], [],
        ["Template EU CQ1: Credit quality"], [2024, "Gross", "Impairment"],
        ["Loans", 6100, -150], ["Households", 2500, -20], ["Total", 8600, 0.12],
    ]  # fmt: skip
    for row in rows:
        ws.append(row)
    for r in (3, 4, 5, 11, 12, 13):
        ws.cell(r, 2).number_format = "#,##0"
        ws.cell(r, 3).number_format = "#,##0;(#,##0)"
    ws.cell(5, 3).number_format = "0.00%"
    wb.save(path)


def pdf_table() -> TableData:
    cells = [["EUR m", "Gross", "Impairment"],
             ["Loans", "7,854", "(187)"],
             ["Households", "2,879", "(28)"],
             ["Total", "10,733", "13.08%"]]  # fmt: skip
    return TableData(table_id="ing#tbl-1", n_rows=4, n_cols=3, cells=cells, header_rows=1)


def test_display_follows_the_number_format() -> None:
    assert display(7854.4, "#,##0") == "7,854"
    assert display(-187, "#,##0;(#,##0)") == "(187)"
    assert display(0.1308, "0.00%") == "13.08%"
    assert display(0, '#,##0;(#,##0);"-"') == "-"
    assert display(2025, "General") == "2025"


def test_blocks_split_on_template_titles(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    write_workbook(path)
    assert [b.name for b in read_blocks(path)] == ["CQ1/1", "CQ1/2"]


def test_table_maps_to_the_block_holding_its_rounded_numbers(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    write_workbook(path)
    blocks = read_blocks(path)
    keys = {b.name: printed_forms(b.numbers()) for b in blocks}
    m = match_table(pdf_table(), "en", blocks, keys, **RULE)  # 5 informative numbers
    assert m.best == "CQ1/1" and m.best_share == 1.0 and m.certain
    assert align_rows(pdf_table(), "en", blocks[0]).complete


def test_too_few_numbers_is_never_certain(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    write_workbook(path)
    blocks = read_blocks(path)
    keys = {b.name: printed_forms(b.numbers()) for b in blocks}
    rule = {**RULE, "min_numbers": 6}
    assert not match_table(pdf_table(), "en", blocks, keys, **rule).certain  # 5 < 6


def test_replacement_keeps_id_and_page(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    write_workbook(path)
    block = Block(block_id="ing#tbl-1/table", parent_id="ing#tbl-1", kind="table",
                  text="CQ1", page_number=57, table=pdf_table())  # fmt: skip
    doc = ParsedDocument(doc_id="ing", source="r.pdf", type_doc="reports", langue="en",
                         parser="docling", blocks=[block])  # fmt: skip
    [r] = apply_spreadsheet(doc, path, **RULE)
    assert r.replaced, r.reason
    new = doc.blocks[0].table
    assert new is not None and new.content_source == "spreadsheet"
    assert new.table_id == "ing#tbl-1" and doc.blocks[0].page_number == 57
    assert new.cells[0] == ["2025", "Gross", "Impairment"]
    assert ["Loans", "7,854", "(187)"] in new.cells


def test_no_replacement_when_a_value_differs(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    write_workbook(path)
    t = pdf_table()
    t.cells[1][1] = "7,855"  # the PDF prints another value than the spreadsheet
    block = Block(block_id="ing#tbl-1/table", parent_id="ing#tbl-1", kind="table",
                  text="CQ1", page_number=57, table=t)  # fmt: skip
    doc = ParsedDocument(doc_id="ing", source="r.pdf", type_doc="reports", langue="en",
                         parser="docling", blocks=[block])  # fmt: skip
    [r] = apply_spreadsheet(doc, path, **RULE)
    assert not r.replaced
    assert doc.blocks[0].table is t


def test_header_found_after_a_code_title_and_a_one_cell_subtitle(tmp_path: Path) -> None:
    # "EU <code> - ..." title (no "Template" word), then a one-cell subtitle "2025": both
    # used to stop the header detection, so the table was stored without header rows.
    path = tmp_path / "b.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TLAC1"
    for row in [["EU TLAC1 - Composition"], [2025], ["", "Gross", "Impairment"],
                ["Loans", 7854.4, -187], ["Households", 2879, -28.2],
                ["Total", 10733.4, 0.1308]]:  # fmt: skip
        ws.append(row)
    for n in (4, 5, 6):
        ws.cell(n, 2).number_format = "#,##0"
        ws.cell(n, 3).number_format = "#,##0;(#,##0)"
    ws.cell(6, 3).number_format = "0.00%"
    wb.save(path)
    block = Block(block_id="ing#tbl-1/table", parent_id="ing#tbl-1", kind="table",
                  text="TLAC1", page_number=3, table=pdf_table())  # fmt: skip
    doc = ParsedDocument(doc_id="ing", source="r.pdf", type_doc="reports", langue="en",
                         parser="docling", blocks=[block])  # fmt: skip
    [r] = apply_spreadsheet(doc, path, **RULE)
    assert r.replaced, r.reason
    new = doc.blocks[0].table
    assert new is not None and new.header_rows == 2
    assert new.cells[0] == ["2025", "2025", "2025"]  # the subtitle names every column
    assert header_paths(new)[1:] == ["2025 > Gross", "2025 > Impairment"]
    assert ["Loans", "7,854", "(187)"] in new.cells


def test_subtitle_rows_hold_one_text_and_no_value() -> None:
    # a one-text row (or merged span) with no value; one just above the body is trimmed
    assert subtitle(["Own funds", "Own funds", "Own funds"], "en")  # a merged span
    assert subtitle(["2025", "", ""], "en")
    assert not subtitle(["Loans", "7,854", ""], "en")
