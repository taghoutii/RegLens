"""Build the corpus with one command: data/raw/ -> parsed, masked documents -> chunks.

Usage (repository root, virtual environment active, `docker compose up -d` for tables):
    python scripts/build_corpus.py                    # run every stage that is out of date
    python scripts/build_corpus.py --record           # also record Docling and chunking runs
    python scripts/build_corpus.py --from parse       # force parse and every stage after it
    python scripts/build_corpus.py --only chunk       # force one stage (or several)
    python scripts/build_corpus.py --dry-run          # show what would run

Stages, in order (each one is its own script, run as a separate process):
    download  scripts/download_corpus.py     data/raw/{regulations,reports}/
    generate  scripts/generate_synthetic.py  data/raw/{policies,logs}/, data/ground_truth/
    docling   scripts/run_docling.py         data/cache/docling/   (hours on CPU, resumable)
    parse     scripts/parse_corpus.py        data/processed/parsed/ + PostgreSQL tables
    chunk     scripts/chunk_corpus.py        data/processed/chunks/<strategy>/

A stage is out of date when its output is missing or older than its input; once a stage
runs, every later stage runs too. Measurements are separate (scripts/measure/).
"""

import argparse
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from reglens.config import Settings, get_settings
from reglens.corpus.sources import download_targets, load_corpus
from reglens.ingestion.chunking import STRATEGIES
from reglens.ingestion.parse_pdf import docling_config, is_cached

STAGES = ["download", "generate", "docling", "parse", "chunk"]
SCRIPTS = {
    "download": "download_corpus.py",
    "generate": "generate_synthetic.py",
    "docling": "run_docling.py",
    "parse": "parse_corpus.py",
    "chunk": "chunk_corpus.py",
}


def _newest(paths: Iterable[Path]) -> float:
    return max((p.stat().st_mtime for p in paths), default=0.0)


def _oldest(paths: Iterable[Path]) -> float:
    return min((p.stat().st_mtime for p in paths), default=0.0)


def _files(folder: Path, pattern: str) -> list[Path]:
    return sorted(folder.glob(pattern)) if folder.exists() else []


def out_of_date(settings: Settings) -> dict[str, Callable[[], bool]]:
    """One check per stage: True when the stage must run."""
    raw, processed = settings.raw_dir, settings.processed_dir
    corpus = load_corpus(settings.sources_file)

    def download() -> bool:
        return any(not t.path.exists() for t in download_targets(corpus, raw))

    def generate() -> bool:
        return not (_files(raw / "policies", "POL-*.md") and (raw / "logs").exists()
                    and _files(settings.ground_truth_dir, "*.json"))  # fmt: skip

    def docling() -> bool:
        config = docling_config(settings)
        return any(not is_cached(settings.docling_cache_dir, s.id, config) for s in corpus.reports)

    def parse() -> bool:
        outputs = _files(processed / "parsed", "*.json")
        inputs = [p for p in raw.rglob("*") if p.is_file()]
        inputs += _files(settings.docling_cache_dir, "*.json")
        return not outputs or _newest(inputs) > _oldest(outputs)

    def chunk() -> bool:
        newest_parsed = _newest(_files(processed / "parsed", "*.json"))
        for strategy in STRATEGIES:
            chunks = _files(processed / "chunks" / strategy, "*.jsonl")
            if not chunks or _oldest(chunks) < newest_parsed:
                return True
        return False

    return {"download": download, "generate": generate, "docling": docling,
            "parse": parse, "chunk": chunk}  # fmt: skip


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--from", dest="start", choices=STAGES, help="force this stage and later")
    group.add_argument("--only", nargs="+", choices=STAGES, help="force only these stages")
    parser.add_argument("--record", action="store_true", help="record docling + chunk runs")
    parser.add_argument("--no-db", action="store_true", help="parse without PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, run nothing")
    args = parser.parse_args()

    checks = out_of_date(settings)
    scripts_dir = Path(__file__).parent
    forced = set(args.only or (STAGES[STAGES.index(args.start) :] if args.start else []))
    running = False  # once a stage runs, every later stage runs too (without --only)
    for stage in STAGES:
        if args.only:
            run = stage in forced
        else:
            run = running or stage in forced or checks[stage]()
            running = running or run
        if not run:
            print(f"[{stage}] up to date")
            continue
        command = [sys.executable, str(scripts_dir / SCRIPTS[stage])]
        if args.record and stage in ("docling", "chunk"):
            command.append("--record")
        if args.no_db and stage == "parse":
            command.append("--no-db")
        print(f"[{stage}] {' '.join(command[1:])}", flush=True)
        if args.dry_run:
            continue
        code = subprocess.run(command, check=False).returncode
        if code != 0:
            print(f"[{stage}] failed (exit code {code}): later stages not run")
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
