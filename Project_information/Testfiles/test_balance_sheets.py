"""
test_balance_sheets.py
Extract Non-Consolidated and Consolidated Balance Sheets from any quarterly PDF.

Strategy:
  1. fitz text search with TABLE_FINGERPRINTS (header keyword + body label array)
     — header alone fails because every title also appears in the TOC.
     — requiring N body labels on the same page gives a unique fingerprint.
  2. camelot lattice for table extraction (all 3 sections: ASSETS / LIABILITIES / NET ASSETS)
  3. Raw camelot → TXT (debug), parsed data → CSV

Run:
  python test_balance_sheets.py                              # Q4 simplified
  python test_balance_sheets.py path/to/some_pdf.pdf        # any PDF
"""

import re
import csv
import sys
from pathlib import Path

try:
    import fitz
except ImportError:
    sys.exit("pip install pymupdf")

try:
    import camelot
    import warnings
    warnings.filterwarnings("ignore")
except ImportError:
    sys.exit("pip install camelot-py[cv]")

# ── Default PDF ────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
DEFAULT_PDF = _HERE / "Project_information" / "sample" / "pr0213en-03.pdf"

# ── Table fingerprints ─────────────────────────────────────────────────────────
# 'header'       : keyword that must appear in page text (case-insensitive)
# 'body_labels'  : list of labels that appear in the table body
# 'min_matches'  : how many body_labels must match before we accept the page
#
# Design: header alone is ambiguous (TOC contains all headers).
# Adding body_labels makes the match unique to the actual table page.
TABLE_FINGERPRINTS = {
    "UNONCONSBS": {
        "header": "non-consolidated balance sheets",
        "body_labels": [
            "monetary claims bought",
            "policy loans",
            "agency accounts receivable",
            "reinsurance payables",
        ],
        "min_matches": 3,
    },
    "UCONSBS": {
        # "unaudited consolidated" avoids matching "unaudited non-consolidated" as substring
        "header": "unaudited consolidated balance sheets",
        "body_labels": [
            "reinsurance receivables",
            "intangible fixed assets",
            "reserve for possible loan losses",
            "liability for retirement benefits",
        ],
        "min_matches": 2,
    },
}

# ── Helpers ────────────────────────────────────────────────────────────────────
_NOTE_RE = re.compile(r"\s*\(\*\d+\)")
_BRKT_RE = re.compile(r"^\([\d,]+(?:\.\d+)?\)$")
_CONT_WORDS = {"network"}

# Section header strings that signal a section change (no data value)
_SECTION_HEADERS = {
    "assets": "ASSETS",
    "assets:": "ASSETS",
    "liabilities": "LIABILITIES",
    "liabilities:": "LIABILITIES",
    "net assets": "NET ASSETS",
    "net assets:": "NET ASSETS",
}

MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,
    "may":5,"june":6,"july":7,"august":8,
    "september":9,"october":10,"november":11,"december":12
}
QUARTER_MAP = {3:1, 6:2, 9:3, 12:4}


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", str(t)).strip()


def _norm(t: str) -> str:
    """Lowercase, strip footnote markers (*N) and [bracket] sub-item notation."""
    t = _NOTE_RE.sub("", _clean(t)).lower()
    return re.sub(r"[\[\]]", "", t)


def _parse_num(text: str):
    """Parse a financial number string. Returns float or None."""
    t = _clean(text)
    if not t or t in ("-", "—", ""):
        return None
    if _BRKT_RE.match(t):
        return -float(t[1:-1].replace(",", ""))
    if re.match(r"^-?[\d,]+(?:\.\d+)?$", t):
        return float(t.replace(",", ""))
    m = re.search(r"\([\d,]+\)|[\d,]+", t)
    if m:
        raw = m.group()
        return (-float(raw[1:-1].replace(",", ""))
                if raw.startswith("(") else float(raw.replace(",", "")))
    return None


def _split(cell: str) -> list:
    """Split cell text on newlines, dropping blank items."""
    return [s.strip() for s in str(cell).split("\n") if s.strip()]


def _join_continued(items: list) -> list:
    """Merge label lines that were split across rows by PDF layout."""
    out = []
    for item in items:
        if out and item:
            if item[0].islower():
                sep = "" if out[-1].endswith("-") else " "
                out[-1] = out[-1] + sep + item
            elif item.split()[0].lower() in _CONT_WORDS:
                out[-1] = out[-1] + " " + item
            else:
                out.append(item)
        else:
            out.append(item)
    return out


def _period_from_header(text: str):
    """'As of March 31, 2025' → '2025-Q1'  (returns None if no match)"""
    m = re.search(r"As of\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})", _clean(text), re.I)
    if not m:
        return None
    mn = MONTH_MAP.get(m.group(1).lower(), 0)
    q  = QUARTER_MAP.get(mn, 0)
    return f"{m.group(3)}-Q{q}" if q else None


# ── Dynamic page finder ────────────────────────────────────────────────────────
def find_table_page(doc, fingerprint: dict, start: int = 0):
    """
    Scan pages (>= start) for one that satisfies the fingerprint:
      - fingerprint['header'] is present (case-insensitive substring)
      - at least fingerprint['min_matches'] of fingerprint['body_labels'] are present

    Returns 0-indexed page number, or None if not found.

    Why both header AND body_labels:
      The PDF table of contents on page 1 contains every section title.
      Header alone would match the TOC page. Body labels appear only on
      the actual table page, making the combined fingerprint unambiguous.
    """
    header_kw  = fingerprint["header"].lower()
    body_kws   = [lbl.lower() for lbl in fingerprint["body_labels"]]
    min_n      = fingerprint.get("min_matches", len(body_kws))

    for pg in range(start, len(doc)):
        txt = doc[pg].get_text("text").lower()
        if header_kw not in txt:
            continue
        n_matched = sum(1 for kw in body_kws if kw in txt)
        if n_matched >= min_n:
            return pg
    return None


# ── Balance-sheet table parser ─────────────────────────────────────────────────
# "Total X" rows carry data but also reset the active section so they land
# in the right bucket when the final rows are reordered.
_SECTION_OVERRIDES = {
    "total assets":                    "ASSETS",
    "total net assets":                "NET ASSETS",
    "total liabilities and net assets":"NET ASSETS",
}


def _process_label_col(df, label_col, val_cols, periods, rows):
    """
    Scan every row of `df` (skipping row 0 header), reading labels from
    `label_col` and values from `val_cols` (one per period, positionally matched).

    Section transitions are driven by _SECTION_HEADERS; _SECTION_OVERRIDES
    re-tags Total-summary rows so they sort into the right section bucket.
    val_idx resets each camelot row (items in one cell are positionally aligned
    with items in the same row's value cell).
    """
    current_section = None

    for ri in range(1, len(df)):
        labels    = _join_continued(_split(str(df.iloc[ri, label_col])))
        val_lists = {}
        for pi, ci in enumerate(val_cols):
            if ci < df.shape[1] and pi < len(periods):
                val_lists[periods[pi]] = _split(str(df.iloc[ri, ci]))

        val_idx = 0
        for label in labels:
            n = _norm(label)

            # Total-summary rows: override section BEFORE appending
            if n in _SECTION_OVERRIDES:
                current_section = _SECTION_OVERRIDES[n]

            # Section header: update tracker, skip (no value row)
            if n in _SECTION_HEADERS:
                current_section = _SECTION_HEADERS[n]
                continue

            if current_section is None:
                val_idx += 1
                continue

            vdict = {}
            for p in periods:
                vlist = val_lists.get(p, [])
                raw   = vlist[val_idx] if val_idx < len(vlist) else None
                vdict[p] = _parse_num(raw) if raw else None

            rows.append({"section": current_section, "label": label, "values": vdict})
            val_idx += 1


def _parse_bs_df(df):
    """
    Parse a camelot balance-sheet DataFrame.

    Handles two layouts automatically:
      3-col (Q4 simplified):  col0=labels, col1=period1, col2=period2
        ASSETS / LIABILITIES / NET ASSETS sections stacked vertically.

      6-col (Q1 annual):      ASSETS left (cols 0-2) + LIABILITIES right (cols 3-5)
        LEFT  col0 = ASSETS labels,      col1/2 = values
        RIGHT col3 = LIABILITIES labels, col4/5 = values
        After processing both sides, rows are sorted ASSETS -> LIABILITIES -> NET ASSETS.

    Returns:
      periods : list of period strings, e.g. ['2025-Q1', '2026-Q1']
      rows    : list of dicts {'section', 'label', 'values': {period: float|None}}
    """
    # Detect periods from cols 1 and 2 of row 0 (always the first value columns)
    periods = []
    for ci in range(1, min(df.shape[1], 3)):
        cell = " ".join(_split(str(df.iloc[0, ci])))
        p    = _period_from_header(cell)
        if p and p not in periods:
            periods.append(p)

    if not periods:
        return [], []

    rows  = []
    ncols = df.shape[1]

    if ncols >= 6:
        # Two-column layout: left side ASSETS+NET ASSETS, right side LIABILITIES
        _process_label_col(df, label_col=0, val_cols=[1, 2], periods=periods, rows=rows)
        _process_label_col(df, label_col=3, val_cols=[4, 5], periods=periods, rows=rows)
        # Re-sort into natural reading order
        assets = [r for r in rows if r["section"] == "ASSETS"]
        liabs  = [r for r in rows if r["section"] == "LIABILITIES"]
        nets   = [r for r in rows if r["section"] == "NET ASSETS"]
        rows   = assets + liabs + nets
    else:
        # Single-column layout: stacked sections
        _process_label_col(df, label_col=0, val_cols=[1, 2], periods=periods, rows=rows)

    return periods, rows


# ── Stream-based parser for two-column layout ─────────────────────────────────
def _parse_stream_two_col(df, periods):
    """
    Parse camelot stream output for a two-column (ASSETS+LIABILITIES side-by-side)
    balance sheet.  Shape is typically (N, 5):

      col0 = left label (may span multiple rows via continuation)
      col1 = left period-1 value
      col2 = left period-2 value  (first number; sometimes has right-label text appended)
      col3 = right period-1 value  OR  right-label text continuation
      col4 = right period-2 value

    Strategy: row-by-row state machine.
      - Accumulate left label words until col1 has a number → emit left item.
      - Accumulate right label words (text in col2 after the leading number, or
        text in col3 when col3 is not a number) until col3 has a number → emit right item.
    """
    rows       = []
    left_buf   = []   # label words accumulating for current left item
    right_buf  = []   # label words accumulating for current right item
    left_sec   = None
    right_sec  = None

    for ri in range(len(df)):
        c = [_clean(str(df.iloc[ri, ci])) for ci in range(min(5, df.shape[1]))]
        while len(c) < 5:
            c.append("")

        n0 = _norm(c[0])

        # ── Section header in col0 (left side) ────────────────────────────
        if n0 in _SECTION_HEADERS:
            left_sec = _SECTION_HEADERS[n0]
            continue

        # ── Parse left 2025 (col1) and left 2026 (first number in col2) ───
        lv1 = _parse_num(c[1])
        m   = re.match(r"^(-?[\d,]+(?:\.\d+)?|\([\d,]+(?:\.\d+)?\))", c[2].strip())
        lv2 = _parse_num(m.group()) if m else None

        # Text in col2 after the leading number = right-side label contribution
        c2_text = c[2][m.end():].strip().lstrip("\\n").strip() if m else (
            c[2] if c[2] and _parse_num(c[2]) is None else "")

        # ── Parse right values (col3 = period-1, col4 = period-2) ─────────
        rv1 = _parse_num(c[3])
        rv2 = _parse_num(c[4])

        # col3 is label text when it can't be parsed as a number
        c3_text = c[3] if rv1 is None else ""

        # ── Section header in right-side text ─────────────────────────────
        for txt in (c2_text, c3_text):
            nt = _norm(txt)
            if nt in _SECTION_HEADERS:
                right_sec = _SECTION_HEADERS[nt]
                # Clear this text — it's a header, not a label fragment
                if txt == c2_text:
                    c2_text = ""
                else:
                    c3_text = ""

        # ── Accumulate label buffers ───────────────────────────────────────
        if c[0] and n0 not in _SECTION_HEADERS:
            left_buf.append(c[0])
        if c2_text and _norm(c2_text) not in _SECTION_HEADERS:
            right_buf.append(c2_text)
        if c3_text and _norm(c3_text) not in _SECTION_HEADERS:
            right_buf.append(c3_text)

        # ── Emit left item ─────────────────────────────────────────────────
        if lv1 is not None and left_buf and left_sec:
            label = " ".join(left_buf)
            nl    = _norm(label)
            if nl in _SECTION_OVERRIDES:
                left_sec = _SECTION_OVERRIDES[nl]
            rows.append({"section": left_sec, "label": label,
                         "values": dict(zip(periods[:2], [lv1, lv2]))})
            left_buf = []

        # ── Emit right item ────────────────────────────────────────────────
        if rv1 is not None and right_buf and right_sec:
            label = " ".join(right_buf)
            nl    = _norm(label)
            if nl in _SECTION_OVERRIDES:
                right_sec = _SECTION_OVERRIDES[nl]
            rows.append({"section": right_sec, "label": label,
                         "values": dict(zip(periods[:2], [rv1, rv2]))})
            right_buf = []

    # Re-sort into natural reading order
    assets = [r for r in rows if r["section"] == "ASSETS"]
    liabs  = [r for r in rows if r["section"] == "LIABILITIES"]
    nets   = [r for r in rows if r["section"] == "NET ASSETS"]
    return assets + liabs + nets


def _has_displacement(rows):
    """Return True if any label looks like a leaked numeric value (camelot artifact)."""
    return any(re.match(r"^\d", r["label"]) for r in rows)


# ── High-level extractor ───────────────────────────────────────────────────────
def extract_balance_sheet(pdf_path: Path, doc, name: str,
                          fingerprint: dict, start: int = 0):
    """
    Find, extract, and parse one balance-sheet table.
    Tries camelot lattice first; if displacement is detected in a 6-col table,
    falls back to camelot stream which gives a cleaner row-per-item structure.

    Returns (page_0idx, periods, rows) — all None on failure.
    Side-effect: writes raw_<name>.txt with the raw camelot output.
    """
    pg = find_table_page(doc, fingerprint, start=start)
    if pg is None:
        print(f"  {name}: page NOT FOUND (searched from p{start+1})")
        return None, None, None

    print(f"  {name}: found on p{pg + 1}")

    def _run_camelot(flavor):
        return camelot.read_pdf(
            str(pdf_path), pages=str(pg + 1),
            flavor=flavor, suppress_stdout=True
        )

    # ── Try lattice first ──────────────────────────────────────────────────────
    tbls = _run_camelot("lattice")

    # Write raw lattice TXT
    raw_path = _HERE / f"raw_{name.lower()}.txt"
    with open(raw_path, "w", encoding="utf-8") as f:
        for ti, tbl in enumerate(tbls):
            f.write(f"\n{'='*60}\n")
            f.write(f"[lattice] Table {ti}  shape={tbl.df.shape}  "
                    f"accuracy={tbl.parsing_report['accuracy']:.1f}\n")
            f.write(f"{'='*60}\n")
            f.write(tbl.df.to_string())
            f.write("\n")
    print(f"    Raw TXT  -> {raw_path.name}")

    periods, rows = [], []
    for ti, tbl in enumerate(tbls):
        p, r = _parse_bs_df(tbl.df)
        if p and r:
            periods, rows = p, r
            print(f"    Lattice table {ti}  shape={tbl.df.shape}  "
                  f"periods={p}  rows={len(r)}")
            break

    # ── Displacement check: if labels contain leaked numbers, fall back to stream
    if rows and tbl.df.shape[1] >= 6 and _has_displacement(rows):
        print(f"    Displacement detected — retrying with stream flavor")
        stm_tbls = _run_camelot("stream")

        # Append stream TXT to raw file
        with open(raw_path, "a", encoding="utf-8") as f:
            f.write("\n\n")
            for ti, tbl in enumerate(stm_tbls):
                f.write(f"\n{'='*60}\n")
                f.write(f"[stream] Table {ti}  shape={tbl.df.shape}  "
                        f"accuracy={tbl.parsing_report['accuracy']:.1f}\n")
                f.write(f"{'='*60}\n")
                f.write(tbl.df.to_string())
                f.write("\n")

        for ti, tbl in enumerate(stm_tbls):
            if tbl.df.shape[1] >= 5:
                sr = _parse_stream_two_col(tbl.df, periods)
                if sr and not _has_displacement(sr):
                    print(f"    Stream table {ti}  shape={tbl.df.shape}  rows={len(sr)}")
                    return pg, periods, sr

    if rows:
        return pg, periods, rows

    print(f"  {name}: parsed 0 rows")
    return pg, None, None


# ── CSV writer ─────────────────────────────────────────────────────────────────
def write_csv(name: str, periods: list, rows: list):
    out_path = _HERE / f"output_{name.lower()}_bs.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Section", "Label"] + periods)
        for row in rows:
            vals = [
                ("" if row["values"].get(p) is None else row["values"][p])
                for p in periods
            ]
            w.writerow([row["section"], row["label"]] + vals)
    print(f"    CSV      -> {out_path.name}")


# ── Console print helper ───────────────────────────────────────────────────────
def print_table(name: str, periods: list, rows: list):
    print(f"\n{'-'*70}")
    print(f"  {name}  ({len(rows)} rows)")
    print(f"{'-'*70}")
    hdr = f"  {'Section':<14} {'Label':<50} " + "  ".join(f"{p:>12}" for p in periods)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for row in rows:
        vals = "  ".join(
            f"{'':>12}" if row["values"].get(p) is None
            else f"{row['values'][p]:>12,.0f}"
            for p in periods
        )
        print(f"  {row['section']:<14} {row['label']:<50} {vals}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    print(f"\nPDF: {pdf_path.name}")
    print("=" * 70)

    doc = fitz.open(str(pdf_path))

    # ── UNONCONSBS ─────────────────────────────────────────────────────────────
    print("\n[1] Non-Consolidated Balance Sheets")
    unon_pg, p1, r1 = extract_balance_sheet(
        pdf_path, doc, "UNONCONSBS", TABLE_FINGERPRINTS["UNONCONSBS"], start=0
    )
    if r1:
        write_csv("UNONCONSBS", p1, r1)
        print_table("UNONCONSBS", p1, r1)

    # ── UCONSBS — start search after UNONCONSBS page ───────────────────────────
    print("\n[2] Consolidated Balance Sheets")
    ucons_start = (unon_pg + 1) if unon_pg is not None else 0
    _, p2, r2 = extract_balance_sheet(
        pdf_path, doc, "UCONSBS", TABLE_FINGERPRINTS["UCONSBS"], start=ucons_start
    )
    if r2:
        write_csv("UCONSBS", p2, r2)
        print_table("UCONSBS", p2, r2)

    doc.close()

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    if r1:
        print(f"  UNONCONSBS : {len(r1):3d} rows  periods={p1}")
    else:
        print("  UNONCONSBS : NOT EXTRACTED")
    if r2:
        print(f"  UCONSBS    : {len(r2):3d} rows  periods={p2}")
    else:
        print("  UCONSBS    : NOT EXTRACTED")


if __name__ == "__main__":
    main()
