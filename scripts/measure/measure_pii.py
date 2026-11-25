"""Measure PII masking: recall on every planted value, false positives on regulations.

Usage (repository root, virtual environment active):
    python scripts/measure/measure_pii.py                 # all policies, the log, all regulations
    python scripts/measure/measure_pii.py --show-failures # also print the leaked values locally
    python scripts/measure/measure_pii.py --holdout-seeds 43 44

Recall is measured on the corpus's synthetic documents (data/raw/, seed 42) and on
held-out sets generated with other seeds (default 43) in a temporary folder, deleted
afterwards: the recognizers were built on seed 42, so the held-out seeds show whether
they generalise to other names and values.

Also scans the regulations and the reports already in the Docling cache: they hold no
planted PII, so every detection there is a candidate false positive.

Writes results/<date>_pii_masking.json (counts and ids only, never a planted value)
and appends one row to results/EXPERIMENTS.md. Writes nothing to data/processed/.
"""

import argparse
import logging
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from docling_core.types.doc.document import DoclingDocument

from reglens.config import get_settings
from reglens.corpus.sources import load_corpus
from reglens.corpus.synthetic import generate_all
from reglens.evaluation.pii_leaks import check_leaks, load_planted
from reglens.evaluation.results import append_row, write_result
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.parse_eurlex import parse_eurlex
from reglens.ingestion.parse_pdf import cache_paths, from_docling
from reglens.ingestion.parse_synthetic import parse_incident_log, parse_policy
from reglens.ingestion.pii import PiiMasker, collect_strings


@dataclass
class Scan:
    """Detections on documents without planted PII (candidate false positives)."""

    documents: int = 0
    chars: int = 0
    kept: Counter[str] = field(default_factory=Counter)  # entity type -> spans masked
    examples: Counter[tuple[str, str]] = field(default_factory=Counter)
    dropped: Counter[str] = field(default_factory=Counter)  # PERSON spans removed by filter
    # Masked PERSON spans written in capitals with several words: names or not? (to review)
    caps_multiword: Counter[str] = field(default_factory=Counter)

    @property
    def dropped_n(self) -> int:
        return sum(self.dropped.values())

    def add(self, masker: PiiMasker, doc: ParsedDocument) -> None:
        texts = collect_strings(doc)[1]
        self.documents += 1
        self.chars += sum(len(t) for t in texts)
        raw = masker.find_many(texts, doc.langue, filtered=False)
        for text, results in zip(texts, raw, strict=True):
            for r in results:
                span = text[r.start : r.end]
                if masker.keeps(text, r):
                    self.kept[r.entity_type] += 1
                    self.examples[(r.entity_type, span)] += 1
                    if (r.entity_type == "PERSON" and len(span.split()) > 1
                            and not any(ch.islower() for ch in span)):  # fmt: skip
                        self.caps_multiword[" ".join(span.split())] += 1
                else:
                    self.dropped[span] += 1

    def summary(self, n_examples: int) -> dict[str, Any]:
        out: dict[str, Any] = {
            "documents": self.documents,
            "chars_scanned": self.chars,
            "detections_by_type": dict(self.kept),
            "dropped_by_person_filter": self.dropped_n,
            "caps_multiword_masked": sum(self.caps_multiword.values()),
        }
        if n_examples:
            out["top_examples"] = [
                {"entity": e, "text": t, "count": c}
                for (e, t), c in self.examples.most_common(n_examples)
            ]
            out["caps_multiword_masked_spans"] = dict(self.caps_multiword.most_common())
            out["top_dropped"] = [
                {"text": t, "count": c} for t, c in self.dropped.most_common(n_examples)
            ]
        return out


def mask_synthetic(masker: PiiMasker, raw: Path) -> dict[str, ParsedDocument]:
    """The policies and the log of `raw`, parsed and masked as the pipeline does."""
    docs: dict[str, ParsedDocument] = {}
    for f in sorted((raw / "policies").glob("POL-*.md")):
        d = masker.mask_document(parse_policy(f, source=f.as_posix()))
        docs[d.doc_id] = d
    log_file = raw / "logs" / "incident_log.jsonl"
    docs["incident_log"] = masker.mask_document(
        parse_incident_log(log_file, source=log_file.as_posix())
    )
    return docs


def holdout_recall(masker: PiiMasker, seed: int) -> dict[str, Any]:
    """Recall on a synthetic set generated with `seed` in a temporary folder."""
    with tempfile.TemporaryDirectory() as tmp:
        raw, truth = Path(tmp) / "raw", Path(tmp) / "ground_truth"
        generate_all(raw, truth, seed=seed)
        report = check_leaks(mask_synthetic(masker, raw), load_planted(truth))
    summary = report.summary()
    return {"seed": seed, "planted": summary["planted"], "leaked": summary["leaked"],
            "recall": summary["recall"]}  # fmt: skip


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--show-failures", action="store_true", help="print leaked values (local)")
    parser.add_argument("--fp-examples", type=int, default=25, help="false-positive examples kept")
    parser.add_argument("--holdout-seeds", type=int, nargs="*", default=[43],
                        help="also measure recall on synthetic sets of these seeds")  # fmt: skip
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    t0 = time.perf_counter()
    masker = PiiMasker(settings)
    load_s = time.perf_counter() - t0

    # ---- synthetic documents: recall against the ground truth
    raw = settings.raw_dir
    t0 = time.perf_counter()
    docs = mask_synthetic(masker, raw)
    synthetic_s = time.perf_counter() - t0
    report = check_leaks(docs, load_planted(settings.ground_truth_dir))
    holdout = {seed: holdout_recall(masker, seed) for seed in args.holdout_seeds}

    # ---- regulations and reports: no planted PII, so every detection is a candidate false
    # positive. "dropped" = PERSON spans removed by the shape filter (cannot be a name).
    corpus = load_corpus(settings.sources_file)
    scans: dict[str, Scan] = {"regulations": Scan(), "reports": Scan()}
    t0 = time.perf_counter()
    for src in corpus.regulations:
        for lang in ("en", "fr"):
            path = raw / "regulations" / f"{src.id}_{lang}.html"
            if path.exists():
                doc = parse_eurlex(path, doc_id=src.id, langue=lang, reglement=src.short_name,
                                   source=path.as_posix())  # fmt: skip
                scans["regulations"].add(masker, doc)
    regulations_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    for src in corpus.reports:
        doc_file = cache_paths(settings.docling_cache_dir, src.id)[0]
        if doc_file.exists():
            doc = from_docling(DoclingDocument.load_from_json(doc_file), doc_id=src.id,
                               langue=src.language or "en", source=src.id,
                               max_header_rows=settings.table_max_inferred_header_rows)  # fmt: skip
            scans["reports"].add(masker, doc)
    reports_s = time.perf_counter() - t0

    summary = report.summary()
    result = {
        "name": "pii_masking",
        "date": date.today().isoformat(),
        "config": {
            "spacy_models": settings.pii_spacy_models,
            "score_threshold": settings.pii_score_threshold,
            "phone_regions": settings.pii_phone_regions,
            "phone_detection": "phonenumbers VALID + UK/FR format pattern",
            "context_names": settings.pii_context_names,
            "person_filter": "drop PERSON spans with no lowercase letter, with digits or '|', "
            "or made only of legal structure words",
            "batch_size": settings.pii_batch_size,
        },
        "synthetic": {"seed": settings.synthetic_seed, **summary},
        "holdout": [holdout[seed] for seed in args.holdout_seeds],
        # Regulations are public texts: what was flagged there is shown. Reports may name
        # real people (board members), so only counts are written for them.
        "regulations_false_positives": scans["regulations"].summary(args.fp_examples),
        "reports_false_positives": scans["reports"].summary(0),
        "timing_s": {
            "analyzer_load": round(load_s, 1),
            "synthetic_parse_and_mask": round(synthetic_s, 1),
            "regulations_scan": round(regulations_s, 1),
            "reports_scan": round(reports_s, 1),
        },
    }
    out = write_result(settings.results_dir, result)

    recall = summary["recall"]
    row = (
        f"| {result['date']} | pii_masking | 2 | Presidio recall on planted values; "
        f"detections on regulations and {scans['reports'].documents} cached reports | "
        f"recall {recall} ; leaks {sum(report.leaked.values())} ; "
        f"held-out seeds {[(h['seed'], h['recall']) for h in holdout.values()]} ; "
        f"regulation detections {dict(scans['regulations'].kept)} ; "
        f"report detections {dict(scans['reports'].kept)} | `{out.as_posix()}` |\n"
    )
    append_row(settings.results_dir, row)

    print(f"planted: {summary['planted']}")
    print(f"leaked:  {summary['leaked']}")
    print(f"recall:  {recall}")
    for h in holdout.values():
        print(f"seed {h['seed']}: recall {h['recall']}, leaked {h['leaked']}")
    for name, scan in scans.items():
        print(f"{name}: kept {dict(scan.kept)}, dropped by the PERSON filter {scan.dropped_n}")
        for (entity, text), count in scan.examples.most_common(10):  # local console only
            print(f"   kept    {entity:<14} x{count:<4} {text!r}")
        for text, count in scan.dropped.most_common(10):
            print(f"   dropped PERSON         x{count:<4} {text!r}")
        for text, count in scan.caps_multiword.most_common():  # every one, for review
            print(f"   caps multi-word masked x{count:<4} {text!r}")
    if args.show_failures:
        for p, how in report.failures:
            print(f"   LEAK {p.doc_id} {p.where or ''} {p.kind}: {p.value!r} ({how})")
    print(f"written: {out}")
    leaks = any(sum(h["leaked"].values()) for h in holdout.values())
    return 0 if not report.failures and not leaks else 1


if __name__ == "__main__":
    sys.exit(main())
