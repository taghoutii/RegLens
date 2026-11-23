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

### Table rows: header-path serialization and Total-row check
- **Measures:** (1) tokens of one self-describing line per table row ("row label — header path = value; ..."), against the Markdown grid; (2) "Total" rows that equal the sum of their component rows, within rounding (each printed value is off by at most half a unit of its last digit), on the 929 report tables; (3) whether a failed total catches the header errors of the 10 visually labelled tables.
- **Why:** row groups of large tables must keep their labels; a total check could flag values misread or put in the wrong column.
- **Result (serialization):** table tokens grow from 0.77 M (Markdown) to 1.50 M (row lines): ratio median 1.34, P95 3.45. 18 tables have a single row line over 512 tokens (wide templates with long header paths). 278 tables (30 %) have no detected header row; 226 of them start with rows without numbers, i.e. a header Docling missed: their lines carry header text as data. On bnpp_fortis_pillar3_2025#tbl-10, the known wrong year span is carried into every line.
- **Result (total check):** 477 tables have a "Total" row; 253 are checkable; 36 fail. 0 / 6 labelled header errors caught (header errors move labels, not values: the sums still hold). Review of 8 failures: 0 confirmed extraction errors (5 check limitations: subtraction, split tables, numbered or (a)/(b) hierarchies; 1 source sign convention; 1 tolerance bug, fixed; 1 unclear). A first version that ignored row hierarchy gave 46 failures; detecting subtotal rows brought it to 39, the rounding fix to 36.
- **Decision:** pending. Serialization needs header-row inference first; the total check is not precise enough to flag tables as wrong (proposal: keep it as a diagnostic, not a chunk flag).
- **File:** `2026-11-23_table_rows.json`

### Structured table sources and vision re-extraction (investigation)
- **Measures:** which reports publish their tables in a structured file, how many of the corpus's table ids such a file could map to; pages and cost to re-extract flagged tables with a vision model. No paid call.
- **Why:** improve table content (headers in particular) instead of only flagging errors; table ids must stay unchanged.
- **Result (structured):** ING publishes an `.xlsx` appendix (81 sheets, 79 EBA templates). 112 of its 216 tables carry a template code with a matching sheet (67 templates); 59 more follow such a table without a code of their own (continuations, second year), so up to 171 (79 %) at template level; cell alignment not checked. SocGen: no Pillar 3 spreadsheet found (to check by hand on its document base). BNPP Fortis: unknown (site unavailable). Reading cells needs openpyxl (installed, not declared) or a standard-library reader.
- **Result (vision):** 106 flagged tables (74 `header_uncertain`, 36 failed totals) on 91 pages; 315 pages if tables without a detected header are added; 20 pages for the comparison on the Phase 2 test tables. At $4 per 1,000 pages: $0.08 for the comparison, $0.36 for the 91 pages, $1.26 for 315, $4.37 for all 1,092 report pages. No new dependency (httpx is declared); the pages (public, unmasked) leave the machine.
- **Decision:** pending.
- **File:** `2026-11-23_table_sources.json`

### Header-row inference (tables without a detected header)
- **Measures:** how many of the 278 tables without a detected header row receive one, and whether the inferred count is right (PDF region vs grid, by eye, 10 random tables).
- **Rule:** leading rows with text beyond the first column and no number except years become header rows, at most 4, only if a row with numbers follows.
- **Result:** v1 on sample seed 42: 7 / 10 right (2 tables with 5 header rows cut at the cap of 4; 1 section row repeated by a span read as a header). v2 (a row whose only text repeats the first cell is a section) on a fresh sample, seed 43: 9 / 10 right (the miss: the same 5-row climate template). With v2: 182 tables get 1 to 4 header rows, 96 keep 0. 8 tables reach the cap with a 5th header-like row.
- **Decision:** approved rule; header text quality (fused dates) is not fixed by it.
- **Files:** `2026-11-23_header_inference.json` (v1), `2026-11-23_header_inference_2.json` (v2)

### Header spans from the PDF geometry
- **Measures:** header-path accuracy on the 66 value columns of the 10 visually labelled tables (gold written from the PDF images before the method ran); on the 20 Phase 2 test tables, cells found in the PDF text layer, number cells found and word coverage; tables changed over the corpus; a spot check of 10 random changed tables outside the labelled set.
- **Method:** columns from Docling's body cell boxes; header labels from the PDF characters (pypdfium2); each label spans the filled box that holds only it, else the rule just under it, else the columns its text overlaps. Body rows unchanged. Adoption rule fixed beforehand (see the result file).
- **Result:** run 1 invalid (character-box bug: words and lines split). Run 2: accuracy 37 / 66 (56 %) vs 24 / 66 (36 %) for Docling + header-row inference; 0 regressions on the 4 tables labelled correct; test tables: cells found 98.98 % vs 97.13 %, number cells 1,008 / 1,008, coverage 99.09 % vs 98.85 %. The fixed rule passes. Applied to 700 of 929 tables, header paths changed on 645. Spot check outside the labelled set: 4 better, 3 worse, 3 mixed. Two causes: right-aligned headers of adjacent columns read as one label; section rows above the first value row read as header lines.
- **Decision:** not adopted yet: the rule passed on 10 tables but the out-of-sample check is mixed. Fixes proposed (split phrases at column gutters; end the header at the first label-only row), to be judged on the labelled set and a fresh spot check.
- **Files:** `2026-11-23_header_geometry.json` (invalid run), `2026-11-23_header_geometry_2.json`

### ING spreadsheet appendix mapped to the extracted tables
- **Measures:** how many of the 216 ING tables map with certainty to a block of the bank's `.xlsx` appendix (113 blocks in 76 sheets), using the numbers themselves.
- **Method:** a table maps to the block holding its informative numbers (non-zero, not a bare integer < 100, not a year) once spreadsheet values are rounded as printed. Certain = at least 5 such numbers, at least 95 % found in the best block, runner-up block at least 0.2 lower. Row level: every table row finds the one spreadsheet row holding all its numbers, in order.
- **Result:** 151 tables have at least 5 informative numbers; 132 map with certainty to one block; 112 of them also align row by row. 12 cover a whole block; the others are page parts of one block (25 blocks are shared by several tables). The template code, when the table has one (81 of 132), always agrees with the sheet. Misses seen: rounding differences in a few rows, and a table where Docling fused header and body text (the spreadsheet holds the correct values).
- **Decision:** prototype only; replacing table content from the spreadsheet is not built.
- **File:** `2026-11-23_structured_tables.json`
