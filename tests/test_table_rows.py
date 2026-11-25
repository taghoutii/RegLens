"""Header rows and self-describing table rows, on hand-built tables."""

from reglens.ingestion.models import TableData
from reglens.ingestion.table_rows import (
    header_paths,
    infer_header_rows,
    row_lines,
    with_header,
)
from tests.table_stubs import make

MAX = 4  # inferred header rows at most (the configured value)


def test_header_paths_join_every_header_row() -> None:
    t = make([["", "31 December 2025", "31 December 2025"],
              ["EUR m", "Gross carrying amount", "of which: defaulted"],
              ["Loans", "100", "7"]], header_rows=2)  # fmt: skip
    assert header_paths(t) == ["EUR m", "31 December 2025 > Gross carrying amount",
                               "31 December 2025 > of which: defaulted"]  # fmt: skip


def test_header_paths_without_header() -> None:
    t = TableData(table_id="x#tbl-1", n_rows=1, n_cols=2, cells=[["a", "b"]])
    assert header_paths(t) == ["", ""]


def test_row_lines_carry_section_and_labels() -> None:
    t = make([["", "2025"],
              ["Assets", ""],
              ["Cash", "39,306"],
              ["Total assets", "39,306"]], header_rows=1)  # fmt: skip
    assert row_lines(t, "en") == ["Assets > Cash — 2025 = 39,306",
                                  "Assets > Total assets — 2025 = 39,306"]  # fmt: skip


def test_row_number_column_belongs_to_the_label() -> None:
    t = make([["", "", "Amount"], ["1", "Credit risk", "216"], ["2", "Market risk", "12"]],
             header_rows=1)  # fmt: skip
    assert row_lines(t, "en")[0] == "1 Credit risk — Amount = 216"


def test_header_rows_are_inferred_when_docling_found_none() -> None:
    t = make([["(In EURm)", "2025", "2024"], ["Loans", "1,200", "1,100"]], header_rows=0)
    assert infer_header_rows(t, "en", MAX) == 1
    assert header_paths(with_header(t, "en", MAX)) == ["(In EURm)", "2025", "2024"]


def test_section_row_repeated_by_a_span_is_not_a_header() -> None:
    t = make([["", "Amount", "Share"],
              ["ASSETS", "ASSETS", ""],
              ["Loans", "1,200", "12.5%"]], header_rows=0)  # fmt: skip
    assert infer_header_rows(t, "en", MAX) == 1


def test_inferred_header_is_capped_and_needs_numbers_below() -> None:
    five = make([["", f"h{i}"] for i in range(5)] + [["Loans", "1,200"]], header_rows=0)
    assert infer_header_rows(five, "en", MAX) == 4
    text_only = make([["Name", "Role"], ["Alice", "Chair"]], header_rows=0)
    assert infer_header_rows(text_only, "en", MAX) == 0
