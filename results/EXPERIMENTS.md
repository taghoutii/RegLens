# Experiments

Every measurement behind a reported number for the current corpus, one section per
pipeline stage. Each run writes `results/<date>_<name>.json` (configuration, metrics,
timing; a second run on the same day gets `_2`, ...). Each item gives what it measures,
the result, why it is measured, and its file.

A target counts as met only when a file here shows it. The KPIs of the question path
(Recall@k, MRR, faithfulness, refusals, leaks, latency, cache) are not yet measured.
Exact table accuracy against ground truth (synthetic quarterly report) is not yet measured.

## Download

### Sources fetched and validated
- **Measures:** files fetched from `data/sources.yaml` and checked (content type, size, file signature, expected marker).
- **Result:** 26 files: 12 regulations in EN and FR (24 HTML files, 16 MB), the ING Pillar 3 report (PDF) and its spreadsheet appendix (XLSX), 6.3 MB together.
- **Why:** the corpus is exactly what `sources.yaml` lists; outputs of unlisted documents are deleted by the build.
- **File:** none (printed by `scripts/download_corpus.py`).

## Docling

### PDF parsing speed on CPU
- **Measures:** conversion time of the report PDF (accurate tables, cell matching on, no OCR, 8 threads).
- **Result:** 190 pages in 1,670 s (8.8 s/page).
- **Why:** Docling is the slowest ingestion step and there is no GPU; its output is cached in `data/cache/` so later stages rerun without it.
- **File:** `2026-11-25_docling_full_run.json`

## Parse: masking

### PII recall on planted values and false positives
- **Measures:** recall per PII type on every value planted in the synthetic data (seed 42, and a held-out seed 43 generated into a temporary folder), planted values found in the masked output, and detections on regulations and the report (all false positives or public data).
- **Result:** recall 1.0 for names (300), e-mails (300), phones (50), IBANs (50) and secrets (50) on both seeds; 0 leaks. False positives: 108 PERSON spans over the 24 regulation files (6.5 M characters; e.g. "Alphanumerical" and "Instructions" in the ITS 2024/2956 templates, "Titles III", "Charter"), 725 more removed by the person filter; 280 PERSON spans on the report (e.g. "Forborne", hyphenated word ends), 5 all-caps multi-word spans masked.
- **Why:** Phase 2 acceptance: 0 planted values in processed data, recall 100 % on seeds 42 and 43; false positives on regulations are reported.
- **File:** `2026-11-25_pii_masking.json`

## Parse: tables (ING)

### Extracted cell values vs the PDF text layer
- **Measures:** share of non-empty Docling cells found in the PDF text inside the table box, on 20 ING tables (seed 42). Checks values, not their row or column.
- **Result:** 1,541 / 1,595 cells (96.6 %); numbers 836 / 838, row labels 568 / 603, header cells 137 / 154. 11 tables have at least one miss; the low-confidence flag catches 2 of the 54 missing cells.
- **Why:** checks Docling's extraction where no ground truth exists; it measures Docling's grid, before the spreadsheet replacement.
- **File:** `2026-11-25_table_cells.json`

## Parse: spreadsheet

### ING tables replaced by the spreadsheet appendix
- **Measures:** outcome of the pipeline's spreadsheet replacement on the 216 ING tables (111 blocks in 76 sheets), and replaced tables left without header rows.
- **Result:** 107 replaced; 9 kept on PDF content because a value differs, 16 because rows do not align, 84 with no certain block. 0 replaced tables without header rows.
- **Why:** a replaced table takes exact values and headers from the bank's own file; the rules are strict so that a wrong block is never used.
- **File:** `2026-11-25_structured_tables.json`

## Parse: quality (ING)

### Severity of errors in the final tables
- **Measures:** errors that change meaning in 20 of the 216 final ING tables (`random.Random(42)`), PDF region vs stored grid, labelled by eye with the rules in `scripts/measure/review_table_quality.py` (high = a reader would get a wrong answer; medium = degraded but recoverable; none = matches the PDF).
- **Result:** high 3 / 20 = 15 % (95 % Wilson interval 5–36 %), medium 7, none 10. Spreadsheet tables: high 1, medium 3, none 10; PDF tables: high 2, medium 4, none 0. High errors: a data row read as the header (PDF), two marker columns merged (PDF), a missing risk-weight bucket header row (spreadsheet CR5, a defect that affects 5 tables). Single reviewer, not blind.
- **Why:** the value check cannot see a value under the wrong label; this estimates how often a table answer would be wrong.
- **File:** `2026-11-25_table_quality.json`

## Parse: PostgreSQL

### Planted values and one version per table
- **Measures:** planted PII values in the PostgreSQL table store; duplicates after a rerun; rows of unlisted documents.
- **Result:** 0 planted values; a rerun creates no duplicates; tables of unlisted documents are deleted.
- **Why:** the masking rule applies to every store; stale rows would be retrieved as current data.
- **File:** none (integration tests `tests/test_integration_processed.py`, `tests/test_integration_table_store.py`).

## Chunk

### Chunks and tokens per strategy and type
- **Measures:** chunk counts and token lengths (bge-m3 tokenizer, without special tokens) per strategy and document type, embedded size of table chunks, context lines, semantic-split calibration, bge-m3 CPU cost.
- **Result (structure + semantic, main):** 8,159 chunks, median 182 tokens, P95 455. Regulations 6,222 (median 208, max 512), report text 275 (median 155), tables 1,112 (687 from the spreadsheet, 425 from the PDF), policy sections 350 (median 64), incidents 200 (median 180). Table chunks as embedded (context + row lines): median 424, P95 505; 20 chunks of 2 tables exceed 512 (tbl-200: a header path of about 480 tokens; tbl-139: a long text cell). Context lines on 5,511 chunks, median 19 tokens.
- **Result (semantic split):** threshold 0.4561 = 5th percentile of 4,673 consecutive-sentence similarities in regulation pieces over 512 tokens; 720 cuts, 89 below the threshold (the others forced by the 512 limit).
- **Result (comparison strategies):** recursive 5,191 chunks, median 397; semantic-only 6,771, median 221 (one regulation chunk of 513 tokens).
- **Result (cost):** bge-m3 on CPU embeds 5.5 sentences/s; 2,166 MB of weights, peak process memory 3.8 GB; semantic-only takes 64 min.
- **Why:** Phase 3 acceptance (token statistics per strategy and type); sizes the Phase 5 embedding; input of the Phase 6 strategy comparison.
- **File:** `2026-11-25_chunking_stats.json`
