"""Spreadsheet blocks matched to extracted tables by their numbers."""

from pathlib import Path

import openpyxl

from reglens.ingestion.models import TableData
from reglens.ingestion.structured_tables import (
    align_rows,
    match_table,
    printed_forms,
    read_blocks,
)


def write_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CQ1"
    rows: list[list[object]] = [
        ["Template EU CQ1: Credit quality"],
        [2025, "Gross", "Impairment"],
        ["Loans", 7854.4, -187],
        ["Households", 2879, -28.2],
        ["Total", 10733.4, 0.1308],
        [],
        [],
        [],
        ["Template EU CQ1: Credit quality"],
        [2024, "Gross", "Impairment"],
        ["Loans", 6100, -150],
        ["Households", 2500, -20],
        ["Total", 8600, 0.12],
    ]
    for row in rows:
        ws.append(row)
    wb.save(path)


def pdf_table() -> TableData:
    cells = [
        ["EUR m", "Gross", "Impairment"],
        ["Loans", "7,854", "(187)"],
        ["Households", "2,879", "(28)"],
        ["Total", "10,733", "13.08%"],
    ]
    return TableData(table_id="ing#tbl-1", n_rows=4, n_cols=3, cells=cells, header_rows=1)


def test_blocks_split_on_template_titles(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    write_workbook(path)
    assert [b.name for b in read_blocks(path)] == ["CQ1/1", "CQ1/2"]


def test_table_maps_to_the_block_holding_its_rounded_numbers(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    write_workbook(path)
    blocks = read_blocks(path)
    keys = {b.name: printed_forms(b.numbers()) for b in blocks}
    m = match_table(pdf_table(), "en", blocks, keys)  # 5 informative numbers
    assert m.best == "CQ1/1" and m.best_share == 1.0 and m.certain
    alignment = align_rows(pdf_table(), "en", blocks[0])
    assert alignment.complete


def test_too_few_numbers_is_never_certain(tmp_path: Path) -> None:
    path = tmp_path / "a.xlsx"
    write_workbook(path)
    blocks = read_blocks(path)
    keys = {b.name: printed_forms(b.numbers()) for b in blocks}
    assert not match_table(pdf_table(), "en", blocks, keys, min_numbers=6).certain  # 5 < 6
