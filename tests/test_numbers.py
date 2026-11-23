"""One number parser for the whole code base."""

from reglens.ingestion.numbers import has_several_numbers, is_year, parse_number


def test_parse_number_formats() -> None:
    assert parse_number("1,234.5", "en") is not None
    assert parse_number("1,234.5", "en").value == 1234.5  # type: ignore[union-attr]
    assert parse_number("(1,043)", "en").value == -1043  # type: ignore[union-attr]
    assert parse_number("1 571 898", "fr").value == 1571898  # type: ignore[union-attr]
    assert parse_number("0,84%", "fr").percent  # type: ignore[union-attr]
    assert parse_number("-", "en").value == 0  # type: ignore[union-attr]
    assert parse_number("1,565 2,387", "en") is None  # two numbers fused in one cell
    assert parse_number("Belgium", "en") is None


def test_space_before_closing_bracket_is_one_number() -> None:
    assert parse_number("(6,063 )", "en").value == -6063  # type: ignore[union-attr]
    assert not has_several_numbers("(6,063 )")


def test_years_are_recognized() -> None:
    assert is_year(parse_number("2025", "en"))  # type: ignore[arg-type]
    assert not is_year(parse_number("2025.0", "en"))  # type: ignore[arg-type]
