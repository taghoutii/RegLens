"""Article references found by regex.

    "Article 19(4) of Regulation (EU) 2022/2554"        -> DORA:19
    "article 19, paragraphe 4, du règlement (UE) 2022/2554" -> DORA:19
    "Articles 1 to 8 of Delegated Regulation (EU) 2024/1772" -> RTS2024/1772:1 ... :8
    "Article 3 of Directive (EU) 2022/2555"             -> 2022/2555:3 (act not in the corpus)
    "Article 17" (no act named)                          -> <this regulation>:17

An act is attached to an article only if it follows closely, linked by "of" / "du" / "de"
("Article 5, the entity shall comply with Regulation X" is *not* a reference to X:5).
Article headings ("Article 19 — Reporting ...") are not references.
Keys are the regulation short names without spaces, from data/sources.yaml.
"""

import re
from collections.abc import Iterable

_NUMBER = r"(?:\d+[a-z]?(?:\s?er)?|premier)"
_ARTICLES = re.compile(
    rf"\b[Aa]rticles?\s+(?P<list>{_NUMBER}(?:\s*\(\d+\))?"
    rf"(?:\s*(?:,|and|or|to|et|ou|à)\s*{_NUMBER}(?:\s*\(\d+\))?)*)"
)
_LIST_ITEM = re.compile(rf"{_NUMBER}|\b(?:to|à)\b")
_ACT = re.compile(
    r"\((?:EU|UE|EC|CE|EEC|CEE)\)\s*(?P<no>No\.?\s*|n\s?o\s*|n°\s*)?(?P<a>\d+)/(?P<b>\d+)"
)
_LINK = re.compile(r"\b(?:of|du|de|des|d['’])\b", re.IGNORECASE)
_STOP = re.compile(r"[.;:]\s|\b[Aa]rticles?\b")
_WINDOW = 120  # characters after the article number where its act may be named
_HEADING_END = re.compile(r"[ \t]*(?:—|-|–|\n|$)")
_CELEX = re.compile(r"CELEX:3(\d{4})[RL](\d{4})")


def act_number_from_url(url: str) -> str | None:
    """'...CELEX:32022R2554' -> '2022/2554' (how acts are cited in the text)."""
    m = _CELEX.search(url)
    return f"{m.group(1)}/{int(m.group(2))}" if m else None


def ref_key(short_name: str) -> str:
    return short_name.replace(" ", "")


def _numbers(text: str) -> list[str]:
    """'1 to 8' -> 1..8; '17 and 18' -> 17, 18; 'premier' / '1 er' -> 1."""
    text = re.sub(r"\(\d+\)", "", text)  # "19(4)": the paragraph is not an article
    items = [t.replace(" ", "") for t in _LIST_ITEM.findall(text)]
    out: list[str] = []
    for i, item in enumerate(items):
        if item in ("to", "à"):
            continue
        number = "1" if item in ("premier", "1er") else item.removesuffix("er")
        if i >= 2 and items[i - 1] in ("to", "à") and out and out[-1].isdigit():
            if number.isdigit() and int(out[-1]) < int(number) <= int(out[-1]) + 50:
                out.extend(str(k) for k in range(int(out[-1]) + 1, int(number) + 1))
                continue
        out.append(number)
    return out


def _act_after(text: str, pos: int) -> str | None:
    """The act named right after an article list, as 'YYYY/N' (or 'N/YYYY' old style)."""
    window = text[pos : pos + _WINDOW]
    stop = _STOP.search(window)
    window = window[: stop.start()] if stop else window
    m = _ACT.search(window)
    if not m or not _LINK.search(window[: m.start()]):
        return None
    return f"{m.group('a')}/{m.group('b')}"


def find_references(text: str, *, own: str | None, acts: dict[str, str]) -> list[str]:
    """References in `text`. `own` = key of the document's regulation (None for a text
    that is not a regulation); `acts` maps '2022/2554' -> 'DORA' (short names)."""
    found: list[str] = []
    for m in _ARTICLES.finditer(text):
        line_start = m.start() == 0 or text[m.start() - 1] == "\n"
        if line_start and _HEADING_END.match(text, m.end()):
            continue  # "Article 19 — Title": a heading, not a reference
        act = _act_after(text, m.end())
        if act is None:
            key = own
        else:
            key = ref_key(acts[act]) if act in acts else act
        if key is None:
            continue
        found.extend(f"{key}:{n}" for n in _numbers(m.group("list")))
    return list(dict.fromkeys(found))  # unique, first-seen order


def most_cited(text: str, names: Iterable[tuple[str, str]]) -> str | None:
    """The regulation a policy cites most: `names` = (short_name, act_number) pairs;
    a mention is the short name ("DORA") or the act number ("2022/2554")."""
    best, best_count = None, 0
    for short, number in names:
        count = len(re.findall(rf"(?<![\w/]){re.escape(short)}(?![\w/])", text))
        count += len(re.findall(rf"(?<![\d/]){re.escape(number)}(?![\d/])", text))
        if count > best_count:
            best, best_count = short, count
    return best
