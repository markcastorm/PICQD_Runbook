"""
test_fitz_asalsec.py
Extract Available-for-sale Securities (ASALSEC) using PyMuPDF find_tables().

Table structure:
  - Single period (current report date)
  - 4 columns: label | Consolidated BS amount | Cost | Difference
  - 2 sections: exceed (BS amount > cost) / notexc (BS amount <= cost)
  - 9 items per section: Bonds, JGB, JLocGov, JCorp, Stocks, Foreign sec,
    Foreign bonds, Other foreign sec, Other (*) + Subtotal = 10 per section
  - Grand Total row

Page also contains a second table (POLRES Sold in Q1, Money Held in Trust in Q3).
Table classifier uses "those for which...exceeds cost" to pick the right one.

Present in:  Q1 annual (pr0515en-3-03.pdf, p54), Q3 (pr1114en-06.pdf, p44)
Absent in:   Q4 simplified (pr0213en-03.pdf) -- NOT FOUND expected

Run:
  python test_fitz_asalsec.py                                 # Q4 (NOT FOUND expected)
  python test_fitz_asalsec.py path/to/q1_or_q3.pdf           # Q1 or Q3
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

# ── Fingerprint ────────────────────────────────────────────────────────────────
# "available-for-sale securities" appears on many pages (FAIRVAL, HELDMAT etc).
# Body label "those for which the consolidated balance sheet amount exceeds cost"
# is unique to ASALSEC -- different wording from HELDMAT ("fair value exceeds").
FINGERPRINT = {
    "header":      "available-for-sale securities",
    "body_labels": ["those for which the consolidated balance sheet amount exceeds cost",
                    "other foreign securities",
                    "foreign bonds",
                    "stocks"],
    "min_matches": 3,
}

# ── Helpers ───────────────────────────────────────────────────────────────────
# (*) without digit must also be stripped (e.g. "Other (*)")
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


def _split(cell):
    return [s.strip() for s in str(cell).split("\n") if s.strip()]


def _join_continued(items):
    out = []
    for item in items:
        if not item or item == "None":
            continue
        if out:
            f = item[0]
            if (f.islower()
                    or (f == "(" and not re.match(r"^\([\d,]+\)$", item))):
                sep = "" if out[-1].endswith("-") else " "
                out[-1] += sep + item
            else:
                out.append(item)
        else:
            out.append(item)
    return out


def _period_from_text(text):
    m = re.search(r"[Aa]s of\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if not m:
        return None
    mn = MONTH_MAP.get(m.group(1).lower(), 0)
    q  = QUARTER_MAP.get(mn, 0)
    return f"{m.group(3)}-Q{q}" if q else None


def _doc_period(doc, up_to_page=None):
    """Scan doc; return the LATEST 'As of' date found (avoids picking prior-year column)."""
    limit      = (up_to_page + 1) if up_to_page is not None else len(doc)
    pat        = re.compile(r"[Aa]s of\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})")
    latest     = None
    latest_key = (0, 0)
    for pg in range(min(limit, len(doc))):
        for m in pat.finditer(doc[pg].get_text("text")):
            mn = MONTH_MAP.get(m.group(1).lower(), 0)
            q  = QUARTER_MAP.get(mn, 0)
            yr = int(m.group(3))
            if q and (yr, q) > latest_key:
                latest_key = (yr, q)
                latest = f"{yr}-Q{q}"
    return latest or "UNKNOWN"


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


# ── Table classifier ──────────────────────────────────────────────────────────
def _is_asalsec_table(df):
    """ASALSEC table contains 'those for which...exceeds cost' in col0."""
    full = " ".join(str(df.iloc[ri, 0]) for ri in range(len(df))).lower()
    return ("those for which" in full and "exceeds cost" in full)


# ── DataFrame parser ───────────────────────────────────────────────────────────
def _parse_asalsec_df(df):
    """
    Parse an ASALSEC DataFrame.

    fitz packs section-header text + all item labels into ONE cell (newline-sep).
    Section header keywords:
      'exceeds cost'       -> section = 'exceed'
      'does not exceed'    -> section = 'notexc'
    val_idx is NOT incremented for section-header labels.

    Labels: Bonds / JGB / JLocGov / JCorp / Stocks / Foreign sec /
            Foreign bonds / Other foreign sec / Other(*) / Subtotal  (10 per section)
    """
    rows    = []
    section = None

    for ri in range(len(df)):
        raw0     = str(df.iloc[ri, 0])
        stripped = raw0.strip()

        if not stripped or stripped == "None":
            continue

        labels   = _join_continued(_split(raw0))
        col1_raw = _split(str(df.iloc[ri, 1])) if df.shape[1] > 1 else []
        col2_raw = _split(str(df.iloc[ri, 2])) if df.shape[1] > 2 else []
        col3_raw = _split(str(df.iloc[ri, 3])) if df.shape[1] > 3 else []

        def _g(lst, i): return _parse_num(lst[i]) if i < len(lst) else None

        val_idx = 0
        for label in labels:
            n = _norm(label)

            # Section-header lines — switch section, consume NO value slot
            if "those for which" in n:
                if "does not exceed" in n:
                    section = "notexc"
                else:
                    section = "exceed"
                continue

            # Grand total
            if n == "total":
                rows.append({
                    "section": "TOTAL",
                    "label":   "Total",
                    "cons":    _g(col1_raw, val_idx),
                    "cost":    _g(col2_raw, val_idx),
                    "diff":    _g(col3_raw, val_idx),
                })
                val_idx += 1
                continue

            if section is None:
                val_idx += 1
                continue

            rows.append({
                "section": section,
                "label":   _NOTE_RE.sub("", _clean(label)),
                "cons":    _g(col1_raw, val_idx),
                "cost":    _g(col2_raw, val_idx),
                "diff":    _g(col3_raw, val_idx),
            })
            val_idx += 1

    return rows


# ── Extractor ──────────────────────────────────────────────────────────────────
def extract(doc):
    pg = find_page(doc)
    if pg is None:
        print("  ASALSEC: NOT FOUND (expected for simplified format)")
        return None, None

    print(f"  ASALSEC: found on p{pg + 1}")
    period = _doc_period(doc, up_to_page=pg)
    print(f"  Period : {period}")

    tabs = doc[pg].find_tables()

    # Write raw TXT
    raw = _OUT / f"raw_fitz_asalsec_p{pg + 1}.txt"
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
        if t.col_count < 3:
            continue
        df = t.to_pandas()
        if not _is_asalsec_table(df):
            print(f"    Table {i}  {t.row_count}x{t.col_count}  -- skipped (not ASALSEC)")
            continue
        rows = _parse_asalsec_df(df)
        if rows:
            print(f"    Table {i}  {t.row_count}x{t.col_count}  rows={len(rows)}")
            return period, rows

    print("  ASALSEC: no parseable table found")
    return period, None


# ── CSV writer ─────────────────────────────────────────────────────────────────
def write_csv(period, rows):
    out = _OUT / "output_fitz_asalsec.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Section", "Label",
                    f"Cons_BS_{period}", f"Cost_{period}", f"Diff_{period}"])
        for r in rows:
            w.writerow([r["section"], r["label"],
                        "" if r["cons"] is None else r["cons"],
                        "" if r["cost"] is None else r["cost"],
                        "" if r["diff"] is None else r["diff"]])
    print(f"    CSV -> {out.name}")


# ── Console display ────────────────────────────────────────────────────────────
def print_table(period, rows):
    print(f"\n{'-'*85}")
    print(f"  ASALSEC  ({len(rows)} rows)  period={period}")
    print(f"{'-'*85}")
    hdr = (f"  {'Sec':<10} {'Label':<42}"
           f" {'Cons BS':>14} {'Cost':>14} {'Diff':>12}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        def _f(v): return f"{v:>14,.0f}" if v is not None else f"{'':>14}"
        print(f"  {r['section']:<10} {r['label']:<42}"
              f" {_f(r['cons'])} {_f(r['cost'])} {_f(r['diff'])}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    print(f"\nPDF: {pdf_path.name}\n{'='*70}")
    doc = fitz.open(str(pdf_path))

    period, rows = extract(doc)
    if rows:
        write_csv(period, rows)
        print_table(period, rows)

    doc.close()
    print(f"\n{'='*70}")
    if rows:
        print(f"  ASALSEC : {len(rows):3d} rows  period={period}")
    else:
        print("  ASALSEC : NOT EXTRACTED")


if __name__ == "__main__":
    main()
