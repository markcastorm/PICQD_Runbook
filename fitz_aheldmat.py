"""
test_fitz_aheldmat.py
Extract 'Assets held-to-maturity in trust / assets held for reserves in trust /
other money held in trust' (AHELDMAT) table.

Two-period table. Captures 'Book value' column for both periods.

3 rows:
  - Assets held-to-maturity in trust  (dashes — no values)
  - Assets held for reserves in trust (dashes — no values)
  - Other money held in trust          (has values)

Present in: Q1 annual (p16), Q3 (p11), Q4 simplified (p6) -- ALL PDFs.

fitz Table 2 on the page (indices 0,1,2 — the AHELDMAT is always the last table).
Column layout (11 cols):
  col0 : row label
  col1 : Period 1 Book value
  col2 : Period 1 Fair value
  col3 : Period 1 Net unrealized
  col4 : Period 1 Gains
  col5 : Period 1 Losses
  col6 : Period 2 Book value
  ...
Rows 0-1 in the DataFrame are header rows; data starts at row 2.

Run:
  python test_fitz_aheldmat.py                        # Q4
  python test_fitz_aheldmat.py path/to/pdf            # Q1, Q3, Q4
"""
import re, csv, sys
from pathlib import Path

try:
    import fitz
except ImportError:
    sys.exit("pip install pymupdf")

_HERE = Path(__file__).parent
_OUT  = _HERE / "Testfiles"
DEFAULT = _HERE / "Project_information" / "sample" / "pr0213en-03.pdf"

# ── Fingerprint ───────────────────────────────────────────────────────────────
# Section title uses "/" between the three trust categories.
# Body labels appear as row labels in the table itself.
FINGERPRINT = {
    "header":      "assets held-to-maturity in trust/assets held for reserves in trust",
    "body_labels": ["assets held for reserves in trust",
                    "other money held in trust",
                    "assets held-to-maturity in trust"],
    "min_matches": 2,
}

# ── Helpers ───────────────────────────────────────────────────────────────────
_NOTE_RE = re.compile(r"\s*\(\*\d*\)")
_BRKT_RE = re.compile(r"^\([\d,]+(?:\.\d+)?\)$")

MONTH_MAP   = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
               "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
QUARTER_MAP = {3:1, 6:2, 9:3, 12:4}


def _clean(t):  return re.sub(r"\s+", " ", str(t)).strip()
def _norm(t):   return _NOTE_RE.sub("", _clean(t)).lower().strip()


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
        return (-float(raw[1:-1].replace(",", "")) if raw.startswith("(")
                else float(raw.replace(",", "")))
    return None


def _period_from_match(month_word, year_str):
    mn = MONTH_MAP.get(month_word.lower(), 0)
    q  = QUARTER_MAP.get(mn, 0)
    return f"{year_str}-Q{q}" if q else None


# ── Page finder ────────────────────────────────────────────────────────────────
def find_page(doc, start=0):
    fp   = FINGERPRINT
    hkw  = fp["header"].lower()
    bkws = [l.lower() for l in fp["body_labels"]]
    minn = fp.get("min_matches", len(bkws))
    for pg in range(start, len(doc)):
        txt = doc[pg].get_text("text").lower()
        if hkw not in txt:
            continue
        if sum(1 for k in bkws if k in txt) >= minn:
            return pg
    return None


# ── Period extractor ──────────────────────────────────────────────────────────
def _extract_periods(doc, pg):
    """
    Scan page text for 'Month DD, YYYY' patterns.
    Returns (period1, period2) sorted ascending — P1 = prior year, P2 = current.
    """
    txt = doc[pg].get_text("text")
    pat = re.compile(
        r"(January|February|March|April|May|June|July|August"
        r"|September|October|November|December)\s+\d{1,2},?\s*(\d{4})",
        re.IGNORECASE,
    )
    seen = []
    for m in pat.finditer(txt):
        p = _period_from_match(m.group(1), m.group(2))
        if p and p not in seen:
            seen.append(p)
    seen.sort()
    return (seen[0] if seen else "UNKNOWN",
            seen[1] if len(seen) > 1 else "UNKNOWN")


# ── Table classifier ──────────────────────────────────────────────────────────
def _is_aheldmat_table(df):
    """AHELDMAT col0 contains 'assets held for reserves in trust'."""
    col0 = " ".join(_clean(str(df.iloc[ri, 0])) for ri in range(len(df))).lower()
    return "assets held for reserves in trust" in col0


# ── DataFrame parser ──────────────────────────────────────────────────────────
_ROW_MAP = {
    "assets held-to-maturity in trust": "Assets held-to-maturity in trust",
    "assets held for reserves in trust": "Assets held for reserves in trust",
    "other money held in trust":          "Other money held in trust",
}


def _parse_aheldmat_df(df):
    """
    Extract Book value for both periods from the 3 data rows.

    Rows 0-1 are header rows (no recognizable label) — skipped automatically.
    col1 = Period 1 Book value, col6 = Period 2 Book value (consistent across all PDFs).
    """
    rows = []
    for ri in range(len(df)):
        # Join lines; collapse "hyphen + whitespace" to reconnect mid-word splits
        raw0 = re.sub(r"-\s+", "-",
                      " ".join(s.strip() for s in str(df.iloc[ri, 0]).split("\n") if s.strip()))
        n    = _norm(raw0)

        matched = None
        for key, canonical in _ROW_MAP.items():
            if key in n:
                matched = canonical
                break
        if not matched:
            continue

        bv1 = _parse_num(str(df.iloc[ri, 1])) if df.shape[1] > 1 else None
        bv2 = _parse_num(str(df.iloc[ri, 6])) if df.shape[1] > 6 else None

        rows.append({"label": matched, "bv1": bv1, "bv2": bv2})

    return rows


# ── Extractor ──────────────────────────────────────────────────────────────────
def extract(doc):
    pg = find_page(doc)
    if pg is None:
        print("  AHELDMAT: NOT FOUND")
        return None, None, None

    print(f"  AHELDMAT: found on p{pg + 1}")
    period1, period2 = _extract_periods(doc, pg)
    print(f"  Period1 : {period1}")
    print(f"  Period2 : {period2}")

    tabs = doc[pg].find_tables()

    # Write raw TXT
    raw = _OUT / f"raw_fitz_aheldmat_p{pg + 1}.txt"
    with open(raw, "w", encoding="utf-8") as f:
        f.write(f"Page {pg + 1}  |  {len(tabs.tables)} table(s)\n\n")
        for i, t in enumerate(tabs.tables):
            df = t.to_pandas()
            f.write(f"=== Table {i}  {t.row_count}x{t.col_count}  "
                    f"bbox={[round(x) for x in t.bbox]}\n")
            f.write(df.to_string())
            f.write("\n\n")
    print(f"    Raw TXT -> {raw.name}")

    for i, t in enumerate(tabs.tables):
        if t.col_count < 6:
            continue
        df = t.to_pandas()
        if not _is_aheldmat_table(df):
            print(f"    Table {i}  {t.row_count}x{t.col_count}  -- skipped")
            continue
        rows = _parse_aheldmat_df(df)
        if rows:
            print(f"    Table {i}  {t.row_count}x{t.col_count}  rows={len(rows)}")
            return period1, period2, rows

    print("  AHELDMAT: no parseable table found")
    return period1, period2, None


# ── CSV writer ─────────────────────────────────────────────────────────────────
def write_csv(period1, period2, rows):
    out = _OUT / "output_fitz_aheldmat.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Label", f"BookValue_{period1}", f"BookValue_{period2}"])
        for r in rows:
            w.writerow([r["label"],
                        "" if r["bv1"] is None else r["bv1"],
                        "" if r["bv2"] is None else r["bv2"]])
    print(f"    CSV -> {out.name}")


# ── Console display ────────────────────────────────────────────────────────────
def print_table(period1, period2, rows):
    print(f"\n{'-'*80}")
    print(f"  AHELDMAT  ({len(rows)} rows)  {period1} / {period2}")
    print(f"{'-'*80}")
    hdr = f"  {'Label':<42} {f'BV {period1}':>18} {f'BV {period2}':>18}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        def _f(v): return f"{v:>18,.1f}" if v is not None else f"{'-':>18}"
        print(f"  {r['label']:<42} {_f(r['bv1'])} {_f(r['bv2'])}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    print(f"\nPDF: {pdf_path.name}\n{'='*70}")
    doc = fitz.open(str(pdf_path))

    period1, period2, rows = extract(doc)

    if rows:
        write_csv(period1, period2, rows)
        print_table(period1, period2, rows)

    doc.close()
    print(f"\n{'='*70}")
    if rows:
        print(f"  AHELDMAT : {len(rows):3d} rows  {period1} / {period2}")
    else:
        print("  AHELDMAT : NOT EXTRACTED")


if __name__ == "__main__":
    main()
