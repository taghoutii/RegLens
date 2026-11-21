"""The regex/validation recognizers, tested alone: no spaCy model needed."""

import re

from presidio_analyzer import PatternRecognizer
from presidio_analyzer.predefined_recognizers import IbanRecognizer, PhoneRecognizer

from reglens.ingestion.pii import (
    API_KEY_PATTERNS,
    EMAIL_PATTERNS,
    NAME_CONTEXT_PATTERNS,
    PHONE_LENIENCY_VALID,
    PHONE_PATTERNS,
    NationalPhoneRecognizer,
    cannot_be_a_name,
    is_legal_structure,
)


def _spans(recognizer: object, text: str, entity: str) -> list[str]:
    results = recognizer.analyze(text=text, entities=[entity], nlp_artifacts=None)  # type: ignore[attr-defined]
    return [text[r.start : r.end] for r in results]


def test_three_api_key_shapes() -> None:
    rec = PatternRecognizer(supported_entity="API_KEY", patterns=API_KEY_PATTERNS)
    text = (
        "keys: `vlr_live_" + "a1B2" * 8 + "`, AKIA" + "ABCDEFGH12345678" + " and tok_" + "0f" * 20
    )
    assert len(_spans(rec, text, "API_KEY")) == 3
    assert _spans(rec, "the word token and AKIA alone", "API_KEY") == []


def test_email_on_a_reserved_domain() -> None:
    """Presidio's built-in recognizer rejects ".example"; ours must not."""
    rec = PatternRecognizer(supported_entity="EMAIL_ADDRESS", patterns=EMAIL_PATTERNS)
    text = "Owner: a.b-c@velora-fintech.example, (029)2018819."
    assert _spans(rec, text, "EMAIL_ADDRESS") == ["a.b-c@velora-fintech.example"]


def test_iban_checksum() -> None:
    rec = IbanRecognizer()
    assert _spans(rec, "IBAN GB82WEST12345698765432 ok", "IBAN_CODE") == ["GB82WEST12345698765432"]
    assert _spans(rec, "IBAN GB00WEST12345698765432 bad", "IBAN_CODE") == []  # wrong checksum


def _national_phones(text: str) -> list[str]:
    rec = NationalPhoneRecognizer(supported_entity="PHONE_NUMBER", patterns=PHONE_PATTERNS)
    return [s.strip() for s in _spans(rec, text, "PHONE_NUMBER")]


def test_uk_and_fr_phone_formats_are_found() -> None:
    """Faker uses UK ranges reserved for fiction: the strict library rejects them, so the
    format pattern must catch them."""
    numbers = ["01632 960031", "(0115) 496 0657", "+441154960782", "+44(0)306 999 0691",
               "(029)2018819", "01 23 45 67 89", "+33 (0)6 31 97 90 86", "0245371867"]  # fmt: skip
    for number in numbers:
        assert _national_phones(f"call {number} now") == [number], number


def test_regulation_numbers_and_years_are_not_phones() -> None:
    text = ("Regulation (EU) 2022/2554 and (EU) No 1095/2010 apply from 17 January 2025; "
            "Directive 2015/2366, OJ L 333, 27.12.2022, p. 1; EUR 1 000 000; 2024.")  # fmt: skip
    assert _national_phones(text) == []
    strict = PhoneRecognizer(supported_regions=["GB", "FR"], leniency=PHONE_LENIENCY_VALID)
    assert _spans(strict, text, "PHONE_NUMBER") == []


def _names(text: str) -> list[str]:
    rec = PatternRecognizer(supported_entity="PERSON", patterns=NAME_CONTEXT_PATTERNS,
                            global_regex_flags=re.MULTILINE)  # fmt: skip
    return _spans(rec, text, "PERSON")


def test_labelled_names() -> None:
    assert _names("Owner: Amelia Thomson (Head of ICT Risk), a@b.example.") == ["Amelia Thomson"]
    assert _names("Deputy: Nathan Cross-Berry, n@b.example.") == ["Nathan Cross-Berry"]
    assert _names("Responsable : Anaïs de la Bailly (DPO)") == ["Anaïs de la Bailly"]
    name = "Océane-Josette Valentin"
    assert _names(f"Suppléant : {name}, o@b.example.") == [name]
    assert _names("On-call Bethan Reeves <b@b.example> acknowledged the page") == ["Bethan Reeves"]


def test_labels_without_a_name() -> None:
    assert _names("The owner: see section 3. On-call rota is weekly.") == []
    assert _names("Responsable : la direction des risques") == []


def test_legal_structure_spans() -> None:
    for span in ["Chapter III", "Annex", "Annex III", "Article 19", "Titre II", "Chapitre IV"]:
        assert is_legal_structure(span), span
    for span in ["Amelia Thomson", "Annex Smith", "Valentin"]:
        assert not is_legal_structure(span), span


def test_spans_that_cannot_be_names() -> None:
    for span in ["RWEA", "CRF", "2024/1620", "| Oui", "EU-7", "Chapter III", "JON",
                 "Appendix B", "Appendice C", "ANNEX III", "CHAPTER III HIGH-RISK",
                 "CHAPITRE II DISPOSITIONS"]:  # fmt: skip
        assert cannot_be_a_name(span), span
    for span in ["Amelia Thomson", "Anaïs de la Bailly", "Nathan Cross-Berry", "Dr Lee Green",
                 "AMELIA THOMSON", "Roberta METSOLA", "PARTNER SMITH"]:  # fmt: skip
        assert not cannot_be_a_name(span), span
