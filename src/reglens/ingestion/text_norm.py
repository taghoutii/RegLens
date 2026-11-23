"""Text normalization shared by parsing, chunking and the table checks.

    clean("Gross  carrying\namount")  -> "Gross carrying amount"    (display, labels)
    compact("off\x02balance  1 234")  -> "off-balance1234"          (comparison key)

The PDF text layer and Docling may write the same character differently (soft hyphens,
minus signs, no-break spaces): `compact` maps them to one form and drops all whitespace,
so two tools' output can be compared character by character.
"""

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")  # Unicode \s: includes no-break and narrow no-break spaces
# \x02 and ­ are soft hyphens in the PDF text layer ("off\x02balance" = "off-balance").
_EQUIVALENT = str.maketrans({
    "−": "-", "–": "-", "—": "-", "’": "'",
    " ": " ", "\x02": "-", "­": "-",
})  # fmt: skip


def clean(text: str) -> str:
    """Collapse every run of whitespace to one space and strip the ends."""
    return _WHITESPACE.sub(" ", text).strip()


def compact(text: str) -> str:
    """Comparison key: Unicode-normalized, equivalent characters unified, no whitespace."""
    return _WHITESPACE.sub("", unicodedata.normalize("NFKC", text).translate(_EQUIVALENT))
