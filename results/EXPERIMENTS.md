# Experiments

Every measurement behind a reported number, grouped by phase. Each run writes
`results/<date>_<name>.json` (configuration, metrics, timing, memory; a second run on the
same day gets `_2`, `_3`, ...: files are never overwritten). For each measurement: what it
measures, why, the final result, the decision it supports, and how earlier runs led to it.
Superseded runs are kept in `results/archive/`.

A target counts as met only when a file here shows it. KPIs of the question path
(Recall@k, MRR, faithfulness, refusals, leaks, latency, cache) are not yet measured.

Phases 0, 1: no measurement (setup and corpus generation).

## Phase 2: Parsing and PII masking

### Docling speed on CPU
- **Measures:** seconds per page and cells found on 5 table-heavy pages, for table mode (accurate / fast) x threads (4 / 8) x OCR.
- **Why:** Docling is the slowest ingestion step and there is no GPU; changing parser needs approval, so its speed is reported first.
- **Result:** accurate, 8 threads, no OCR: 8.1 s/page, 790/790 cells. Fast: 3.6 s/page but 289/790 cells. OCR: +10 % time, no extra cells (the PDFs have a text layer).
- **Decision:** accurate tables, 8 threads, no OCR; Docling kept.
- **File:** `2026-11-19_docling_speed_2.json`

### Docling full run
- **Measures:** time to parse all 6 reports with the chosen settings.
- **Why:** sizes the ingestion cost and decides whether parser output must be cached.
- **Result:** 1,092 pages in 2.27 h (7.5 s/page).
- **Decision:** raw Docling output is cached in `data/cache/`, so masking and mapping can be rerun without re-parsing.
- **File:** `2026-11-19_docling_full_run.json`

### Name recall: spaCy model size and context recognizer (decision O5)
- **Measures:** planted person names missed by Presidio, spaCy md vs lg, with and without a context recognizer, on seed 42 and a held-out seed 43 (300 names each).
- **Why:** O5, model size vs detection quality in EN and FR; names are the hardest PII type.
- **Result:** md missed 5 / 12 names, lg 6 / 16; with the context recognizer both miss 0 on both seeds. Rechecked with the final person filter: md + context, 0 missed on both seeds.
- **Decision:** O5 = md models + context recognizer (lg is larger and not better).
- **Files:** `2026-11-19_pii_names_ablation.json`, recheck `2026-11-20_pii_names_ablation_2.json`

### PII masking: recall on planted values and false positives
- **Measures:** recall per PII type on every planted value of the synthetic data, values leaked into `data/processed/`, and detections on regulations and reports (where every detection is a false positive or public data).
- **Why:** Phase 2 acceptance: 0 planted values in processed data and PostgreSQL; false positives on regulations must be reported.
- **Result:** recall 1.0 for names, e-mails, phones, IBANs and secrets; 0 leaks. False positives: 47 PERSON spans on the 16 regulation files, 669 PERSON and 1 phone on the 6 reports (e.g. "Contenu", masked in a MiCA FR article title).
- **Decision:** acceptance "0 planted values" met; the remaining false positives are accepted and reported.
- **Progression:** lenient phone detection, no filters: names 0.983, 5 leaks, 3,627 phone and 354 person false positives on regulations (JSON rebuilt from console output) → valid phone numbers + UK/FR pattern, context names, legal-structure filter: recall 1.0, 0 leaks, 167 → person filter (no lowercase letter, digits, "|"): 47 on regulations, 2,446 on reports → rule for all-caps multi-word names: reports 669, but regulations 323 ("CHAPTER III ..." headings masked) → rule kept off regulation headings: 47 / 669.
- **File:** `2026-11-20_pii_masking_2.json` (earlier runs in `archive/`)

### Table cells: Docling values vs the PDF text layer
- **Measures:** share of non-empty extracted cells found in the PDF text inside the table box, on 20 tables spread over the 6 reports (seed 42). Checks values, not their row or column.
- **Why:** Phase 2 acceptance: 100 % of cells correct on 20 tables.
- **Result:** 1,833 / 1,877 cells (97.7 %). Numbers: 1,310 / 1,310. Missing: 28 / 196 header cells, 16 / 371 row labels. The low-confidence flag caught 0 of the 44 missing cells.
- **Decision:** criterion **not met** (header and label text, not numbers); kept as a known limitation, with header problems flagged per table (below).
- **File:** `2026-11-20_table_cells_2.json`

### Docling cell matching on vs off
- **Measures:** the same 20 tables with `do_cell_matching` on and off, plus the words of the PDF table region that end up in no cell, by type.
- **Why:** off found more cells (99.4 % vs 97.7 %); switching was conditional on losing no row label and no number.
- **Result:** with off, 12 row-label words and 1 number of the PDF region are in no cell; with on, none (its 43 lost words are other text, footnotes and captions).
- **Decision:** cell matching stays on.
- **Files:** `2026-11-20_table_cell_matching.json`, `2026-11-20_table_lost_words.json`

### Table structure: visual review
- **Measures:** 2 tables compared by eye (rendered PDF region vs extracted grid).
- **Why:** the value check cannot see a value under the wrong column header.
- **Result:** both have high-severity header errors (a year span over the wrong column; two stacked tables merged into one, with a column merge); all numbers are correct and in the right rows. Not blind: the reviewer's notes were known beforehand.
- **Decision:** add a "header may be wrong" flag (next). Known limitation: stacked tables on one page are not detected (not built).
- **File:** `2026-11-20_table_review.json`

### Header-uncertain rules
- **Measures:** precision, recall and firing rate of 3 candidate rules against 10 visually labelled tables (6 with header errors).
- **Why:** the flag (`header_uncertain`) lets later phases mark answers built on such tables as uncertain.
- **Result:** A-slots: P 0.75, R 1.0, fires on 50 % of tables; **A-cells (`same_label_cells`): P 1.0, R 0.5, fires on 8 %**; B: P 1.0, R 0.17. Biased: half the labelled set was chosen because A-slots fired.
- **Decision:** `same_label_cells` adopted (precision first; a flag on half the tables would mean nothing).
- **File:** `2026-11-20_table_header_flags.json`

## Phase 3: Chunking

### Chunk statistics per strategy and type
- **Measures:** token counts (bge-m3 tokenizer) per strategy and per document type / chunk kind; semantic-split calibration and cuts; bge-m3 CPU time and peak memory.
- **Why:** Phase 3 acceptance (token-length statistics per strategy and type); sizes the Phase 5 embedding cost; input of the Phase 6 strategy comparison.
- **Result (main strategy, structure + semantic):** 8,563 chunks, median 206 tokens, P95 812. Regulations 4,967 chunks, median 214, max 512. Report text 2,117, median 165. Tables 929, median 764, max 5,100, 622 over 512. Policy sections 350, median 64. Incidents 200, median 180. Context lines on 3,173 chunks: median 15 tokens, P95 33, about 55k tokens in total.
- **Result (semantic split):** threshold 0.4714 = 5th percentile of 3,211 consecutive-sentence similarities in regulation pieces over 512 tokens. 629 cuts: 79 below the threshold, 550 forced by the 512 limit (so the threshold has little influence; to confirm in Phase 6).
- **Result (comparison strategies):** recursive 5,177 chunks, median 455; semantic-only 7,236, median 253 (95 min on CPU).
- **Result (cost):** bge-m3 on CPU embeds 4.3 sentences/s (about 290 tokens/s), 2,166 MB of weights, peak process memory 3.3 GB.
- **Decision:** tables over 512 tokens will be split into row groups before embedding; Phase 5 embedding estimated at 1.2–2.4 h for the main strategy (estimate, to measure on a sample first).
- **Progression:** first run, threshold 0.364, 9,069 chunks (memory value invalid: measurement bug, fixed) → rerun: byte-identical chunks (determinism check), peak 3.3 GB → fix: pieces of one cut block no longer glued together (Required test 1 passes on the corpus), 9,063 → sentence repair (labels, abbreviations, act numbers, punctuation-only pieces, whole table rows) and heading-only report sections dropped: threshold 0.364 → 0.4714 (the lowest similarities came from fragments), 8,563 chunks, report text chunks under 100 tokens 925 → 446 → final recount of the joined text (no text chunk over 512) and `context` field.
- **Files:** main strategy `2026-11-22_chunking_stats.json` (threshold fixed to the calibrated value, so it records no calibration and a cold sentence cache: 1,329 s); calibration, recursive and semantic-only `2026-11-21_chunking_stats_4.json` (its main-strategy numbers are superseded); earlier runs in `archive/`

### Tables continued across pages
- **Measures:** candidate pairs where table B continues table A: B is the next content item after A (only page header/footer, footnote or caption between), on the next page, with the same column count, and no caption or a "continued" caption. Then 10 candidates checked by eye (end of A, start of B).
- **Why:** a table cut by a page break becomes 2 tables, so a row group of the second part loses its title and header; this decides whether continuations are merged before embedding.
- **Result:** 51 candidate pairs (97 of the 929 tables): ING 35, BNPP Fortis tables 7, SocGen EN 3, SocGen FR 3, BNPP Fortis 2, SocGen H1 1. No caption says "continued". 5 candidates are chains (tables over 3 or more pages). Review: 7 / 10 true continuations (precision 0.7, 95 % interval 0.40–0.89). All 3 false positives: A ends with a "Total" row and B is another table with the same layout (other year or other side of the balance sheet). 10 of the 51 candidates have A ending with a "Total" row; that rule separates the sample perfectly, but it was found on that sample (not an independent check). Recall not measured.
- **Check of the "Total" filter (independent sample):** 10 fresh candidates among the 41 kept by the filter (seed 43), with the decision rule set beforehand: link only if at least 9 are true. Result: 8 / 10 (precision 0.8, 95 % interval 0.49–0.94). The 2 false positives: a table title on the page that Docling did not output as a heading; a Total row whose label follows a row number, so the rule missed it.
- **Decision:** continued tables are neither merged nor linked. Known limitation: about 33 pairs (20–39), i.e. the second part of roughly 3–4 % of the tables is chunked without the first part's title, and without a header when it has none of its own (4 of the 7 true pairs of the first review, 5 of the 8 of the second).
- **Files:** `2026-11-22_table_continuations.json` (detection, first review), `2026-11-22_table_continuations_2.json` (filter check); review images with unmasked report content in `data/cache/continuation_review/` (not committed)
