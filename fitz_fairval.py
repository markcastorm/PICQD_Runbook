"""
test_fitz_fairval.py
Extract Fair Values of Financial Instruments using PyMuPDF find_tables().

Table: FAIRVAL
  - Single period (current report date: "As of Month DD, YYYY")
  - 4 columns: label | Consolidated BS amount | Fair value | Net unrealized gains (losses)
  - 3 sections: ASSETS, LIABILITIES, DERIVATIVES
  - Values in (brackets) = negative  |  [brackets] = positive net derivative values
  - Footnote markers (*N) stripped from labels

Present in:  Q1 annual (pr0515en-3-03.pdf, p46), Q3 (pr1114en-06.pdf, p36)
Absent in:   Q4 simplified (pr0213en-03.pdf) -- NOT FOUND expected

Run:
  python test_fitz_fairval.py                                    # Q4 (NOT FOUND expected)
  python test_fitz_fairval.py path/to/q1_or_q3.pdf              # Q1 annual or Q3
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
FINGERPRINT = {
    "header":      "fair values of financial instruments",
    "body_labels": ["monetary claims bought", "reserve for possible loan losses",
                    "bonds payable", "held-to-maturity bonds"],
    "min_matches": 3,
}

# ── Section detection ─────────────────────────────────────────────────────────
# Labels that trigger a section switch BEFORE appending the row
_SEC_TRANSITION = {
    "bonds payable": "LIABILITIES",
}
# Labels that are pure section headers (no values) -- skip row but switch section
_SEC_HEADER_START = "derivative transactions"

# ── Helpers ───────────────────────────────────────────────────────────────────
_NOTE_RE  = re.compile(r"\s*\(\*\d+\)")        # strip (*1), (*2) etc.
_BRKT_RE  = re.compile(r"^\([\d,]+(?:\.\d+)?\)$")   # (1,234) negative
_SQBRK_RE = re.compile(r"^\[[\d,]+(?:\.\d+)?\]$")   # [1,234] net derivative value

MONTH_MAP   = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
               "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
QUARTER_MAP = {3:1, 6:2, 9:3, 12:4}

_CONT_WORDS = {"network"}   # label continuation words


def _clean(t):  return re.sub(r"\s+", " ", str(t)).strip()
def _norm(t):   return _NOTE_RE.sub("", _clean(t)).lower().strip()


def _parse_num(text):
    t = _clean(text)
    if not t or t in ("-", "—", ""):
        return None
    if _BRKT_RE.match(t):
        return -float(t[1:-1].replace(",", ""))
    if _SQBRK_RE.match(t):
        return float(t[1:-1].replace(",", ""))      # [281] → +281
    if re.match(r"^-?[\d,]+(?:\.\d+)?$", t):
        return float(t.replace(",", ""))
    # Embedded bracket
    m = re.search(r"\([\d,]+\)|\[[\d,]+\]|[\d,]+", t)
    if m:
        raw = m.group()
        if raw.startswith("("): return -float(raw[1:-1].replace(",", ""))
        if raw.startswith("["): return  float(raw[1:-1].replace(",", ""))
        return float(raw.replace(",", ""))
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
                    or (f == "(" and not re.match(r"^\([\d,]+\)$", item))
                    or item.split()[0].lower() in _CONT_WORDS):
                sep = "" if out[-1].endswith("-") else " "
                out[-1] += sep + item
            else:
                out.append(item)
        else:
            out.append(item)
    return out


def _period_from_text(text):
    """'as of March 31, 2026' -> '2026-Q1'"""
    m = re.search(r"[Aa]s of\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if not m:
        return None
    mn = MONTH_MAP.get(m.group(1).lower(), 0)
    q  = QUARTER_MAP.get(mn, 0)
    return f"{m.group(3)}-Q{q}" if q else None


# ── Page finder ────────────────────────────────────────────────────────────────
def find_table_page(doc, start=0):
    hkw  = FINGERPRINT["header"].lower()
    bkws = [l.lower() for l in FINGERPRINT["body_labels"]]
    minn = FINGERPRINT.get("min_matches", len(bkws))
    for pg in range(start, len(doc)):
        txt = doc[pg].get_text("text").lower()
        if hkw not in txt:
            continue
        if sum(1 for k in bkws if k in txt) >= minn:
            return pg
    return None


# ── DataFrame parser ───────────────────────────────────────────────────────────
def _parse_df(df):
    rows    = []
    section = "ASSETS"

    for ri in range(len(df)):
        raw0 = str(df.iloc[ri, 0])
        nl   = _norm(raw0)

        # Skip empty / column-header rows
        if not nl or nl in ("none", "amount", "year", "items"):
            continue
        if "consolidated balance sheet" in nl or "net unrealized" in nl:
            continue

        labels   = _join_continued(_split(raw0))
        col1_raw = _split(str(df.iloc[ri, 1])) if df.shape[1] > 1 else []
        col2_raw = _split(str(df.iloc[ri, 2])) if df.shape[1] > 2 else []
        col3_raw = _split(str(df.iloc[ri, 3])) if df.shape[1] > 3 else []

        def _g(lst, i): return _parse_num(lst[i]) if i < len(lst) else None

        val_idx = 0
        for label in labels:
            n = _norm(label)

            # "Derivative transactions (*5)" — section header, no values
            if n.startswith(_SEC_HEADER_START):
                section = "DERIVATIVES"
                continue

            # Bonds payable signals start of LIABILITIES section
            if n in _SEC_TRANSITION:
                section = _SEC_TRANSITION[n]

            rows.append({
                "section":    section,
                "label":      _NOTE_RE.sub("", _clean(label)),
                "cons":       _g(col1_raw, val_idx),
                "fv":         _g(col2_raw, val_idx),
                "unrealized": _g(col3_raw, val_idx),
            })
            val_idx += 1

    return rows


# ── Extractor ─────────────────────────────────────────────────────────────────
def extract(doc):
    pg = find_table_page(doc)
    if pg is None:
        print("  FAIRVAL: NOT FOUND (expected for simplified format)")
        return None, None

    print(f"  FAIRVAL: found on p{pg + 1}")
    period = _period_from_text(doc[pg].get_text("text")) or "UNKNOWN"
    print(f"  Period : {period}")

    tabs = doc[pg].find_tables()

    # Write raw TXT for inspection
    raw = _OUT / "raw_fitz_fairval.txt"
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
        rows = _parse_df(t.to_pandas())
        if rows:
            print(f"    Table {i}  {t.row_count}x{t.col_count}  rows={len(rows)}")
            return period, rows

    print("  FAIRVAL: no parseable table found")
    return period, None


# ── CSV writer ─────────────────────────────────────────────────────────────────
def write_csv(period, rows):
    out = _OUT / "output_fitz_fairval.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Section", "Label",
                    f"Cons_BS_{period}", f"FairValue_{period}", f"Unrealized_{period}"])
        for r in rows:
            w.writerow([r["section"], r["label"],
                        "" if r["cons"]       is None else r["cons"],
                        "" if r["fv"]         is None else r["fv"],
                        "" if r["unrealized"] is None else r["unrealized"]])
    print(f"    CSV     -> {out.name}")


# ── Console display ────────────────────────────────────────────────────────────
def print_table(period, rows):
    print(f"\n{'-'*88}")
    print(f"  FAIRVAL  ({len(rows)} rows)  period={period}")
    print(f"{'-'*88}")
    hdr = (f"  {'Sec':<12} {'Label':<44}"
           f" {'Cons BS':>15} {'Fair Value':>15} {'Unrealized':>14}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        def _f(v): return f"{v:>15,.0f}" if v is not None else f"{'':>15}"
        print(f"  {r['section']:<12} {r['label']:<44}"
              f" {_f(r['cons'])} {_f(r['fv'])} {_f(r['unrealized'])}")


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
        print(f"  FAIRVAL : {len(rows):3d} rows  period={period}")
    else:
        print("  FAIRVAL : NOT EXTRACTED")


if __name__ == "__main__":
    main()
