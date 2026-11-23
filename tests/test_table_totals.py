"""The Total-row check (a diagnostic), on hand-built tables."""

from reglens.evaluation.table_totals import check_totals
from tests.table_stubs import make


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
