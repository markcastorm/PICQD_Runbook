# PICQD Pipeline

Automated extraction of 228 financial data points from Japan Post Insurance (JP Life) quarterly PDF releases into structured Excel + ZIP output.

## Overview

Japan Post Insurance publishes quarterly financial results as English-language PDFs at:
`https://www.jp-life.japanpost.jp/english/news/en_news_index.html`

This pipeline scrapes the latest release, parses 11 distinct table types from the PDF using PyMuPDF, maps values to 228 standardized column codes, and writes the output to a timestamped Excel workbook with ZIP archive.

### What it extracts

| Table | Code Prefix | Columns | Description |
|---|---|---|---|
| UNONCONSBS | `PICQD.UNONCONSBS.*` | 42 | Non-Consolidated Balance Sheet (Assets) |
| UCONSBS | `PICQD.UCONSBS.*` | 23 | Unaudited Consolidated Balance Sheet (Assets) |
| FAIRVAL | `PICQD.FAIRVAL.*` | 34 | Fair Values of Financial Instruments |
| HELDMAT | `PICQD.HELDMAT.*` | 24 | Held-to-maturity Bonds |
| POLRES | `PICQD.POLRES.*` | 24 | Policy-reserve-matching Bonds |
| ASALSEC | `PICQD.ASALSEC.*` | 36 | Available-for-sale Securities |
| ASALSECSOLD | `PICQD.ASALSECSOLD.*` | 24 | Available-for-sale Securities Sold |
| MONHELD | `PICQD.MONHELD.*` | 2 | Specified Money Held in Trust |
| CURRELDER | `PICQD.CURRELDER.*` | 12 | Currency-related Derivatives |
| INTRATEDER | `PICQD.INTRATEDER.*` | 4 | Interest-rate Derivatives |
| AHELDMAT | `PICQD.AHELDMAT.*` | 3 | Assets Held-to-maturity in Trust |

## Requirements

- Python 3.9+
- Google Chrome (for automated scraping mode)

### Python dependencies

```
pymupdf          # PDF table extraction (imported as fitz)
openpyxl         # Excel workbook generation
requests         # PDF download
beautifulsoup4   # HTML parsing
selenium         # Browser automation (scraper)
selenium-stealth # Anti-detection for Selenium
undetected-chromedriver  # Chrome automation
```

Install all dependencies:
```bash
pip install pymupdf openpyxl requests beautifulsoup4 selenium selenium-stealth undetected-chromedriver
```

## Usage

### Full auto pipeline (scrape + extract + generate)

```bash
python main.py
```

This will:
1. Scrape the JP Life news page for the latest financial results PDF
2. Download the PDF to `downloads/`
3. Extract all 228 data points
4. Write Excel + ZIP to `output/latest/` and `output/<timestamp>/`

### Manual mode (local PDF, skip scraper)

```bash
# Single PDF
python main.py path/to/file.pdf

# Multiple PDFs (combined output)
python main.py file1.pdf file2.pdf file3.pdf
```

### Examples with included samples

```bash
# Q1 annual report (all tables present)
python main.py Project_information/pr0515en-01_03.pdf

# Q3 quarterly report (derivatives + sold tables absent)
python main.py Project_information/pr1114en-05.pdf

# Q4 quarterly report (only BS + AHELDMAT)
python main.py Project_information/pr0214en-03.pdf
```

## Output

```
output/
  latest/                            # Wiped and repopulated each run
    PICQD_DATA_<YYYYMMDD>.xlsx
    PICQD_DATA_<YYYYMMDD>.zip
  <YYYYMMDD_HHMMSS>/                 # Archived copy
    PICQD_DATA_<YYYYMMDD>.xlsx
    PICQD_DATA_<YYYYMMDD>.zip
```

### Excel layout

| | A | B | C | ... | HU |
|---|---|---|---|---|---|
| **Row 1** | | PICQD.UNONCONSBS.ASSET.CASHDEP.Q | PICQD.UNONCONSBS.ASSET.CASHDEP.CASH.Q | ... | PICQD.AHELDMAT.BOOK.AHELDTOM.Q |
| **Row 2** | | Cash and deposits | Cash | ... | Other money held in trust |
| **Row 3** | 2024-Q1 | 1152730 | 723 | ... | 3642.4 |
| **Row 4** | 2025-Q1 | 1970343 | 582 | ... | 3874.5 |

- **Row 1**: 228 column codes (blue fill, bold)
- **Row 2**: Human-readable headers (green fill, italic)
- **Row 3+**: One row per period, float values (empty cell where None)
- Freeze panes at B3 for easy scrolling

## Architecture

```
main.py
  |
  +-- orchestrator.py          # Auto mode: scrape -> extract -> generate
  |     |
  |     +-- scraper.py         # Selenium + undetected_chromedriver
  |     +-- extractor.py       # Orchestrates 8 fitz modules
  |     +-- file_generator.py  # Writes Excel + ZIP
  |
  +-- _run_manual()            # Manual mode: extract -> generate
        |
        +-- extractor.py
        +-- file_generator.py

extractor.py
  |
  +-- fitz_bs.py                    # UNONCONSBS + UCONSBS
  +-- fitz_fairval.py               # FAIRVAL
  +-- fitz_bonds.py                 # HELDMAT + POLRES
  +-- fitz_asalsec.py               # ASALSEC
  +-- fitz_asalsecsold_monheld.py   # ASALSECSOLD + MONHELD
  +-- fitz_currelder_intrateder.py  # CURRELDER + INTRATEDER
  +-- fitz_aheldmat.py              # AHELDMAT

config.py                          # All 228 column codes, labels, settings
```

### Key algorithms

- **Fingerprint-based page detection**: Each fitz module locates its target page by searching for a combination of header keywords and body labels. This avoids false matches on table-of-contents pages.

- **Sequential label matching (`_seq_match`)**: Config labels are walked in order; for each label, rows are scanned forward from the current pointer. The pointer only advances on a successful match. This handles duplicate labels (e.g. "Other assets" appears twice in UNONCONSBS) and rows present in the PDF but absent from config.

- **`_norm()` normalization**: Strips footnote markers `(*N)`, square brackets `[]`, collapses whitespace, and lowercases. Square bracket stripping is essential because Q2/Q3/Q4 PDFs display securities sub-items as `[Japanese government bonds]`.

- **Two-period extraction**: Q1 annual PDFs contain both prior-year and current-year columns. The pipeline detects this and returns two records. Q3/Q4 PDFs also have prior-year reference columns.

## Japan Post Insurance Fiscal Year

The fiscal year runs April 1 to March 31.

| Quarter | Period End | PDF Release | Tables Available |
|---|---|---|---|
| Q1 (Annual) | March 31 | May | All 11 tables |
| Q2 | June 30 | August | 8 tables (no ASALSECSOLD, CURRELDER, INTRATEDER) |
| Q3 | September 30 | November | 8 tables (no ASALSECSOLD, CURRELDER, INTRATEDER) |
| Q4 | December 31 | February | 3 tables (UNONCONSBS, UCONSBS, AHELDMAT only) |

The pipeline dynamically handles missing tables — extraction proceeds cleanly regardless of which quarter's PDF is provided.

## Table Availability Matrix

| Table | Q1 (Annual) | Q2/Q3 | Q4 |
|---|---|---|---|
| UNONCONSBS | 6-col, 82 rows | 3-col, 54 rows | 3-col, 54 rows |
| UCONSBS | 6-col, 46 rows | 3-col, 39 rows | 3-col, 39 rows |
| FAIRVAL | Present | Present | Absent |
| HELDMAT | Present | Present | Absent |
| POLRES | Present | Present | Absent |
| ASALSEC | Present | Present | Absent |
| ASALSECSOLD | Present | Absent | Absent |
| MONHELD | Present | Present | Absent |
| CURRELDER | Present | Absent | Absent |
| INTRATEDER | Present | Absent | Absent |
| AHELDMAT | Present (2 periods) | Present (2 periods) | Present (2 periods) |

## Verified Accuracy

The pipeline has been tested against 6 PDFs spanning all quarterly formats with **100% extraction accuracy**:

| PDF | Quarter | Periods | Values Verified | Accuracy |
|---|---|---|---|---|
| pr0515en-3-03.pdf | Q1 FY2026 | 2025-Q1, 2026-Q1 | 257 | 100% |
| pr0515en-01_03.pdf | Q1 FY2025 | 2024-Q1, 2025-Q1 | 257 | 100% |
| pr1114en-06.pdf | Q3 FY2025 | 2025-Q1, 2025-Q3 | 173 | 100% |
| pr1114en-05.pdf | Q3 FY2024 | 2024-Q1, 2024-Q3 | 175 | 100% |
| pr0213en-03.pdf | Q4 FY2024 | single period | ~34 | 100% |
| pr0214en-03.pdf | Q4 FY2024 | 2024-Q1, 2024-Q4 | 78 | 100% |
| **Total** | | | **~974** | **100%** |

All values verified cell-by-cell against PDF screenshots.

## Configuration

Key settings in `config.py`:

| Setting | Default | Description |
|---|---|---|
| `HEADLESS_MODE` | `True` | Run Chrome in headless mode |
| `WAIT_TIMEOUT` | `60` | Selenium wait timeout (seconds) |
| `BYPASS_CACHE` | `False` | `True` = re-download even if already processed |
| `RELEASE_YEAR` | `None` | `None` = latest release; `2026` = specific year |

## State Management

`state.json` tracks the last processed release so the scraper skips re-downloading on subsequent runs. Set `BYPASS_CACHE = True` in `config.py` to force reprocessing.

## Debug Output

Each extraction run creates debug files at:
```
extractor/<YYYYMMDD_HHMMSS>/<pdf_stem>/
  raw_fitz_unonconsbs.txt
  raw_fitz_uconsbs.txt
  raw_fitz_fairval.txt
  raw_fitz_bonds_p<N>.txt
  raw_fitz_asalsec_p<N>.txt
  raw_fitz_sold_monheld_p<N>.txt
  raw_fitz_hedge_p<N>.txt
  raw_fitz_aheldmat_p<N>.txt
```

These contain raw PyMuPDF table dumps for troubleshooting extraction issues.

## Project Structure

```
PICQD_Runbook/
├── main.py                      # Entry point (auto or manual mode)
├── orchestrator.py              # Pipeline coordinator
├── scraper.py                   # Selenium web scraper
├── extractor.py                 # Extraction orchestrator + mapping logic
├── file_generator.py            # Excel + ZIP output writer
├── config.py                    # Column codes, labels, settings
├── fitz_bs.py                   # Balance sheet extractor
├── fitz_fairval.py              # Fair values extractor
├── fitz_bonds.py                # HELDMAT + POLRES bonds extractor
├── fitz_asalsec.py              # Available-for-sale securities extractor
├── fitz_asalsecsold_monheld.py  # Securities sold + money held extractor
├── fitz_currelder_intrateder.py # Derivatives extractor
├── fitz_aheldmat.py             # Assets held-to-maturity extractor
├── CLAUDE.md                    # Full technical reference
├── state.json                   # Scraper state (auto-managed)
├── downloads/                   # Downloaded PDFs
├── output/                      # Generated Excel + ZIP
│   ├── latest/                  # Current run output
│   └── <timestamp>/             # Archived runs
├── extractor/                   # Debug output per run
└── Project_information/         # Reference files and sample PDFs
    ├── PICQD_DATA_20260515.xlsx # Reference output (ground truth)
    └── sample/                  # Sample PDFs for testing
```

## Value Conventions

- All monetary values are stored as Python `float`
- Balance sheet tables: **millions of yen** (as printed in PDF)
- AHELDMAT table: **billions of yen** (different unit)
- Negative values: `(766)` in PDF becomes `-766.0`
- Dashes/empty cells: stored as `None` (blank Excel cell)
- Footnote markers `(*1)`, `(*2)`: stripped before label matching
