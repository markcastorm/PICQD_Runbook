# PICQD Extraction Pipeline — Working Memory

## Project Goal
Extract 228 financial data columns from Japan Post Insurance quarterly PDFs into a structured CSV/Excel output.
Pipeline: PDF → extractor.py → file_generator.py → ZIP with timestamped Excel.

## Source PDFs
| File | Type | Pages | Period |
|------|------|-------|--------|
| `Project_information/pr0515en-3-03.pdf` | Q1 Annual | 65 | FY2026-Q1 (March 31, 2026) |
| `Project_information/sample/pr0213en-03.pdf` | Q4 Simplified | 19 | 2025-Q4 (December 31, 2025) |
| `Project_information/sample/pr1114en-06.pdf` | Q2 Simplified | ? | — |
| `Project_information/sample/pr0808en-03.pdf` | Q3 Simplified | ? | — |

**Q1 Annual** = 65-page detailed report, two-column ASSETS+LIABILITIES side-by-side layout, all 11 tables present.
**Q4/Q2/Q3 Simplified** = ~19-page condensed report, single-column layout, only UNONCONSBS + UCONSBS + AHELDMAT tables present. The detailed investment tables (FAIRVAL, HELDMAT, POLRES, ASALSEC, MONHELD, ASALSECSOLD, CURRELDER, INTRATEDER) are **absent** in simplified format — NOT FOUND is expected.

## Period Detection
- Pattern: `r'(?:Fiscal Year|(?:Three|Six|Nine) Months) Ended (\w+) \d{1,2}, (\d{4})'`
- Month → Quarter: `MONTH_TO_QUARTER = {3:1, 6:2, 9:3, 12:4}`
- Column header: `r'As of (\w+) (\d{1,2}),? (\d{4})'` → `YYYY-QN`
- Every table outputs **2 periods** (prior year + current period)

---

## Key Files
| File | Purpose |
|------|---------|
| `test_fitz_bs.py` | **PRIMARY** Balance sheet extractor (UNONCONSBS + UCONSBS). Uses fitz find_tables(). Works on both Q1 annual and Q4 simplified. Run: `python test_fitz_bs.py [pdf_path]` |
| `test_extraction.py` | Older camelot-based extraction + validation script (all 11 tables, some bugs remain) |
| `config.py` | All 228 COLUMN_CODES, COLUMN_HEADERS, and label maps for every table |
| `extract_unonconsbs_csv.py` | Standalone UNONCONSBS → CSV validator (confirmed 82/82 match) |
| `output_unonconsbs.csv` | UNONCONSBS validation output |
| `q4_tables_raw.txt` | Raw camelot output for Q4 UNONCONSBS+UCONSBS |
| `test_fitz_fairval.py` | **DONE** FAIRVAL extractor. Single-period, 4-col, 3-section. Works Q1+Q3, NOT FOUND on Q4. |
| `raw_fitz_unonconsbs.txt` | Raw fitz find_tables() output for UNONCONSBS inspection |
| `raw_fitz_uconsbs.txt` | Raw fitz find_tables() output for UCONSBS inspection |
| `raw_fitz_fairval.txt` | Raw fitz find_tables() output for FAIRVAL inspection |
| `output_fitz_unonconsbs.csv` | UNONCONSBS CSV output from test_fitz_bs.py |
| `output_fitz_uconsbs.csv` | UCONSBS CSV output from test_fitz_bs.py |
| `output_fitz_fairval.csv` | FAIRVAL CSV output from test_fitz_fairval.py |
| `Project_information/test script/Table 2/uconsbs_assets_q1.csv` | Manually-verified reference CSV for UCONSBS ASSETS section (22 rows, both periods) |

---

## Helper Functions (test_extraction.py)

| Function | Purpose |
|----------|---------|
| `_clean(text)` | Collapse whitespace, strip |
| `_norm(text)` | lowercase + strip `(*N)` footnotes + strip `[brackets]` |
| `_parse_num(text)` | Parse financial numbers: `(1,234)` → -1234, `[35,390]` → 35390 |
| `_split(cell)` | Split cell on `\n`, return non-empty stripped items |
| `_period_from_header(text)` | `"As of March 31, 2026"` → `"2026-Q1"` |
| `_find_page(doc, *kws, start)` | Find first page where ALL keywords appear in text |
| `_get_tables(pdf_path, page_1idx)` | camelot lattice read, returns list of DataFrames |
| `_vals_by_fitz(doc, pg, x_min, x_max, y_min, y_max)` | Extract numeric words by x-band using fitz coords |
| `_join_continued(items)` | Merge split label lines (lowercase start or 'Network' → join to previous) |
| `_seq_match(pdf_labels, label_map)` | Sequential alignment: skips unknown PDF labels, does NOT skip config entries |

---

## Table Extraction Status (Q1 Annual pr0515en-3-03.pdf)

| Table | Page | Shape | Status | Notes |
|-------|------|-------|--------|-------|
| UNONCONSBS | p17 | (9,3) lattice | ✅ 41/41 both periods | Body labels used to avoid TOC match |
| UCONSBS | p34 | (9,3) lattice | ✅ 22/22 both periods | fitz fallback for Q1 6-col displaced layout |
| FAIRVAL | p46 | (7,4) fitz | ✅ 18/18 single period | test_fitz_fairval.py; [brackets]=positive derivatives |
| HELDMAT | p53 tbl0 | (6,4) fitz | ✅ 11 rows single period | test_fitz_bonds.py; no Foreign sec/bonds |
| POLRES | p53 tbl1 | (6,4) fitz | ✅ 15 rows single period | test_fitz_bonds.py; includes Foreign sec+bonds |
| ASALSEC | p54 tbl0 | (6,4) fitz | ✅ 21 rows single period | test_fitz_asalsec.py; "Other (*)" stripped; classifier by "exceeds cost" |
| ASALSECSOLD | p55 tbl0 | (3,4) fitz | ✅ 10 rows single period | test_fitz_asalsecsold_monheld.py; absent in Q3 (fiscal-year table) |
| MONHELD | p55 tbl1 Q1 / p44 Q3 | (3,6) fitz | ✅ 1 row single period | 6 cols: cons/cost/diff/exceed/notexc |
| CURRELDER | p56 tbl1 | (4,6) fitz | ✅ 9 rows single period | test_fitz_currelder_intrateder.py; 3 deferred + 5 fair_value sub-rows + Total |
| INTRATEDER | p56 tbl2 | (3,6) fitz | ✅ 2 rows single period | 1 deferred row + Total; "exceptional" method handled for other PDFs |
| AHELDMAT | p16 tbl2 | (6,11) fitz | ✅ 3/3 both periods | test_fitz_aheldmat.py; col1=P1 Book, col6=P2 Book; hyphen-newline fix needed |

## Table Extraction Status (Q4 Simplified pr0213en-03.pdf)

| Table | Page | Status | Notes |
|-------|------|--------|-------|
| UNONCONSBS | p7 | ✅ 23/23 both periods | Bracket labels `[JGB]` handled by `_norm` stripping `[]` |
| UCONSBS | p15 | ✅ 15/15 both periods | Clean (9,3) camelot, no fitz needed |
| FAIRVAL | — | NOT FOUND (expected) | Absent in Q4 simplified ✓ |
| HELDMAT–CURRELDER | — | NOT FOUND (expected) | Absent in simplified format |
| AHELDMAT | p6 | ✅ 1/3 per period | Period labels now `2025-Q1` / `2025-Q4` |

---

## Critical Design Decisions

### Table Finding Strategy
Use **body label text** (not section title alone) to find each table page.
The TOC on p1 of every PDF contains all section titles — matching title only returns p1.
Always combine section title + a unique body label:

| Table | Search Keywords |
|-------|----------------|
| UNONCONSBS | `'Monetary claims bought', 'Policy loans', 'Agency accounts receivable'` |
| UCONSBS | `'Unaudited Consolidated Balance Sheets', 'Cash and deposits'` + `start=unon_pg+1` |
| FAIRVAL | header=`'fair values of financial instruments'`, body=`['monetary claims bought','reserve for possible loan losses','bonds payable','held-to-maturity bonds']`, min_matches=3 |
| HELDMAT+POLRES | `'Held-to-maturity Bonds', 'fair value exceeds'` |
| ASALSEC | `'Available-for-sale Securities', 'fair value exceeds', 'Cost'` |
| ASALSECSOLD+MONHELD | `'Available-for-sale Securities Sold during the Fiscal Year'` |
| CURRELDER+INTRATEDER | `'Fair value hedge accounting', 'Forward foreign exchange'` |
| AHELDMAT | `'Assets held-to-maturity in trust', 'assets held for reserves in trust'` |

### fitz find_tables() — PRIMARY approach for Balance Sheets (UNONCONSBS + UCONSBS)
`page.find_tables()` uses PDF vector border lines for exact cell detection — no displacement.
Completely replaces camelot for these two tables. Key design:

**TABLE_FINGERPRINTS dict:**
```python
TABLE_FINGERPRINTS = {
    "UNONCONSBS": {
        "header":      "non-consolidated balance sheets",
        "body_labels": ["monetary claims bought", "policy loans",
                        "agency accounts receivable", "reinsurance payables"],
        "min_matches": 3,
    },
    "UCONSBS": {
        # "unaudited consolidated" avoids substring match with "non-consolidated"
        "header":      "unaudited consolidated balance sheets",
        "body_labels": ["reinsurance receivables", "intangible fixed assets",
                        "reserve for possible loan losses", "liability for retirement benefits"],
        "min_matches": 2,
    },
}
```
Header alone hits TOC page. Requiring N body labels on same page gives unique match.
UCONSBS must use "unaudited consolidated" — "consolidated" is substring of "non-consolidated".

**Period detection (dual fallback):**
1. Try column names (Q1 annual: `"1-As of March\n31, 2025"`)
2. Fallback: row-0 cells (Q4: generic `"Col0"`, `"Col1"` column names)

**Two-column layout (Q1 annual, ncols >= 6):**
- Cols 0-2: ASSETS section (label + 2025 + 2026)
- Cols 3-5: LIABILITIES+NET ASSETS section (label + 2025 + 2026)
- Process both sides separately then sort: ASSETS → LIABILITIES → NET ASSETS

**Verified results:**
- Q4 pr0213en-03.pdf: UNONCONSBS 54 rows, UCONSBS 39 rows ✓
- Q1 pr0515en-3-03.pdf: UNONCONSBS 82 rows, UCONSBS 46 rows ✓ (Money held in trust 2026 = 8,039,836 correct)

### Sequential Label Matching (`_seq_match`)
Never use dict lookup. Iterate PDF labels in order; for each label scan config from current pointer forward.
- If PDF label matches config: record (map_idx, pdf_idx), advance map_ptr
- If PDF label NOT in config: skip it, **do NOT advance map_ptr**
- Config entries not found in PDF: silently absent (→ None in output)

### `_norm` strips `[brackets]`
Simplified PDFs indent sub-items as `[Japanese government bonds]`. `_norm` strips `[` and `]` so these match config entries without brackets.

### `_join_continued`
Merges label lines split across rows:
- Line starts lowercase → join to previous (with space, or no space if previous ends with `-`)
- Line starts with word in `_CONT_WORDS = {'network'}` → join to previous
- Line starts with `"("` AND is NOT a value bracket like `"(901)"` → join to previous (handles `"(losses) on available-for-sale securities"`)
- Skip `None` and `"None"` items (fitz returns Python None for empty cells)
- Removed 'bonds' from continuation set (was causing false merges)

---

## Config Label Maps
| Config Key | Entries | Used by |
|-----------|---------|---------|
| `UNONCONSBS_LABELS` | 42 | UNONCONSBS — ASSETS only |
| `UCONSBS_LABELS` | 23 | UCONSBS — ASSETS only |
| `FAIRVAL_LABELS` | 13 | FAIRVAL (cons + FV cols) |
| `BONDS_SEC_LABELS` | dict with `exceed`/`notexc` | HELDMAT + POLRES |
| `ASALSEC_LABELS` | dict with `exceed`/`notexc` | ASALSEC |
| `ASALSECSOLD_LABELS` | ~8 entries | ASALSECSOLD |
| `CURRELDER_LABELS` | ~6 entries | CURRELDER |
| `INTRATEDER_LABELS` | ~2 entries | INTRATEDER |
| `AHELDMAT_LABELS` | 3 entries | AHELDMAT |

---

## Known Bugs / TODO

### ⚠️ POLRES 20/24, ASALSEC 28/32 (4 missing each)
Likely a label mismatch in the exceed/notexc section parsing. Need to print extracted labels vs config to find the gap.

### ⚠️ FAIRVAL 25/26 (1 missing)
One config entry not found in the PDF label list or period detection issue.

---

## Extraction Scripts — COMPLETED
| Script | Tables | Verified |
|--------|--------|---------|
| `test_fitz_bs.py` | UNONCONSBS, UCONSBS | Q4 (54+39 rows), Q1 (82+46 rows) |
| `test_fitz_fairval.py` | FAIRVAL | Q1 p46 (18r), Q3 p36 (18r), Q4 NOT FOUND |
| `test_fitz_bonds.py` | HELDMAT, POLRES | Q1 p53 (11r+15r), Q3 p43 (11r+15r), Q4 NOT FOUND |
| `test_fitz_asalsec.py` | ASALSEC | Q1 p54 (21r), Q3 p44 (21r), Q4 NOT FOUND |
| `test_fitz_asalsecsold_monheld.py` | ASALSECSOLD, MONHELD | Q1 p55 (10r+1r), Q3 ASALSECSOLD NOT FOUND + MONHELD p44 (1r), Q4 NOT FOUND |
| `test_fitz_currelder_intrateder.py` | CURRELDER, INTRATEDER | Q1 p56 (9r+2r), Q3 NOT FOUND, Q4 NOT FOUND |
| `test_fitz_aheldmat.py` | AHELDMAT | Q1 p16 (3r), Q3 p11 (3r), Q4 p6 (3r) — all PDFs |

## Next Steps (in order)
1. ~~HELDMAT + POLRES~~ DONE
2. ~~ASALSEC~~ DONE
3. ~~ASALSECSOLD + MONHELD~~ DONE
4. ~~CURRELDER + INTRATEDER~~ DONE
5. ~~AHELDMAT~~ DONE
6. Write `extractor.py` (production module)
7. Write `file_generator.py`, `orchestrator.py`, `main.py`
