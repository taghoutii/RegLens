"""Chunk the parsed corpus: data/processed/parsed/ -> data/processed/chunks/<strategy>/.

Usage (repository root, virtual environment active):
    python scripts/chunk_corpus.py                                  # all 3 strategies
    python scripts/chunk_corpus.py --strategies structure_semantic
    python scripts/chunk_corpus.py --record             # also write the result file to results/

Steps:
  1. calibrate the semantic threshold (unless CHUNK_SEMANTIC_THRESHOLD is set): the
     CHUNK_SEMANTIC_PERCENTILE-th percentile of consecutive-sentence similarity inside the
     regulation pieces longer than CHUNK_MAX_TOKENS (the only places it is used);
  2. chunk every document with every strategy, one JSONL file per document;
  3. with --record, record token statistics, bge-m3 CPU time and memory in results/
     (without it, the statistics are only printed: a rebuild adds no result file).
Every semantic cut of the main strategy goes to data/processed/chunks/semantic_cuts.jsonl
for inspection. Input documents are masked; chunks are only slices of them.
Only the documents of the corpus are chunked (reglens/ingestion/inventory.py): a parsed
file left by a document no longer listed is ignored, and each strategy folder is rebuilt
from scratch, so it keeps no stale chunk file.
"""

import argparse
import json
import logging
import statistics
import sys
import time
from datetime import date
from typing import Any

from reglens.config import get_settings
from reglens.corpus.sources import Language, load_corpus
from reglens.evaluation.chunk_stats import stats_by_group
from reglens.evaluation.memory import peak_memory_mb
from reglens.evaluation.results import write_result
from reglens.ingestion.chunking import STRATEGIES, Chunk, Chunker, ChunkParams, ChunkTools
from reglens.ingestion.encoders import Bgem3, SpacySentences
from reglens.ingestion.inventory import expected_documents
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.references import act_number_from_url
from reglens.ingestion.text_split import Span, sentence_spans

logger = logging.getLogger("chunk_corpus")


class TimedSentences:
    """spaCy sentence finder that also adds up its own time."""

    def __init__(self, inner: SpacySentences) -> None:
        self.inner, self.seconds, self.calls = inner, 0.0, 0

    def __call__(self, text: str, langue: Language) -> list[Span]:
        t0 = time.perf_counter()
        out = self.inner(text, langue)
        self.seconds += time.perf_counter() - t0
        self.calls += 1
        return out


def calibrate(docs: list[ParsedDocument], tools: ChunkTools, max_tokens: int,
              percentile: float) -> tuple[float, dict[str, object]]:  # fmt: skip
    sims: list[float] = []
    pieces = 0
    for doc in docs:
        if doc.type_doc != "regulations":
            continue
        finder = tools.finder(doc.langue)
        for b in doc.blocks:
            if b.kind in ("title", "text") and tools.tokens.count(b.text) > max_tokens:
                pieces += 1
                spans = sentence_spans(b.text, finder)
                sims += tools.similarities([b.text[s:e] for s, e in spans])
    cut_points = statistics.quantiles(sims, n=1000, method="inclusive")
    threshold = round(cut_points[int(percentile * 10) - 1], 4)
    deciles = statistics.quantiles(sims, n=10, method="inclusive")
    info = {"pieces_over_max": pieces, "boundaries": len(sims), "percentile": percentile,
            "threshold": threshold, "min": round(min(sims), 4), "max": round(max(sims), 4),
            "deciles": [round(x, 4) for x in deciles]}  # fmt: skip
    return threshold, info


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    parser.add_argument("--record", action="store_true", help="write the run to results/")
    args = parser.parse_args()
    logging.basicConfig(level=settings.log_level, format="%(levelname)-7s %(message)s")

    corpus = load_corpus(settings.sources_file)
    acts = {}
    for src in corpus.regulations:
        number = act_number_from_url(str((src.urls or {}).get("en", "")))
        if number and src.short_name:
            acts[number] = src.short_name
    titles = {src.id: src.title or src.id for src in corpus.reports}

    expected = expected_documents(corpus, settings.raw_dir)
    parsed_dir = settings.processed_dir / "parsed"
    parsed = sorted(p for p in parsed_dir.glob("*.json") if p.stem in expected)
    missing = sorted(expected - {p.stem for p in parsed})
    if missing:
        logger.error("not parsed yet (run scripts/parse_corpus.py): %s", ", ".join(missing))
        return 1
    docs = [ParsedDocument.model_validate_json(p.read_text(encoding="utf-8")) for p in parsed]
    logger.info("%d parsed documents, acts %s", len(docs), acts)

    t0 = time.perf_counter()
    bge = Bgem3(settings.embedding_model, batch_size=settings.embedding_batch_size)
    sentences = TimedSentences(SpacySentences(settings.pii_spacy_models))
    tools = ChunkTools(tokens=bge, sentences=sentences, similarities=bge.similarities)
    setup_seconds = time.perf_counter() - t0

    calibration: dict[str, object] = {"fixed": settings.chunk_semantic_threshold}
    threshold = settings.chunk_semantic_threshold
    if threshold is None:
        threshold, calibration = calibrate(docs, tools, settings.chunk_max_tokens,
                                           settings.chunk_semantic_percentile)  # fmt: skip
    logger.info("semantic threshold %.4f (%s)", threshold, calibration)
    params = ChunkParams(max_tokens=settings.chunk_max_tokens,
                         min_tokens=settings.chunk_min_tokens,
                         overlap=settings.chunk_overlap_tokens, threshold=threshold,
                         noise_events=tuple(settings.chunk_incident_noise_events),
                         table_context_max_tokens=settings.chunk_table_context_max_tokens,
                         )  # fmt: skip

    out_root = settings.processed_dir / "chunks"
    per_strategy: dict[str, dict[str, Any]] = {}
    for strategy in args.strategies:
        chunker = Chunker(tools, params, acts=acts, titles=titles)
        out_dir = out_root / strategy
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.jsonl"):
            old.unlink()  # derived data: a rerun replaces it entirely
        all_chunks: list[Chunk] = []
        t1, embed_before = time.perf_counter(), bge.embed_seconds
        spacy_before, embedded_before = sentences.seconds, bge.embedded
        for doc in docs:
            chunks = chunker.chunk(doc, strategy)
            path = out_dir / f"{doc.doc_id}__{doc.langue}.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                for c in chunks:
                    fh.write(c.model_dump_json() + "\n")
            all_chunks += chunks
        seconds = time.perf_counter() - t1
        ids = [c.chunk_id for c in all_chunks]
        per_strategy[strategy] = {
            "seconds": round(seconds, 1),
            "embedding_seconds": round(bge.embed_seconds - embed_before, 1),
            "sentence_split_seconds": round(sentences.seconds - spacy_before, 1),
            "new_sentences_embedded": bge.embedded - embedded_before,
            "duplicate_chunk_ids": len(ids) - len(set(ids)),
            "semantic_cuts": len(chunker.cuts),
            "semantic_cuts_below_threshold": sum(
                1 for c in chunker.cuts if c["below_threshold"]
            ),  # fmt: skip
            "tokens": stats_by_group(
                all_chunks, max_tokens=params.max_tokens, min_tokens=params.min_tokens
            ),  # fmt: skip
            # what the embedding reads for table chunks (context + row lines)
            "table_embedding_tokens": stats_by_group(
                [
                    c.model_copy(update={"n_tokens": bge.count(c.embedding_text())})
                    for c in all_chunks
                    if c.kind == "table"
                ],
                max_tokens=params.max_tokens,
                min_tokens=params.min_tokens,
            ),  # fmt: skip
            "table_sources": {
                src: sum(1 for c in all_chunks if c.table_source == src)
                for src in ("pdf", "spreadsheet")
            },  # fmt: skip
            # extra tokens the embedding reads (context line + newline), per chunk with one
            "context_tokens": stats_by_group(
                [
                    c.model_copy(update={"n_tokens": bge.count(c.context + "\n")})
                    for c in all_chunks
                    if c.context
                ],
                max_tokens=params.max_tokens,
                min_tokens=params.min_tokens,
            ),  # fmt: skip
        }
        if strategy == "structure_semantic":
            with (out_root / "semantic_cuts.jsonl").open("w", encoding="utf-8") as fh:
                for cut in chunker.cuts:
                    fh.write(json.dumps(cut, ensure_ascii=False) + "\n")
        logger.info("%-20s %6d chunks in %.0fs", strategy, len(all_chunks), seconds)

    sentence_count = bge.embedded
    result = {
        "date": date.today().isoformat(),
        "name": "chunking_stats",
        "phase": 3,
        "config": {
            "embedding_model": settings.embedding_model,
            "max_tokens": params.max_tokens,
            "min_tokens": params.min_tokens,
            "overlap": params.overlap,
            "threshold": threshold,
            "noise_events": list(params.noise_events),
            "token_counts": "bge-m3 tokenizer, without special tokens",
        },  # fmt: skip
        "calibration": calibration,
        "documents": len(docs),
        "strategies": per_strategy,
        "bge_m3_cpu": {
            "setup_seconds": round(setup_seconds, 1),
            "model_load_seconds": round(bge.load_seconds, 1),
            "embedding_seconds_total": round(bge.embed_seconds, 1),
            "sentences_embedded": sentence_count,
            "sentences_per_second": round(sentence_count / bge.embed_seconds, 1)
            if bge.embed_seconds
            else None,
            "parameter_mb": round(bge.parameter_bytes() / 2**20, 1),
            "peak_process_memory_mb": peak_memory_mb(),
            "spacy_sentence_seconds_total": round(sentences.seconds, 1),
        },
    }
    if not args.record:
        print(json.dumps(result, indent=2))
        return 0
    path = write_result(settings.results_dir, result)
    summary = "; ".join(
        f"{s}: {v['tokens']['all']['chunks']} chunks, median {v['tokens']['all']['median']}, "
        f"max {v['tokens']['all']['max']}"
        for s, v in per_strategy.items()
    )
    logger.info("threshold %s (p%s); %s", threshold, settings.chunk_semantic_percentile, summary)
    logger.info("recorded %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
