"""Numbers as printed in report tables: one parser for the whole code base.

    English reports:  1,234.5   (1,043)   12.5%   1 234 (space-grouped)
    French reports:   1 234,5   (1 043)   12,5 %
    nil:              -  –  —   (read as 0)

`parse_number` reads one number; a cell that holds text or several numbers gives None.
`has_several_numbers` flags a known table-structure error: values of several rows fused
into one cell ("1,571 898 92").
"""

import re
from dataclasses import dataclass

from reglens.ingestion.text_norm import clean

# One number: optional bracket and minus, thousands grouped by "," (EN) or a space (EN and
# FR), optional decimals, optional space before ")" and "%".
_EN = re.compile(
    r"^\(?-?(?:\d{1,3}(?:,\d{3})+|\d{1,3}(?: \d{3})+|\d+)(?:\.(\d+))?\s?\)?\s?%?$"
)  # fmt: skip
_FR = re.compile(r"^\(?-?(?:\d{1,3}(?: \d{3})+|\d+)(?:,(\d+))?\s?\)?\s?%?$")
_NIL = {"-", "–", "—"}
_DIGIT = re.compile(r"\d")
YEARS = (1990, 2035)  # a bare integer in this range is read as a year


@dataclass(frozen=True)
class Number:
    value: float
    decimals: int
    percent: bool


def parse_number(text: str, langue: str) -> Number | None:
    """The number printed in a cell, or None if the cell is not one number (text,
    empty, or several numbers fused together)."""
    s = clean(text)
    if s in _NIL:
        return Number(0.0, 0, False)
    m = (_FR if langue == "fr" else _EN).match(s)
    if not m:
        return None
    digits = s.replace(" ", "").replace("%", "").strip("()")
    digits = digits.replace(",", ".") if langue == "fr" else digits.replace(",", "")
    try:
        value = float(digits)
    except ValueError:
        return None
    if s.startswith("(") and (s.endswith(")") or s.endswith(")%")):
        value = -abs(value)
    return Number(value, len(m.group(1) or ""), s.endswith("%"))


def is_year(n: Number) -> bool:
    return n.decimals == 0 and not n.percent and YEARS[0] <= n.value <= YEARS[1]


def is_one_number(text: str) -> bool:
    """One number in either language's notation (used where the language is unknown)."""
    return parse_number(text, "en") is not None or parse_number(text, "fr") is not None


def has_several_numbers(text: str) -> bool:
    """True for a cell with no letters that is not one number: "1,571 898 92" is three
    values from three rows fused into one cell (a known table-structure error).
    Limitation: "674 340" reads as one space-grouped number, so it is not flagged."""
    text = text.strip()
    if not text or any(ch.isalpha() for ch in text) or not _DIGIT.search(text):
        return False
    return not is_one_number(text) and len(text.split()) >= 2
