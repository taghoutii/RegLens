"""PII and secret masking with Microsoft Presidio.

Which detector finds what:
  PERSON         spaCy NER plus a *context* recognizer: the word right
                 after a label that always introduces a person ("Owner:", "Deputy:",
                 "Responsable :", "Suppléant :", "Contact person:",
                 "On-call <name> <e-mail>").
                 Spans that cannot be a name are dropped: no lowercase letter
                 (acronyms "RWEA"), digits or "|" ("2024/1620", "| Oui"), or only legal
                 structure words ("Chapter III"). NER mistakes them for names.
                 The register's "Provider name:" field holds a company name, never a
                 person: PERSON spans inside it are dropped ("Keystone Identity").
  EMAIL_ADDRESS  a custom regex. Presidio's built-in recognizer also checks the domain
                 against the public suffix list, which rejects internal domains
                 (".corp", ".internal") and the corpus's reserved ".example" domain.
  IBAN_CODE      regex + IBAN checksum
  PHONE_NUMBER   two detectors: (a) the `phonenumbers` library in strict "VALID" mode,
                 (b) a custom pattern for
                 the UK and French *formats* (leading 0 or +44/+33, 9-10 national digits)
                 The lenient "POSSIBLE" mode is not used: it flagged "2022/2554" and "2024" in the
                 regulations.
  API_KEY        a custom regex for the 3 key shapes used in the corpus
"""

import logging
import re
from collections import Counter
from typing import Any

from presidio_analyzer import (
    AnalyzerEngine,
    BatchAnalyzerEngine,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    IbanRecognizer,
    PhoneRecognizer,
    SpacyRecognizer,
)
from presidio_anonymizer import AnonymizerEngine

from reglens.config import Settings
from reglens.corpus.sources import Language
from reglens.ingestion.models import Block, ParsedDocument

logger = logging.getLogger(__name__)

ENTITIES = ["PERSON", "EMAIL_ADDRESS", "IBAN_CODE", "PHONE_NUMBER", "API_KEY"]

# The three secret shapes planted in the synthetic policies (see corpus/synthetic.py).
API_KEY_PATTERNS = [
    Pattern("internal_live_key", r"\bvlr_live_[A-Za-z0-9]{32}\b", 0.95),
    Pattern("aws_access_key_id", r"\bAKIA[0-9A-Z]{16}\b", 0.95),
    Pattern("hex_token", r"\btok_[0-9a-f]{40}\b", 0.95),
]
EMAIL_PATTERNS = [Pattern("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b", 0.9)]

# ---- phone numbers by national format (UK, FR)
PHONE_LENIENCY_VALID = 1  # phonenumbers.Leniency: 0 POSSIBLE, 1 VALID (Presidio default)
_NO_DIGIT_BEFORE = r"(?<![\w+/.,])"  # not inside "2022/2554", "1.234.567" or a word
_NO_DIGIT_AFTER = r"(?![\w/])"
PHONE_PATTERNS = [
    # UK: 0 or +44 [(0)], then digits with spaces/brackets: "(0115) 496 0657", "+44(0)306 999 0691"
    Pattern(
        "uk_phone_format",
        _NO_DIGIT_BEFORE + r"(?:\+44\s?(?:\(0\)\s?)?|\(?0)\d[\d ()]{7,13}\d" + _NO_DIGIT_AFTER,
        0.7,
    ),
    # FR: 0 or +33 [(0)], then 9 digits in pairs: "01 69 95 00 76", "+33 (0)6 31 97 90 86"
    Pattern(
        "fr_phone_format",
        _NO_DIGIT_BEFORE + r"(?:\+33\s?(?:\(0\)\s?)?|0)[1-9](?:[ .-]?\d{2}){4}" + _NO_DIGIT_AFTER,
        0.7,
    ),
]


class NationalPhoneRecognizer(PatternRecognizer):
    """UK/FR phone formats; a match is kept only if it has 9-10 national digits."""

    def validate_result(self, pattern_text: str) -> bool | None:
        digits = re.sub(r"\D", "", pattern_text)
        text = pattern_text.lstrip()
        if text.startswith("+"):
            digits = digits[2:]  # country code 44 / 33
            if "(0)" in text:
                digits = digits[1:]
        elif digits.startswith("0"):
            digits = digits[1:]  # national trunk prefix
        else:
            return False
        return 9 <= len(digits) <= 10


# ---- names introduced by a label (context recognizer)
_WORD = r"\p{Lu}[\p{L}'’]*(?:-\p{L}+)*\.?"  # "Anaïs", "Cross-Berry", "Dr", "M."
_PARTICLES = r"(?:(?:de|du|des|la|le|van|von|der|den|di|da)\s+|d['’])*"  # "de la", "d'"
NAME = rf"{_WORD}(?:\s+{_PARTICLES}{_WORD}){{0,4}}"
NAME_CONTEXT_PATTERNS = [
    # "Owner: <name> (", "Deputy: <name>,", "Responsable : <name>", "Suppléant : <name>",
    # register: "Contact person: <name>,"
    Pattern(
        "labelled_name",
        rf"(?<=\b(?:Owner|Deputy|Responsable|Suppléant|Contact person)\s?:\s+){NAME}",
        0.85,
    ),
    # incident log: "On-call <name> <e-mail> acknowledged the page"
    Pattern("on_call_name", rf"(?<=\bOn-call\s+){NAME}(?=\s*<)", 0.85),
]

# ---- PERSON spans that are only legal structure ("Chapter III", "Annex", "Titre II")
LEGAL_STRUCTURE_WORDS = {
    "article", "articles", "chapter", "chapters", "section", "sections", "annex",
    "annexes", "title", "part", "paragraph", "point", "points", "recital", "recitals",
    "subparagraph", "regulation", "directive", "appendix", "appendices",
    "chapitre", "chapitres", "annexe", "titre", "partie", "paragraphe",
    "considérant", "considérants", "alinéa", "règlement", "appendice",
}  # fmt: skip
_ROMAN_OR_NUMBER = re.compile(r"^(?:[IVXLC]+|\d+[a-z]?|[A-Za-z])$")  # "III", "19a", "B"


def is_legal_structure(span: str) -> bool:
    """True when every word of the span is a structure word, a Roman numeral or a number."""
    words = [w for w in re.split(r"[\s().,;:]+", span) if w]
    return bool(words) and all(
        w.lower() in LEGAL_STRUCTURE_WORDS or _ROMAN_OR_NUMBER.match(w) for w in words
    )


def cannot_be_a_name(span: str) -> bool:
    """PERSON spans rejected by shape: a single all-caps token (an acronym: "RWEA",
    "CRF"), an all-caps span containing a structure word ("CHAPTER III HIGH-RISK"),
    digits or "|" ("2024/1620", "| Oui"), or only legal structure words ("Chapter III",
    "Appendix B"). Other multi-word all-caps spans ("JOHN SMITH") stay masked: names are
    sometimes written in capitals."""
    capitals = not any(ch.islower() for ch in span)
    acronym = capitals and len(span.split()) == 1
    # an all-caps heading with a structure word: "CHAPTER III HIGH-RISK"
    heading = capitals and any(
        w.lower() in LEGAL_STRUCTURE_WORDS for w in re.split(r"[\s().,;:]+", span)
    )
    return (
        acronym
        or heading
        or any(ch.isdigit() or ch == "|" for ch in span)
        or is_legal_structure(span)
    )


# Register field whose value is a company name (fields are separated by " | ").
_COMPANY_FIELD = re.compile(r"\bProvider name: ([^|]*)")


def in_company_field(text: str, start: int, end: int) -> bool:
    """True when text[start:end] lies inside the value of a "Provider name:" field."""
    return any(m.start(1) <= start and end <= m.end(1) for m in _COMPANY_FIELD.finditer(text))


# Log-record fields that are structure, not free text: never sent to the analyzer.
STRUCTURED_LOG_FIELDS = {
    "timestamp", "incident_id", "severity", "system", "owning_unit",
    "detected_at", "reported_at", "status", "event",
}  # fmt: skip


def build_analyzer(settings: Settings) -> AnalyzerEngine:
    """Presidio analyzer for English and French with only the recognizers the corpus needs."""
    languages = list(settings.pii_spacy_models)
    models = [{"lang_code": lang, "model_name": m} for lang, m in settings.pii_spacy_models.items()]
    nlp_config = {
        "nlp_engine_name": "spacy",
        "models": models,
        # spaCy EN says "PERSON", spaCy FR says "PER": both become Presidio's PERSON.
        "ner_model_configuration": {
            "model_to_presidio_entity_mapping": {"PERSON": "PERSON", "PER": "PERSON"},
            "labels_to_ignore": ["ORG", "LOC", "GPE", "MISC", "NORP", "FAC", "DATE", "TIME",
                                 "CARDINAL", "ORDINAL", "MONEY", "PERCENT", "QUANTITY",
                                 "LAW", "EVENT", "PRODUCT", "WORK_OF_ART", "LANGUAGE"],
        },
    }  # fmt: skip
    nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()

    registry = RecognizerRegistry(supported_languages=languages)
    for lang in languages:
        registry.add_recognizer(
            SpacyRecognizer(supported_language=lang, supported_entities=["PERSON"])
        )
        if settings.pii_context_names:
            registry.add_recognizer(
                PatternRecognizer(
                    supported_entity="PERSON",
                    name="LabelledNameRecognizer",
                    patterns=NAME_CONTEXT_PATTERNS,
                    supported_language=lang,
                    global_regex_flags=re.MULTILINE,  # case-sensitive: names are capitalised
                )
            )
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="EMAIL_ADDRESS", patterns=EMAIL_PATTERNS, supported_language=lang
            )
        )
        registry.add_recognizer(IbanRecognizer(supported_language=lang))
        registry.add_recognizer(
            PhoneRecognizer(
                supported_language=lang,
                supported_regions=settings.pii_phone_regions,
                leniency=PHONE_LENIENCY_VALID,
            )
        )
        registry.add_recognizer(
            NationalPhoneRecognizer(
                supported_entity="PHONE_NUMBER",
                name="NationalPhoneRecognizer",
                patterns=PHONE_PATTERNS,
                supported_language=lang,
            )
        )
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="API_KEY", patterns=API_KEY_PATTERNS, supported_language=lang
            )
        )
    return AnalyzerEngine(nlp_engine=nlp_engine, registry=registry, supported_languages=languages)


class PiiMasker:
    """Masks text and log records; counts what it masked (never logs the values)."""

    def __init__(self, settings: Settings) -> None:
        self.analyzer = build_analyzer(settings)
        self.batch = BatchAnalyzerEngine(analyzer_engine=self.analyzer)
        self.anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]  # untyped library
        self.threshold = settings.pii_score_threshold
        self.batch_size = settings.pii_batch_size

    def keeps(self, text: str, result: Any) -> bool:
        """False for a PERSON span that cannot be a name (see cannot_be_a_name) or that is
        a provider's company name in the register."""
        return not (
            result.entity_type == "PERSON"
            and (
                cannot_be_a_name(text[result.start : result.end])
                or in_company_field(text, result.start, result.end)
            )
        )

    def find_many(
        self, texts: list[str], language: Language, *, filtered: bool = True
    ) -> list[list[Any]]:
        """Detected spans (Presidio RecognizerResult objects) for each text, in one batch.
        filtered=False keeps the PERSON spans that cannot be names (for measurements)."""
        out: list[list[Any]] = [[] for _ in texts]
        todo = [i for i, t in enumerate(texts) if t.strip()]
        results = self.batch.analyze_iterator(
            [texts[i] for i in todo],
            language=language,
            batch_size=self.batch_size,
            entities=ENTITIES,
            score_threshold=self.threshold,
        )
        for i, res in zip(todo, results, strict=True):
            out[i] = [r for r in res if not filtered or self.keeps(texts[i], r)]
        return out

    def find(self, text: str, language: Language) -> list[Any]:
        return self.find_many([text], language)[0]

    def mask_many(self, texts: list[str], language: Language, counts: Counter[str]) -> list[str]:
        """Mask each text (<ENTITY_TYPE> placeholders). Each distinct text is analysed once;
        counts are per occurrence."""
        unique = list(dict.fromkeys(texts))
        masked: dict[str, str] = {}
        span_types: dict[str, list[str]] = {}
        for text, results in zip(unique, self.find_many(unique, language), strict=True):
            if not results:
                masked[text], span_types[text] = text, []
                continue
            anonymized = self.anonymizer.anonymize(text=text, analyzer_results=results)
            masked[text] = str(anonymized.text)
            # spans actually replaced (after overlap resolution)
            span_types[text] = [item.entity_type for item in anonymized.items]
        for text in texts:
            counts.update(span_types[text])
        return [masked[t] for t in texts]

    def mask(self, text: str, language: Language, counts: Counter[str]) -> str:
        return self.mask_many([text], language, counts)[0]

    def mask_document(self, doc: ParsedDocument) -> ParsedDocument:
        """Return a fully masked copy. Raw documents are never modified.

        Every string the document exposes is collected first, masked in one batch, then
        put back in place: `slots` remembers where each string came from."""
        slots, texts = collect_strings(doc)
        counts: Counter[str] = Counter()
        masked = self.mask_many(texts, doc.langue, counts)

        # Deep copies, so the raw document is left untouched.
        blocks: list[Block] = [b.model_copy(deep=True) for b in doc.blocks]
        title = doc.title
        for (i, field, pos), value in zip(slots, masked, strict=True):
            if field == "title":
                title = value
                continue
            b = blocks[i]
            if field == "text":
                b.text = value
            elif field == "section":
                b.section = value
            elif field == "record" and b.records is not None:
                b.records[pos[0]][pos[1]] = value
        logger.info("masked %s (%s): %s", doc.doc_id, doc.langue, dict(counts))
        return doc.model_copy(
            update={"blocks": blocks, "title": title, "masked": True, "pii_counts": dict(counts)}
        )


def collect_strings(doc: ParsedDocument) -> tuple[list[tuple[int, str, Any]], list[str]]:
    """Every string of a document that masking analyses, with where it came from:
    (block index or -1 for the title, field, position in the field)."""
    texts: list[str] = []
    slots: list[tuple[int, str, Any]] = []  # (block index, field, position in field)

    def add(i: int, field: str, pos: Any, value: str) -> None:
        slots.append((i, field, pos))
        texts.append(value)

    if doc.title:
        add(-1, "title", None, doc.title)
    for i, b in enumerate(doc.blocks):
        add(i, "text", None, b.text)
        if b.section:
            add(i, "section", None, b.section)
        for r, rec in enumerate(b.records or []):
            for key, value in rec.items():
                if isinstance(value, str) and key not in STRUCTURED_LOG_FIELDS:
                    add(i, "record", (r, key), value)
    return slots, texts
