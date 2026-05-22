"""
test_fitz_asalsecsold_monheld.py
Extract Available-for-sale Securities Sold (ASALSECSOLD) and
Money Held in Trust (MONHELD) using PyMuPDF find_tables().

ASALSECSOLD:
  - 4 columns: label | Sales | Gains | Losses
  - 9 items + Total (10 rows, no exceed/notexc structure)
  - Present in Q1 annual (p55) ONLY -- absent in Q3/Q4 (full-year fiscal table)

MONHELD:
  - 6 columns: label | Cons BS | Cost | Diff | exceed cost | not exceed cost
  - 1 data row: "Specified money held in trust"
  - Present in Q1 annual (p55) and Q3 (p44) -- absent in Q4

Both tables can be on the same page (Q1: both on p55) OR different pages
(Q3: MONHELD on p44, ASALSECSOLD absent). Script handles all cases.

Run:
  python test_fitz_asalsecsold_monheld.py                         # Q4 NOT FOUND
  python test_fitz_asalsecsold_monheld.py path/to/q1_or_q3.pdf   # Q1 or Q3
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

# ── Fingerprints ──────────────────────────────────────────────────────────────
FINGERPRINTS = {
    "ASALSECSOLD": {
        # "sold during the fiscal year" is unique — prevents matching ASALSEC page
        "header":      "available-for-sale securities sold during the fiscal year",
        "body_labels": ["other foreign securities", "other securities",
                        "foreign bonds", "japanese local government bonds"],
        "min_matches": 3,
    },
    "MONHELD": {
        # Intro text "classified as other than trading..." is on the same page as table
        "header":      "money held in trust",
        "body_labels": ["money held in trust classified as other than trading",
                        "specified money held in trust",
                        "held-to-maturity and policy-reserve-matching"],
        "min_matches": 2,
    },
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


def _doc_period(doc, up_to_page=None):
    """Scan doc; return LATEST 'As of' date (avoids picking prior-year column header)."""
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
def find_page(doc, fp_key, start=0):
    fp   = FINGERPRINTS[fp_key]
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


# ── Table classifiers ─────────────────────────────────────────────────────────
def _is_asalsecsold_table(df):
    """'other securities' is unique to ASALSECSOLD (ASALSEC has 'Other (*)')."""
    full = " ".join(str(df.iloc[ri, 0]) for ri in range(len(df))).lower()
    return "other securities" in full and "specified" not in full


def _is_monheld_table(df):
    full = " ".join(str(df.iloc[ri, 0]) for ri in range(len(df))).lower()
    return "specified" in full


# ── ASALSECSOLD parser ────────────────────────────────────────────────────────
def _parse_asalsecsold_df(df):
    """
    Flat list: no sections. fitz may pack all items in one cell (like HELDMAT).
    Columns: label | Sales | Gains | Losses.
    Skip column-header rows (col0 = None/empty).
    """
    rows = []
    _COL_HDRS = {"sales", "gains", "losses", "none", "amount", "year", "items"}

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
            if n in _COL_HDRS:
                continue
            rows.append({
                "label":  _NOTE_RE.sub("", _clean(label)),
                "sales":  _g(col1_raw, val_idx),
                "gains":  _g(col2_raw, val_idx),
                "losses": _g(col3_raw, val_idx),
            })
            val_idx += 1

    return rows


# ── MONHELD parser ────────────────────────────────────────────────────────────
def _parse_monheld_df(df):
    """
    Single data row: 'Specified money held in trust'.
    6 columns: cons | cost | diff | exceed | notexc.
    Header rows have no recognizable label in col0 — skip them.
    """
    for ri in range(len(df)):
        raw0   = str(df.iloc[ri, 0])
        labels = _join_continued(_split(raw0))
        joined = " ".join(labels).lower()

        if "specified" not in joined:
            continue

        def _gv(ci):
            if df.shape[1] > ci:
                # value cells may have \n if multi-line; take first numeric item
                parts = _split(str(df.iloc[ri, ci]))
                for p in parts:
                    v = _parse_num(p)
                    if v is not None:
                        return v
            return None

        return {
            "label":  "Specified money held in trust",
            "cons":   _gv(1),
            "cost":   _gv(2),
            "diff":   _gv(3),
            "exceed": _gv(4),
            "notexc": _gv(5),
        }
    return None


# ── Raw TXT writer ────────────────────────────────────────────────────────────
def _write_raw(doc, pg, suffix):
    tabs = doc[pg].find_tables()
    raw  = _OUT / f"raw_fitz_{suffix}_p{pg + 1}.txt"
    with open(raw, "w", encoding="utf-8") as f:
        f.write(f"Page {pg + 1}  |  {len(tabs.tables)} table(s)\n\n")
        for i, t in enumerate(tabs.tables):
            df = t.to_pandas()
            f.write(f"=== Table {i}  {t.row_count}x{t.col_count}  "
                    f"bbox={[round(x) for x in t.bbox]}\n")
            f.write(df.to_string())
            f.write("\n\n")
    print(f"    Raw TXT -> {raw.name}")
    return tabs


# ── Extractor ──────────────────────────────────────────────────────────────────
def extract(doc):
    sold_pg   = find_page(doc, "ASALSECSOLD")
    monheld_pg = find_page(doc, "MONHELD")

    if sold_pg is None:
        print("  ASALSECSOLD: NOT FOUND")
    else:
        print(f"  ASALSECSOLD: found on p{sold_pg + 1}")

    if monheld_pg is None:
        print("  MONHELD    : NOT FOUND")
    else:
        print(f"  MONHELD    : found on p{monheld_pg + 1}")

    # Period: scan up to earliest found page
    found_pages = [pg for pg in [sold_pg, monheld_pg] if pg is not None]
    period = _doc_period(doc, up_to_page=min(found_pages)) if found_pages else None

    sold_rows   = None
    monheld_row = None

    # Deduplicate pages to scan
    pages_to_scan = list(dict.fromkeys(pg for pg in [sold_pg, monheld_pg] if pg is not None))

    for pg in pages_to_scan:
        tabs = _write_raw(doc, pg, "sold_monheld")

        for i, t in enumerate(tabs.tables):
            if t.col_count < 3:
                continue
            df = t.to_pandas()

            if sold_rows is None and pg == sold_pg and _is_asalsecsold_table(df):
                rows = _parse_asalsecsold_df(df)
                if rows:
                    sold_rows = rows
                    print(f"  ASALSECSOLD: Table {i}  {t.row_count}x{t.col_count}  rows={len(rows)}")

            if monheld_row is None and pg == monheld_pg and _is_monheld_table(df):
                row = _parse_monheld_df(df)
                if row:
                    monheld_row = row
                    print(f"  MONHELD    : Table {i}  {t.row_count}x{t.col_count}  1 row")

    return period, sold_rows, monheld_row


# ── CSV writers ────────────────────────────────────────────────────────────────
def write_csv_asalsecsold(period, rows):
    out = _OUT / "output_fitz_asalsecsold.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Label", f"Sales_{period}", f"Gains_{period}", f"Losses_{period}"])
        for r in rows:
            w.writerow([r["label"],
                        "" if r["sales"]  is None else r["sales"],
                        "" if r["gains"]  is None else r["gains"],
                        "" if r["losses"] is None else r["losses"]])
    print(f"    CSV -> {out.name}")


def write_csv_monheld(period, row):
    out = _OUT / "output_fitz_monheld.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Label", f"Cons_BS_{period}", f"Cost_{period}", f"Diff_{period}",
                    f"Exceed_{period}", f"NotExceed_{period}"])
        w.writerow([row["label"],
                    "" if row["cons"]   is None else row["cons"],
                    "" if row["cost"]   is None else row["cost"],
                    "" if row["diff"]   is None else row["diff"],
                    "" if row["exceed"] is None else row["exceed"],
                    "" if row["notexc"] is None else row["notexc"]])
    print(f"    CSV -> {out.name}")


# ── Console display ────────────────────────────────────────────────────────────
def print_asalsecsold(period, rows):
    print(f"\n{'-'*80}")
    print(f"  ASALSECSOLD  ({len(rows)} rows)  period={period}")
    print(f"{'-'*80}")
    hdr = f"  {'Label':<40} {'Sales':>14} {'Gains':>12} {'Losses':>12}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        def _f(v): return f"{v:>14,.0f}" if v is not None else f"{'':>14}"
        print(f"  {r['label']:<40} {_f(r['sales'])} {_f(r['gains'])} {_f(r['losses'])}")


def print_monheld(period, row):
    print(f"\n{'-'*90}")
    print(f"  MONHELD  (1 row)  period={period}")
    print(f"{'-'*90}")
    def _f(v): return f"{v:>15,.0f}" if v is not None else f"{'':>15}"
    print(f"  {'Label':<35} {'Cons BS':>15} {'Cost':>15} {'Diff':>15} {'Exceed':>15} {'NotExceed':>15}")
    print("  " + "-" * 88)
    print(f"  {row['label']:<35} {_f(row['cons'])} {_f(row['cost'])} "
          f"{_f(row['diff'])} {_f(row['exceed'])} {_f(row['notexc'])}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    print(f"\nPDF: {pdf_path.name}\n{'='*70}")
    doc = fitz.open(str(pdf_path))

    period, sold_rows, monheld_row = extract(doc)

    if sold_rows:
        write_csv_asalsecsold(period, sold_rows)
        print_asalsecsold(period, sold_rows)

    if monheld_row:
        write_csv_monheld(period, monheld_row)
        print_monheld(period, monheld_row)

    doc.close()
    print(f"\n{'='*70}")
    print(f"  Period      : {period}")
    print(f"  ASALSECSOLD : {len(sold_rows):3d} rows" if sold_rows else "  ASALSECSOLD : NOT EXTRACTED")
    print(f"  MONHELD     : 1 row"                    if monheld_row else "  MONHELD     : NOT EXTRACTED")


if __name__ == "__main__":
    main()
