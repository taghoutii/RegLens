"""Rules for candidate tables continued across a page break."""

from reglens.evaluation.table_continuations import Item, candidates, last_row_is_total


def table(n: int, page: int, cols: int = 4, caption: str | None = None) -> Item:
    return Item("table", page, n, cols, caption, top=0.1, bottom=0.9)


def test_table_running_onto_next_page_is_a_candidate() -> None:
    items = [table(1, 3), Item("page_footer", 3), Item("page_header", 4), table(2, 4)]
    (c,) = candidates(items)
    assert (c.first, c.second, c.page) == (1, 2, 3)


def test_content_between_or_page_gap_or_columns_break_the_pair() -> None:
    assert candidates([table(1, 3), Item("section_header", 4), table(2, 4)]) == []
    assert candidates([table(1, 3), table(2, 5)]) == []  # not the next page
    assert candidates([table(1, 3), table(2, 3)]) == []  # same page
    assert candidates([table(1, 3, cols=4), table(2, 4, cols=5)]) == []


def test_caption_must_be_absent_or_say_continued() -> None:
    assert candidates([table(1, 3), table(2, 4, caption="EU CR1 - Credit quality")]) == []
    (c,) = candidates([table(1, 3), table(2, 4, caption="EU CR1 (continued)")])
    assert c.caption_says_continued
    assert len(candidates([table(1, 3), table(2, 4, caption="Tableau 12 (suite)")])) == 1


def test_last_row_total_is_recorded() -> None:
    assert last_row_is_total([["Item", "1"], ["TOTAL LIABILITIES", "9"], ["", ""]])
    assert not last_row_is_total([["Total assets", "1"], ["Chile", "2"]])
    assert not last_row_is_total([["", "1,200"]])
    a = Item("table", 3, 1, 4, None, 0.1, 0.9, ends_with_total=True)
    (c,) = candidates([a, table(2, 4)])
    assert c.first_ends_with_total
