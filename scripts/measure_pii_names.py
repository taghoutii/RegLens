"""Name-masking ablation: spaCy model size x context recognizer, on two synthetic seeds.

Usage (repository root, virtual environment active):
    python scripts/measure_pii_names.py                  # md/lg x context off/on, seeds 42 + 43
    python scripts/measure_pii_names.py --show-failures  # also print missed values (local only)
    python scripts/measure_pii_names.py --variants md+context   # final configuration only

Seed 42 is the corpus in data/raw/. Seed 43 is a *held-out* corpus: same generator, other
names. It is generated into a temporary folder (never data/raw/, so it can never be
ingested) and deleted at the end. It checks that a fix found on seed 42 is not tailored to
seed 42's names. Note: it does not test new document layouts (same templates and labels).

Writes results/<date>_pii_names_ablation.json (counts and ids only) and one EXPERIMENTS row.
"""

import argparse
import logging
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

from reglens.config import get_settings
from reglens.corpus.synthetic import generate_all, is_inside
from reglens.evaluation.pii_leaks import LeakReport, check_leaks, load_planted
from reglens.evaluation.results import append_row, write_result
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.parse_synthetic import parse_incident_log, parse_policy
from reglens.ingestion.pii import PiiMasker

MODELS = {
    "md": {"en": "en_core_web_md", "fr": "fr_core_news_md"},
    "lg": {"en": "en_core_web_lg", "fr": "fr_core_news_lg"},
}


def parse_synthetic(raw: Path) -> list[ParsedDocument]:
    docs = [parse_policy(f, source=f.as_posix()) for f in sorted((raw / "policies").glob("*.md"))]
    log = raw / "logs" / "incident_log.jsonl"
    return [*docs, parse_incident_log(log, source=log.as_posix())]


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--heldout-seed", type=int, default=43)
    parser.add_argument("--show-failures", action="store_true", help="print missed values")
    parser.add_argument(
        "--variants", nargs="+", default=["md", "md+context", "lg", "lg+context"],
        choices=["md", "md+context", "lg", "lg+context"], help="lg needs the lg models",
    )  # fmt: skip
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    with tempfile.TemporaryDirectory(prefix="reglens_heldout_") as tmp:
        tmp_raw, tmp_truth = Path(tmp) / "raw", Path(tmp) / "ground_truth"
        if is_inside(tmp_raw, settings.raw_dir.resolve()):
            raise SystemExit("held-out corpus must not be written under data/raw/")
        generate_all(tmp_raw, tmp_truth, seed=args.heldout_seed)
        corpora = {
            settings.synthetic_seed: (settings.raw_dir, settings.ground_truth_dir),
            args.heldout_seed: (tmp_raw, tmp_truth),
        }
        parsed = {seed: parse_synthetic(raw) for seed, (raw, _) in corpora.items()}
        planted = {seed: load_planted(truth) for seed, (_, truth) in corpora.items()}

        runs: list[dict[str, Any]] = []
        failures: list[tuple[str, int, LeakReport]] = []
        for model_name, models in MODELS.items():
            for context in (False, True):
                variant = f"{model_name}{'+context' if context else ''}"
                if variant not in args.variants:
                    continue
                s = settings.model_copy(
                    update={"pii_spacy_models": models, "pii_context_names": context}
                )
                t0 = time.perf_counter()
                masker = PiiMasker(s)
                load_s = time.perf_counter() - t0
                for seed, docs in parsed.items():
                    t0 = time.perf_counter()
                    masked = {d.doc_id: masker.mask_document(d) for d in docs}
                    mask_s = time.perf_counter() - t0
                    report = check_leaks(masked, planted[seed])
                    summary = report.summary()
                    runs.append({
                        "variant": variant,
                        "seed": seed,
                        "heldout": seed == args.heldout_seed,
                        **summary,
                        "load_s": round(load_s, 1),
                        "mask_s": round(mask_s, 1),
                    })  # fmt: skip
                    failures.append((variant, seed, report))
                    print(f"{variant:<11} seed {seed}: recall {summary['recall']} "
                          f"leaked {summary['leaked']} mask {mask_s:.0f}s", flush=True)  # fmt: skip
                    if args.show_failures:
                        for p, how in report.failures:
                            print(f"     LEAK {p.doc_id} {p.where or ''} {p.kind}: "
                                  f"{p.value!r} ({how})")  # fmt: skip

    result = {
        "name": "pii_names_ablation",
        "date": date.today().isoformat(),
        "config": {
            "models": MODELS,
            "variants": args.variants,
            "person_filter": "no lowercase letter, digits or '|', legal structure words",
            "score_threshold": settings.pii_score_threshold,
            "seeds": {"main": settings.synthetic_seed, "heldout": args.heldout_seed},
        },
        "runs": runs,
    }
    out = write_result(settings.results_dir, result)
    key = "; ".join(
        f"{r['variant']} s{r['seed']}: {r['leaked'].get('names', 0)} missed" for r in runs
    )
    row = (
        f"| {result['date']} | pii_names_ablation | 2 | Name recall, variants {args.variants}, "
        f"seed {settings.synthetic_seed} + held-out seed {args.heldout_seed} "
        f"(300 names each) | {key} | `{out.as_posix()}` |\n"
    )
    append_row(settings.results_dir, row)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
