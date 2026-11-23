"""Self-describing table rows and the Total-row check, on hand-built tables."""

from reglens.ingestion.models import TableData
from reglens.ingestion.table_rows import (
    check_totals,
    header_paths,
    infer_header_rows,
    parse_number,
    row_lines,
    with_header,
)


def make(cells: list[list[str]], header_rows: int) -> TableData:
    return TableData(table_id="rep#tbl-1", n_rows=len(cells), n_cols=len(cells[0]),
                     cells=cells, header_rows=header_rows)  # fmt: skip


def test_parse_number_formats() -> None:
    assert parse_number("1,234.5", "en") is not None
    assert parse_number("1,234.5", "en").value == 1234.5  # type: ignore[union-attr]
    assert parse_number("(1,043)", "en").value == -1043  # type: ignore[union-attr]
    assert parse_number("1 571 898", "fr").value == 1571898  # type: ignore[union-attr]
    assert parse_number("0,84%", "fr").percent  # type: ignore[union-attr]
    assert parse_number("-", "en").value == 0  # type: ignore[union-attr]
    assert parse_number("1,565 2,387", "en") is None  # two numbers fused in one cell
    assert parse_number("Belgium", "en") is None


def test_header_paths_join_every_header_row() -> None:
    t = make([["", "31 December 2025", "31 December 2025"],
              ["EUR m", "Gross carrying amount", "of which: defaulted"],
              ["Loans", "100", "7"]], header_rows=2)  # fmt: skip
    assert header_paths(t) == ["EUR m", "31 December 2025 > Gross carrying amount",
                               "31 December 2025 > of which: defaulted"]  # fmt: skip


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


def test_total_equal_to_its_rows_passes_within_rounding() -> None:
    t = make([["", "2025", "2024"],
              ["France", "24,443", "100.4"],
              ["of which: Paris", "10,000", "50.0"],
              ["Belgium", "182,783", "200.4"],
              ["Total", "207,226", "300.9"]], header_rows=1)  # fmt: skip
    totals = check_totals(t, "en")
    assert totals.checkable and not totals.failed
    assert totals.checks[0].components == [1, 3]  # the "of which" row is not summed


def test_value_in_the_wrong_column_fails() -> None:
    t = make([["", "2025", "2024"],
              ["France", "10", "5"],
              ["Belgium", "20", ""],
              ["Total", "30", "25"]], header_rows=1)  # fmt: skip
    # Belgium's 2024 value (20) was read into the 2025 column; 2024 no longer adds up
    t.cells[2][2] = ""
    totals = check_totals(t, "en")
    assert totals.checkable and totals.failed
    assert totals.checks[0].mismatched == [2]


def test_grand_total_of_subtotals() -> None:
    t = make([["", "v"],
              ["a", "1"], ["b", "2"], ["Total A", "3"],
              ["c", "4"], ["d", "5"], ["Total B", "9"],
              ["TOTAL", "12"]], header_rows=1)  # fmt: skip
    totals = check_totals(t, "en")
    assert [c.checkable for c in totals.checks] == [True, True, True]
    assert not totals.failed


def test_table_without_total_is_not_checkable() -> None:
    t = make([["", "v"], ["a", "1"], ["b", "2"]], header_rows=1)
    totals = check_totals(t, "en")
    assert totals.total_rows == 0 and not totals.checkable


def test_hierarchical_rows_are_not_counted_twice() -> None:
    t = make([["", "v"],
              ["Cash", "5"],
              ["Loans and advances", "30"], ["Banks", "10"], ["Households", "20"],
              ["Off-balance", "7"], ["Banks", "3"], ["Households", "4"],
              ["TOTAL", "42"]], header_rows=1)  # fmt: skip
    totals = check_totals(t, "en")
    assert totals.checkable and not totals.failed
    assert totals.checks[0].components == [1, 2, 5]


def test_rounding_tolerance_follows_each_value() -> None:
    t = make([["", "v"], ["a", "50"], ["b", "205"], ["c", "1.57"], ["Total", "257"]],
             header_rows=1)  # fmt: skip
    assert not check_totals(t, "en").failed


def test_header_rows_are_inferred_when_docling_found_none() -> None:
    t = make([["(In EURm)", "2025", "2024"], ["Loans", "1,200", "1,100"]], header_rows=0)
    assert infer_header_rows(t, "en") == 1
    assert header_paths(with_header(t, "en")) == ["(In EURm)", "2025", "2024"]


def test_section_row_repeated_by_a_span_is_not_a_header() -> None:
    t = make([["", "Amount", "Share"],
              ["ASSETS", "ASSETS", ""],
              ["Loans", "1,200", "12.5%"]], header_rows=0)  # fmt: skip
    assert infer_header_rows(t, "en") == 1


def test_inferred_header_is_capped_and_needs_numbers_below() -> None:
    five = make([["", f"h{i}"] for i in range(5)] + [["Loans", "1,200"]], header_rows=0)
    assert infer_header_rows(five, "en") == 4
    text_only = make([["Name", "Role"], ["Alice", "Chair"]], header_rows=0)
    assert infer_header_rows(text_only, "en") == 0
