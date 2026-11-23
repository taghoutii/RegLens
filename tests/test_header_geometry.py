"""Header labels and their spans, from synthetic page geometry."""

from reglens.ingestion.header_geometry import Box, Char, PageGeometry, label_span, labels_in


def word(text: str, x: float, y: float, w: float = 5.0, h: float = 8.0) -> list[Char]:
    return [Char(ch, Box(x + i * w, y, x + (i + 1) * w, y + h)) for i, ch in enumerate(text)]


TABLE = Box(0, 0, 300, 100)
REGION = Box(0, 0, 300, 60)


def test_stacked_lines_form_one_label_and_far_phrases_split() -> None:
    chars = word("Total", 100, 30) + word("exposure", 95, 38) + word("Collateral", 200, 38)
    labels = labels_in(REGION, PageGeometry(chars, [], []))
    assert sorted(label.text for label in labels) == ["Collateral", "Total exposure"]


def test_rule_under_a_label_gives_its_span() -> None:
    chars = word("2025", 150, 10) + word("A", 100, 30) + word("B", 200, 30)
    rule = Box(90, 19, 260, 19.5)
    geometry = PageGeometry(chars, [], [rule])
    labels = labels_in(REGION, geometry)
    year = next(label for label in labels if label.text == "2025")
    assert label_span(year, labels, geometry, TABLE) == rule


def test_box_holding_two_labels_is_not_a_span() -> None:
    chars = word("A", 100, 30) + word("B", 200, 30)
    band = Box(90, 25, 260, 45)
    geometry = PageGeometry(chars, [band], [])
    labels = labels_in(REGION, geometry)
    assert all(label_span(label, labels, geometry, TABLE) is None for label in labels)
