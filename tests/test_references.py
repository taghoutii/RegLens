"""Article references by regex, on sentences copied from the corpus."""

from reglens.ingestion.references import act_number_from_url, find_references, most_cited

ACTS = {"2022/2554": "DORA", "2024/1772": "RTS 2024/1772", "2025/301": "RTS 2025/301",
        "2009/110": "EMD2"}  # fmt: skip


def _refs(text: str, own: str | None = "RTS2025/301") -> list[str]:
    return find_references(text, own=own, acts=ACTS)


def test_external_act_in_english_and_french() -> None:
    assert _refs("pursuant to Article 19(4), point (a), of Regulation (EU) 2022/2554 shall") == [
        "DORA:19"
    ]
    assert _refs("visée à l'article 19, paragraphe 4, point a), du règlement (UE) 2022/2554") == [
        "DORA:19"
    ]


def test_ranges_lists_and_premier() -> None:
    assert _refs("Articles 1 to 3 of Delegated Regulation (EU) 2024/1772 have been") == [
        "RTS2024/1772:1", "RTS2024/1772:2", "RTS2024/1772:3",
    ]  # fmt: skip
    assert _refs("articles 1 er à 2 du règlement délégué (UE) 2024/1772") == [
        "RTS2024/1772:1", "RTS2024/1772:2",
    ]  # fmt: skip
    assert _refs("as laid down in Articles 17 and 18, the entity") == [
        "RTS2025/301:17", "RTS2025/301:18",
    ]  # fmt: skip


def test_own_regulation_unknown_act_and_no_false_link() -> None:
    assert _refs("referred to in Article 5 shall") == ["RTS2025/301:5"]
    assert _refs("within the meaning of Article 3 of Directive (EU) 2022/2555") == ["2022/2555:3"]
    # the act is not linked to the article by "of": the article is this regulation's own
    assert _refs("Under Article 5, entities apply Regulation (EU) 2022/2554") == ["RTS2025/301:5"]
    assert _refs("Article 5 applies.", own=None) == []


def test_old_style_directive_citations() -> None:
    # how PSD2 cites the E-Money Directive (EN and FR)
    assert _refs("within the meaning of point (1) of Article 2 of Directive 2009/110/EC, incl") == [
        "EMD2:2"
    ]
    assert _refs("au sens de l'article 2, point 1), de la directive 2009/110/CE, y compris") == [
        "EMD2:2"
    ]
    assert _refs("Article 28 of Directive 95/46/EC shall") == ["95/46:28"]  # not in the corpus
    # a list of directives after the article: the first one is its act
    assert _refs("Article 3 of Directives 2002/65/EC, 2009/110/EC and 2013/36/EU") == ["2002/65:3"]
    # the new style is unchanged, and a number inside a date or path is not an act
    assert _refs("Article 4 of Directive (EU) 2015/2366") == ["2015/2366:4"]
    assert _refs("Article 9 of 12/03/2024/EC-form") == ["RTS2025/301:9"]


def test_headings_are_not_references() -> None:
    assert _refs("Article 19 — Reporting of major incidents") == []
    assert _refs("Article premier — Informations générales") == []
    assert _refs("Intro\nArticle 4\nmore text") == []


def test_helpers() -> None:
    assert act_number_from_url("https://x/?uri=CELEX:32025R0301") == "2025/301"
    text = "Applies DORA and Regulation (EU) 2022/2554; also RTS 2024/1772."
    names = [(s, n) for n, s in ACTS.items()]
    assert most_cited(text, names) == "DORA"
