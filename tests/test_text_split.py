"""Sentence repair, semantic segments and the recursive splitter, with stub tools."""

from reglens.ingestion.text_split import (
    _fit,
    hard_split,
    recursive_split,
    repair_sentences,
    semantic_segments,
    semantic_split,
    sentence_spans,
)
from tests.chunk_stubs import WordTokens, first_word_similarity, regex_sentences


def _texts(text: str, spans: list[tuple[int, int]]) -> list[str]:
    return [text[s:e] for s, e in spans]


def test_abbreviations_and_labels_do_not_end_a_sentence() -> None:
    text = "See Art. 5 of the act. Then No. 3 applies. 1. Entities shall report."
    spans = sentence_spans(text, regex_sentences)
    assert _texts(text, spans) == [
        "See Art. 5 of the act.", "Then No. 3 applies.", "1. Entities shall report.",
    ]  # fmt: skip


def test_each_point_line_is_its_own_sentence() -> None:
    text = "The report contains:\n(a) the date;\n(b) the cause."
    assert _texts(text, sentence_spans(text, regex_sentences)) == [
        "The report contains:", "(a) the date;", "(b) the cause.",
    ]  # fmt: skip


def test_repair_french_cuts_seen_from_spacy() -> None:
    # spaCy's own cuts on this sentence: after "l’art.", after "point a)", after "Cf."
    text = "Voir l’art. 5, point a), du règlement. Cf. annexe II."
    cuts = [(0, 11), (12, 23), (23, 38), (39, 42), (43, 53)]
    assert _texts(text, repair_sentences(text, cuts)) == [
        "Voir l’art. 5, point a), du règlement.", "Cf. annexe II.",
    ]  # fmt: skip


def test_repair_corpus_fragments() -> None:
    # Cuts seen in the first corpus run (results/archive/2026-11-21_chunking_stats.json)
    cases = [
        ("(3) paragraphs are replaced: ‘3. For services, a CSD shall act.",
         [(0, 28), (29, 32), (33, 63)],
         ["(3) paragraphs are replaced:", "‘3. For services, a CSD shall act."]),
        ("1.2. Name of the entity.", [(0, 4), (5, 24)], ["1.2. Name of the entity."]),
        ("as defined in Regulation (EC) No 1060/2009; next.", [(0, 29), (30, 49)],
         ["as defined in Regulation (EC) No 1060/2009; next."]),
        ("of that act ( * ) . New text.", [(0, 11), (12, 19), (20, 29)],
         ["of that act ( * ) .", "New text."]),
        ("( (110) La Commission.", [(0, 1), (2, 22)], ["( (110) La Commission."]),
    ]  # fmt: skip
    for text, cuts, expected in cases:
        assert _texts(text, repair_sentences(text, cuts)) == expected, text


def test_table_rows_are_not_split() -> None:
    text = "1.2. | Name of the entity | Yes. Full legal name."
    assert _texts(text, sentence_spans(text, regex_sentences)) == [text]


def test_repair_keeps_normal_boundaries() -> None:
    text = "One. Two."
    assert repair_sentences(text, [(0, 4), (5, 9)]) == [(0, 4), (5, 9)]


def test_segments_cut_at_topic_change_and_respect_sizes() -> None:
    sizes = [50, 60, 70, 80, 40]
    sims = [0.9, 0.1, 0.9, 0.9]  # topic change between sentence 1 and 2
    assert semantic_segments(sizes, sims, threshold=0.5, max_tokens=300, min_tokens=100) == [
        (0, 2), (2, 5),
    ]  # fmt: skip
    # a cut that would leave a piece under min_tokens is not made
    assert semantic_segments([50, 60], [0.1], threshold=0.5, max_tokens=300, min_tokens=100) == [
        (0, 2)
    ]
    # too long without any topic change: forced cut at the weakest boundary
    sizes, sims = [100] * 6, [0.9, 0.8, 0.6, 0.8, 0.9]
    assert semantic_segments(sizes, sims, threshold=0.5, max_tokens=300, min_tokens=100) == [
        (0, 3), (3, 6),
    ]  # fmt: skip


def test_semantic_split_overlaps_only_on_cuts() -> None:
    words = [f"Alpha w{i}." for i in range(30)] + [f"Beta w{i}." for i in range(30)]
    text = " ".join(words)  # 120 tokens, topic change in the middle
    sentences = sentence_spans(text, regex_sentences)
    spans, cuts = semantic_split(text, sentences, WordTokens(), first_word_similarity,
                                 threshold=0.5, max_tokens=80, min_tokens=20,
                                 overlap=4)  # fmt: skip
    first, second = _texts(text, spans)
    assert first.endswith("Alpha w29.") and second.startswith("Alpha w28.")  # 4 words back
    assert len(cuts) == 1 and cuts[0]["below_threshold"] is True
    assert all(WordTokens().count(t) <= 80 for t in (first, second))


def test_final_recount_shortens_the_overlap_first() -> None:
    text = " ".join(f"w{i}" for i in range(12))
    cut = text.index("w4")  # w0..w3 = overlap repeated from the previous span
    (span,) = _fit(text, (0, len(text)), cut, WordTokens(), 10)
    assert text[span[0] : span[1]].split()[0] == "w2"  # 2 overlap words dropped, no new cut
    spans = _fit(text, (cut, len(text)), cut, WordTokens(), 5)  # no overlap left: hard split
    assert [WordTokens().count(t) for t in _texts(text, spans)] == [5, 3]


def test_hard_split_cuts_at_words() -> None:
    text = " ".join(f"w{i}" for i in range(25))
    spans = hard_split(text, (0, len(text)), WordTokens(), 10)
    assert [WordTokens().count(t) for t in _texts(text, spans)] == [10, 10, 5]


def test_recursive_split_fits_and_overlaps() -> None:
    para = " ".join(f"w{i}." for i in range(30))
    text = "\n\n".join([para, para, para])  # 3 x 30 tokens
    spans = recursive_split(text, WordTokens(), max_tokens=40, overlap=5)
    chunks = _texts(text, spans)
    assert all(WordTokens().count(c) <= 40 for c in chunks)
    assert len(chunks) >= 3
    assert chunks[1].split()[0] in chunks[0]  # the next chunk restarts inside the previous one
