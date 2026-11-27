# RegLens

Cited answers on EU financial regulation for fintech compliance teams.

RegLens answers compliance questions from a payment or e-money fintech by searching EU regulations and the company's internal records. Every claim cites its exact source (document, article, section, record), and the system refuses when the corpus does not support an answer.

## What it can answer
- **Regulation:** "What must an initial notification of a major ICT incident contain?"
- **Cross-references:** "Which articles does DORA Article 19 depend on?"
- **Policy compliance:** "Does the safeguarding policy of the Payments unit meet the PSD2 and EMD2 deadlines?"
- **Incident deadlines:** "Was INC-0010 reported on time, under DORA and under the unit's own policy?"
- **ICT providers:** "Which critical providers support the payments gateway, and which have no exit plan?"
- **Payment obligations:** "After how many failed authentication attempts must access be blocked?"
- **Refusal:** "What were ING's capital ratios in 2025?" (not in the corpus)

## Architecture
```
Ingestion   sources ─► parse (EUR-Lex HTML, Markdown, JSONL, CSV) ─► mask PII (Presidio)
            ─► chunk (structure + semantic) ─► embed (bge-m3, dense + sparse) ─► Qdrant

Question    FastAPI ─► hybrid search in Qdrant (dense + sparse, RRF) ─► cross-encoder reranking
            ─► refusal if evidence is too weak ─► LLM answer with citations ─► PII check
            ─► streamed to the Next.js interface (SSE)
```

## Corpus
Listed in [`data/sources.yaml`](data/sources.yaml); raw files are not committed.

| Type | Content |
| :--- | :--- |
| EU regulations (EN + FR) | DORA and its technical standards (RTS 2024/1772, RTS 2025/301, RTS 2024/1774, ITS 2024/2956), PSD2 and the SCA standard (RTS 2018/389), the E-Money Directive, GDPR, AMLR, MiCA, the AI Act |
| Internal records (synthetic) | Policies (EN + FR), ICT incident log, register of ICT providers, generated with fake personal data |

## Tech stack
FastAPI · Pydantic v2 · LlamaIndex · Presidio · BAAI/bge-m3 · Qdrant · BAAI/bge-reranker-v2-m3 · Mistral · Redis · Ragas · Langfuse · Next.js · Docker Compose · GitHub Actions

## Getting started (Windows, PowerShell)
Requirements: Python 3.11, Docker Desktop.

```powershell
Copy-Item .env.example .env
docker compose up -d                      # Qdrant, Redis
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu   # bge-m3 runs on CPU
pip install -e ".[dev,models]"            # "models": spaCy NER models for PII masking
pytest -m "not integration"
```

## Building the corpus
```powershell
python scripts/build_corpus.py            # download -> generate -> parse + mask -> chunk
python scripts/build_corpus.py --dry-run  # show which stages would run
python scripts/build_corpus.py --from parse
python scripts/chunk_corpus.py --baselines   # also the two comparison strategies (slow on CPU)
```

| Stage | Output |
| :--- | :--- |
| download | `data/raw/regulations/` |
| generate | synthetic records in `data/raw/`, answer key in `data/ground_truth/` (never indexed) |
| parse + mask | `data/processed/parsed/` |
| chunk | `data/processed/chunks/structure_semantic/`; baselines in `recursive/` and `semantic/` |

Personal data is masked before anything derived is written. Sentence vectors are cached in `data/cache/`, so a rebuild only embeds new or changed sentences. Measurement scripts live in `scripts/measure/`; results are in [`results/EXPERIMENTS.md`](results/EXPERIMENTS.md).

## Repository structure
```
data/          sources.yaml, golden_set/ (raw, processed, ground_truth and cache are not committed)
docs/          project scoping and backlog
results/       evaluation results
scripts/       build_corpus.py and one script per stage; measure/ for measurements
src/reglens/   corpus, ingestion, evaluation, retrieval, generation, api, observability
tests/
frontend/
```
