"""
test_fitz_bs.py
Extract Non-Consolidated and Consolidated Balance Sheets using PyMuPDF find_tables().

Combines:
  - fitz text search + TABLE_FINGERPRINTS  →  dynamic page detection (no hardcoded pages)
  - fitz find_tables()                     →  border-aware cell extraction (no displacement)

Works on both layouts:
  Q4 simplified  (pr0213en-03.pdf)  — 3-col stacked layout
  Q1 annual      (pr0515en-3-03.pdf) — 6-col ASSETS left + LIABILITIES right

Run:
  python test_fitz_bs.py                              # Q4 simplified (default)
  python test_fitz_bs.py path/to/annual.pdf           # any PDF
"""
import re
import csv
import sys
from pathlib import Path

try:
    import fitz
except ImportError:
    sys.exit("pip install pymupdf")

_HERE = Path(__file__).parent
_OUT  = _HERE / "Testfiles"
DEFAULT  = _HERE / "Project_information" / "sample" / "pr0213en-03.pdf"

# ── Table fingerprints ─────────────────────────────────────────────────────────
# header + body_labels → unique page identification (TOC only has header, not body)
TABLE_FINGERPRINTS = {
    "UNONCONSBS": {
        "header":       "non-consolidated balance sheet",
        "body_labels":  ["monetary claims bought", "policy loans",
                         "agency accounts receivable", "reinsurance payables"],
        "min_matches":  3,
    },
    "UCONSBS": {
        # "unaudited consolidated" avoids matching "unaudited non-consolidated" as substring
        "header":       "unaudited consolidated balance sheets",
        "body_labels":  ["reinsurance receivables", "intangible fixed assets",
                         "reserve for possible loan losses", "liability for retirement benefits"],
        "min_matches":  2,
    },
}

# ── Helpers ────────────────────────────────────────────────────────────────────
_NOTE_RE = re.compile(r"\s*\(\*\d+\)")
_BRKT_RE = re.compile(r"^\([\d,]+(?:\.\d+)?\)$")
_CONT_WORDS = {"network"}

_SECTION_HEADERS = {
    "assets": "ASSETS",    "assets:": "ASSETS",
    "liabilities": "LIABILITIES", "liabilities:": "LIABILITIES",
    "net assets": "NET ASSETS",   "net assets:": "NET ASSETS",
}
_SECTION_OVERRIDES = {
    "total assets":                     "ASSETS",
    "total net assets":                 "NET ASSETS",
    "total liabilities and net assets": "NET ASSETS",
}

MONTH_MAP   = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
               "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
QUARTER_MAP = {3:1, 6:2, 9:3, 12:4}


def _clean(t):  return re.sub(r"\s+", " ", str(t)).strip()

def _norm(t):
    t = _NOTE_RE.sub("", _clean(t)).lower()
    return re.sub(r"[\[\]]", "", t)

def _parse_num(text):
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

def _split(cell):
    return [s.strip() for s in str(cell).split("\n") if s.strip()]

def _join_continued(items):
    out = []
    for item in items:
        if not item or item == "None":
            continue
        if out:
            first = item[0]
            # join if: starts lowercase, starts with "(" (label continuation like "(losses)"),
            # or is a known continuation word
            if (first.islower()
                    or (first == "(" and not re.match(r"^\([\d,]+\)$", item))
                    or item.split()[0].lower() in _CONT_WORDS):
                sep = "" if out[-1].endswith("-") else " "
                out[-1] = out[-1] + sep + item
            else:
                out.append(item)
        else:
            out.append(item)
    return out

def _period_from_col(col_name):
    """'1-As of March\\n31, 2025' → '2025-Q1'"""
    text = _clean(str(col_name).replace("\n", " "))
    m    = re.search(r"As of\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})", text, re.I)
    if not m:
        return None
    mn = MONTH_MAP.get(m.group(1).lower(), 0)
    q  = QUARTER_MAP.get(mn, 0)
    return f"{m.group(3)}-Q{q}" if q else None


# ── Dynamic page finder ────────────────────────────────────────────────────────
def find_table_page(doc, fingerprint, start=0):
    """
    Scan pages (>= start).  Return first page where:
      - fingerprint['header'] appears in page text
      - >= min_matches of body_labels appear in page text
    """
    hkw   = fingerprint["header"].lower()
    bkws  = [l.lower() for l in fingerprint["body_labels"]]
    min_n = fingerprint.get("min_matches", len(bkws))
    for pg in range(start, len(doc)):
        txt = doc[pg].get_text("text").lower()
        if hkw not in txt:
            continue
        if sum(1 for k in bkws if k in txt) >= min_n:
            return pg
    return None


# ── Label-column processor ─────────────────────────────────────────────────────
def _process_col(df, label_col, val_cols, periods, rows, skip_rows=1):
    """
    Walk rows of `df` (skipping the first `skip_rows` header/subheader rows).
    label_col  : column index with \n-concatenated label text
    val_cols   : list of column indices, one per period (positionally matched)
    """
    current_section = None
    for ri in range(skip_rows, len(df)):
        labels    = _join_continued(_split(str(df.iloc[ri, label_col])))
        val_lists = {}
        for pi, ci in enumerate(val_cols):
            if ci < df.shape[1] and pi < len(periods):
                val_lists[periods[pi]] = _split(str(df.iloc[ri, ci]))

        val_idx = 0
        for label in labels:
            n = _norm(label)
            if n in _SECTION_OVERRIDES:
                current_section = _SECTION_OVERRIDES[n]
            if n in _SECTION_HEADERS:
                current_section = _SECTION_HEADERS[n]
                continue
            if current_section is None:
                val_idx += 1
                continue
            vdict = {p: _parse_num(val_lists.get(p, [])[val_idx]
                                   if val_idx < len(val_lists.get(p, [])) else None)
                     for p in periods}
            rows.append({"section": current_section, "label": label, "values": vdict})
            val_idx += 1


# ── fitz DataFrame parser ──────────────────────────────────────────────────────
def _parse_fitz_df(df):
    """
    Parse a DataFrame returned by fitz table.to_pandas().

    Column names carry 'As of Month DD, YYYY' → derive periods from them.
    Row 0 is the 'Amount' sub-header → skip it (skip_rows=1).

    3-col layout  (Q4 simplified)   : single label+value pair, sections stacked
    6-col layout  (Q1 annual)       : ASSETS left (0-2) + LIABILITIES right (3-5)
                                       → reorder into ASSETS / LIABILITIES / NET ASSETS
    """
    # Detect periods from column names (Q1 annual: "1-As of March\n31, 2025")
    periods     = []
    period_cols = []
    for ci, col in enumerate(df.columns):
        p = _period_from_col(col)
        if p and p not in periods:
            periods.append(p)
            period_cols.append(ci)

    # Fallback: periods in row 0 cells (Q4 simplified has generic Col0/Col1 names)
    if not periods:
        for ci in range(df.shape[1]):
            p = _period_from_col(str(df.iloc[0, ci]).replace("\n", " "))
            if p and p not in periods:
                periods.append(p)
                period_cols.append(ci)

    if not periods:
        return [], []

    rows  = []
    ncols = df.shape[1]

    if ncols >= 6:
        _process_col(df, label_col=0, val_cols=[1, 2], periods=periods, rows=rows)
        _process_col(df, label_col=3, val_cols=[4, 5], periods=periods, rows=rows)
        assets = [r for r in rows if r["section"] == "ASSETS"]
        liabs  = [r for r in rows if r["section"] == "LIABILITIES"]
        nets   = [r for r in rows if r["section"] == "NET ASSETS"]
        rows   = assets + liabs + nets
    else:
        _process_col(df, label_col=0, val_cols=period_cols[:2], periods=periods, rows=rows)

    return periods, rows


# ── High-level extractor ───────────────────────────────────────────────────────
def extract(doc, name, fingerprint, start=0):
    """
    Find the table page, run fitz find_tables(), parse and return results.
    Also writes raw_fitz_<name>.txt for inspection.
    """
    pg = find_table_page(doc, fingerprint, start=start)
    if pg is None:
        print(f"  {name}: NOT FOUND (searched from p{start + 1})")
        return None, None, None

    print(f"  {name}: found on p{pg + 1}")
    page = doc[pg]
    tabs = page.find_tables()

    # Write raw TXT
    raw = _OUT / f"raw_fitz_{name.lower()}.txt"
    with open(raw, "w", encoding="utf-8") as f:
        f.write(f"Page {pg + 1}  |  {len(tabs.tables)} table(s) detected\n\n")
        for i, t in enumerate(tabs.tables):
            df = t.to_pandas()
            f.write(f"=== Table {i}  {t.row_count} x {t.col_count}  "
                    f"bbox={[round(x) for x in t.bbox]}\n")
            f.write(df.to_string())
            f.write("\n\n")
    print(f"    Raw TXT -> {raw.name}")

    for i, t in enumerate(tabs.tables):
        df             = t.to_pandas()
        periods, rows  = _parse_fitz_df(df)
        if periods and rows:
            print(f"    Table {i}  {t.row_count}x{t.col_count}  "
                  f"periods={periods}  data_rows={len(rows)}")
            return pg, periods, rows

    print(f"  {name}: no parseable tables on p{pg + 1}")
    return pg, None, None


# ── CSV writer ─────────────────────────────────────────────────────────────────
def write_csv(name, periods, rows):
    out = _OUT / f"output_fitz_{name.lower()}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Section", "Label"] + periods)
        for r in rows:
            w.writerow([r["section"], r["label"]] +
                       ["" if r["values"].get(p) is None else r["values"][p]
                        for p in periods])
    print(f"    CSV     -> {out.name}")


# ── Console display ────────────────────────────────────────────────────────────
def print_table(name, periods, rows):
    print(f"\n{'-'*70}")
    print(f"  {name}  ({len(rows)} rows)")
    print(f"{'-'*70}")
    hdr = f"  {'Section':<14} {'Label':<50} " + "  ".join(f"{p:>12}" for p in periods)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        vals = "  ".join(
            f"{'':>12}" if r["values"].get(p) is None
            else f"{r['values'][p]:>12,.0f}"
            for p in periods
        )
        print(f"  {r['section']:<14} {r['label']:<50} {vals}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    print(f"\nPDF: {pdf_path.name}\n{'='*70}")
    doc = fitz.open(str(pdf_path))

    print("\n[1] Non-Consolidated Balance Sheets")
    pg1, p1, r1 = extract(doc, "UNONCONSBS", TABLE_FINGERPRINTS["UNONCONSBS"])
    if r1:
        write_csv("UNONCONSBS", p1, r1)
        print_table("UNONCONSBS", p1, r1)

    print("\n[2] Consolidated Balance Sheets")
    pg2, p2, r2 = extract(doc, "UCONSBS", TABLE_FINGERPRINTS["UCONSBS"],
                          start=(pg1 + 1) if pg1 is not None else 0)
    if r2:
        write_csv("UCONSBS", p2, r2)
        print_table("UCONSBS", p2, r2)

    doc.close()

    print(f"\n{'='*70}")
    print(f"  UNONCONSBS : {len(r1):3d} rows  {p1}" if r1 else "  UNONCONSBS : NOT EXTRACTED")
    print(f"  UCONSBS    : {len(r2):3d} rows  {p2}" if r2 else "  UCONSBS    : NOT EXTRACTED")


if __name__ == "__main__":
    main()
