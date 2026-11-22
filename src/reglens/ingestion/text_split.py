"""Splitting text into pieces: sentences, semantic cuts, recursive cuts.

The heavy tools are passed in, not imported:
    Tokens        counts tokens (bge-m3's tokenizer in production, a word count in tests)
    sentences     finds sentence spans (spaCy in production, a regex in tests)
    similarities  cosine similarity of consecutive sentences (bge-m3 in production)
so the logic is unit-tested without any model.
"""

import re
from collections.abc import Callable, Sequence
from typing import Protocol

Span = tuple[int, int]


class Tokens(Protocol):
    def count(self, text: str) -> int: ...

    def tail_start(self, text: str, n: int) -> int:
        """Character index where the last `n` tokens of `text` begin."""
        ...

    def head_end(self, text: str, n: int) -> int:
        """Character index where the first `n` tokens of `text` end."""
        ...


SentenceFinder = Callable[[str], list[Span]]
Similarities = Callable[[list[str]], list[float]]

# A "sentence" made only of a label belongs to what follows it: "1.", "1.2.", "(a)", "ii)",
# and quoted labels that open an amendment ("‘3.", "«6.").
_LABEL_ONLY = re.compile(
    r"^[‘'\"«“]?(?:\(\w{1,5}\)|\w{1,5}\)|\d{1,3}(?:\.\d{1,3})*\.|[a-z]\.)$", re.IGNORECASE
)
# A sentence that ends with an abbreviation was cut too early ("Art. 5", "No. 3", "p. 12").
_ENDS_WITH_ABBREVIATION = re.compile(
    r"(?:^|[\s(’'])(?:Art|Arts|No|Nos|p|pp|para|paras|cf|vol|e\.g|i\.e|ex|n°|al|Ch|Sect|Reg)\.$",
    re.IGNORECASE,
)
# A real sentence never starts with a lowercase letter, with , ; : ) |, or with an act
# number ("Regulation (EC) / No 1060/2009": spaCy cuts before "No").
_CONTINUES = re.compile(r"^(?:[a-zà-ÿ,;:)|]|No\.?\s?\d|n\s?°\s?\d)")


def _no_words(piece: str) -> bool:
    """Only punctuation or footnote marks: "»." "( * )" "(" never stand alone."""
    return not any(ch.isalnum() for ch in piece)


def repair_sentences(text: str, spans: list[Span]) -> list[Span]:
    """Glue back sentences that a splitter cut after a label or an abbreviation, or
    around a piece that cannot be a sentence on its own."""
    out: list[Span] = []
    for start, end in spans:
        if out:
            prev, cur = text[out[-1][0] : out[-1][1]].strip(), text[start:end].strip()
            glue_prev = _LABEL_ONLY.match(prev) or _ENDS_WITH_ABBREVIATION.search(prev)
            if glue_prev or _no_words(prev) or _no_words(cur) or _CONTINUES.match(cur):
                out[-1] = (out[-1][0], end)
                continue
        out.append((start, end))
    return out


def line_spans(text: str) -> list[Span]:
    """Non-empty lines. In EUR-Lex text every point "(a) ..." is its own line."""
    spans, pos = [], 0
    for line in text.split("\n"):
        if line.strip():
            lead = len(line) - len(line.lstrip())
            spans.append((pos + lead, pos + len(line.rstrip())))
        pos += len(line) + 1
    return spans


def sentence_spans(text: str, finder: SentenceFinder) -> list[Span]:
    """Lines first (a line break is always a boundary), then sentences inside each line.
    A table row flattened by the EUR-Lex parser ("1.2. | Name | Full legal name") is kept
    whole: its cells are not sentences."""
    out: list[Span] = []
    for ls, le in line_spans(text):
        if " | " in text[ls:le]:
            out.append((ls, le))
            continue
        inner = [(ls + s, ls + e) for s, e in finder(text[ls:le])] or [(ls, le)]
        out.extend(repair_sentences(text, inner))
    return out


def _snap_forward(text: str, i: int, limit: int) -> int:
    """Move a cut position to the start of the next word (never inside a word)."""
    while 0 < i < limit and not text[i - 1].isspace():
        i += 1
    while i < limit and text[i].isspace():
        i += 1
    return i


def hard_split(text: str, span: Span, tokens: Tokens, max_tokens: int) -> list[Span]:
    """Last resort for one sentence longer than `max_tokens`: cut every `max_tokens`
    tokens, at a word boundary."""
    start, end = span
    out = []
    while tokens.count(text[start:end]) > max_tokens:
        cut = start + tokens.head_end(text[start:end], max_tokens)
        if cut < end and not text[cut].isspace():  # inside a word: back to its start
            cut = max(_snap_back(text, cut, start), start + 1)
        out.append((start, cut))
        start = _snap_forward(text, cut, end)
    out.append((start, end))
    return out


def _snap_back(text: str, i: int, floor: int) -> int:
    j = i
    while j > floor and not text[j - 1].isspace():
        j -= 1
    return j if j > floor else i


def semantic_segments(
    sizes: Sequence[int],
    sims: Sequence[float],
    *,
    threshold: float,
    max_tokens: int,
    min_tokens: int,
) -> list[tuple[int, int]]:
    """Group consecutive sentences into segments (first, last+1).

    `sizes[i]` = tokens of sentence i; `sims[i]` = similarity between sentence i and i+1.
    1. Cut wherever similarity falls below `threshold` (a topic change), if both sides
       keep at least `min_tokens`.
    2. A segment still over `max_tokens` is cut at its weakest boundary, recursively.
    Sizes are summed per sentence (a close approximation of the joined text's count).
    """
    n = len(sizes)
    if n == 0:
        return []
    prefix = [0]  # prefix[k] = tokens of sentences 0..k-1, so size(a, b) = prefix[b] - prefix[a]
    for s in sizes:
        prefix.append(prefix[-1] + s)

    def size(a: int, b: int) -> int:
        return prefix[b] - prefix[a]

    cuts, last = [], 0
    for i, sim in enumerate(sims):  # boundary between sentence i and i+1
        if sim < threshold and size(last, i + 1) >= min_tokens and size(i + 1, n) >= min_tokens:
            cuts.append(i + 1)
            last = i + 1
    bounds = [0, *cuts, n]
    segments = list(zip(bounds, bounds[1:], strict=False))

    def fit(a: int, b: int) -> list[tuple[int, int]]:
        if size(a, b) <= max_tokens or b - a == 1:
            return [(a, b)]
        inner = range(a + 1, b)  # cut before sentence k
        balanced = [k for k in inner if size(a, k) >= min_tokens and size(k, b) >= min_tokens]
        k = min(balanced or inner, key=lambda k: sims[k - 1])
        return fit(a, k) + fit(k, b)

    # 3. A forced cut can leave a tiny group (e.g. a lone "("): merge it back if it fits.
    out: list[tuple[int, int]] = []
    for a, b in (part for seg in segments for part in fit(*seg)):
        if (
            out
            and min(size(*out[-1]), size(a, b)) < min_tokens
            and size(out[-1][0], b) <= max_tokens
        ):
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def semantic_split(
    text: str,
    sentences: list[Span],
    tokens: Tokens,
    similarities: Similarities,
    *,
    threshold: float,
    max_tokens: int,
    min_tokens: int,
    overlap: int,
) -> tuple[list[Span], list[dict[str, object]]]:
    """Split `text` (already known to be too long) into spans of at most `max_tokens`.

    Each new span starts `overlap` tokens before its cut (only on these semantic cuts),
    so the room for new text is `max_tokens - overlap`. Also returns one record per cut
    (similarity, the sentences on both sides) for inspection.
    """
    pieces: list[Span] = []
    for span in sentences:  # a single sentence over the limit is hard-split first
        if tokens.count(text[span[0] : span[1]]) > max_tokens - overlap:
            pieces.extend(hard_split(text, span, tokens, max_tokens - overlap))
        else:
            pieces.append(span)
    if len(pieces) == 1:
        return pieces, []
    sizes = [tokens.count(text[s:e]) for s, e in pieces]
    sims = similarities([text[s:e] for s, e in pieces])
    groups = semantic_segments(sizes, sims, threshold=threshold,
                               max_tokens=max_tokens - overlap, min_tokens=min_tokens)  # fmt: skip
    spans: list[Span] = []
    cuts: list[dict[str, object]] = []
    for g, (a, b) in enumerate(groups):
        start, end = pieces[a][0], pieces[b - 1][1]
        if g > 0:
            cuts.append({
                "similarity": round(sims[a - 1], 4),
                "below_threshold": sims[a - 1] < threshold,  # False = forced by the size
                "before": text[pieces[a - 1][0] : pieces[a - 1][1]],
                "after": text[pieces[a][0] : pieces[a][1]],
            })  # fmt: skip
            if overlap > 0:  # restart `overlap` tokens before the cut, at a word start
                prev_start = spans[-1][0]
                back = prev_start + tokens.tail_start(text[prev_start:start], overlap)
                start = _snap_forward(text, back, start)
        spans.extend(_fit(text, (start, end), pieces[a][0], tokens, max_tokens))
    return spans, cuts


def _fit(text: str, span: Span, cut: int, tokens: Tokens, max_tokens: int) -> list[Span]:
    """Final recount of the joined text. Group sizes are sums of per-sentence counts,
    which can be a few tokens off the count of the joined text: if the span is over
    `max_tokens`, shorten its overlap (the part before `cut`) first, then hard-split."""
    start, end = span
    if tokens.count(text[start:end]) <= max_tokens:
        return [span]
    while start < cut and tokens.count(text[start:end]) > max_tokens:
        start = _snap_forward(text, start + 1, cut)
    if tokens.count(text[start:end]) <= max_tokens:
        return [(start, end)]
    return hard_split(text, (start, end), tokens, max_tokens)


# ---------- recursive splitting (comparison strategy) ----------

RECURSIVE_SEPARATORS = ["\n\n", "\n", ". ", " "]


def _split_on(text: str, span: Span, sep: str) -> list[Span]:
    """Split a span after each separator (the separator stays with the left part)."""
    start, end = span
    out, pos = [], start
    while True:
        i = text.find(sep, pos, end)
        if i < 0:
            break
        out.append((pos, i + len(sep)))
        pos = i + len(sep)
    if pos < end:
        out.append((pos, end))
    return out


def _atoms(text: str, span: Span, tokens: Tokens, max_tokens: int, seps: list[str]) -> list[Span]:
    if tokens.count(text[span[0] : span[1]]) <= max_tokens:
        return [span]
    for k, sep in enumerate(seps):
        parts = _split_on(text, span, sep)
        if len(parts) > 1:
            return [a for p in parts for a in _atoms(text, p, tokens, max_tokens, seps[k + 1 :])]
    return hard_split(text, span, tokens, max_tokens)


def recursive_split(text: str, tokens: Tokens, *, max_tokens: int, overlap: int) -> list[Span]:
    """Classic recursive splitter: try paragraph breaks, then lines, then sentences, then
    words, so every piece fits; then pack pieces into chunks of at most `max_tokens`,
    each chunk starting with the last pieces (at most `overlap` tokens) of the previous one.
    It knows nothing about articles: that is what it is compared against."""
    atoms = [a for a in _atoms(text, (0, len(text)), tokens, max_tokens, RECURSIVE_SEPARATORS)
             if text[a[0] : a[1]].strip()]  # fmt: skip
    sizes = [tokens.count(text[s:e]) for s, e in atoms]
    chunks: list[Span] = []
    i = 0
    while i < len(atoms):
        j, size = i, 0
        while j < len(atoms) and (j == i or size + sizes[j] <= max_tokens):
            size += sizes[j]
            j += 1
        chunks.append((atoms[i][0], atoms[j - 1][1]))
        if j >= len(atoms):
            break
        k, back = j, 0  # next chunk restarts with up to `overlap` tokens of this one
        while k - 1 > i and back + sizes[k - 1] <= overlap:
            k -= 1
            back += sizes[k]
        i = k
    return [_strip(text, c) for c in chunks]


def _strip(text: str, span: Span) -> Span:
    s, e = span
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return (s, e)
