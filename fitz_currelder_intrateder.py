"""
test_fitz_currelder_intrateder.py
Extract Currency-related derivatives (CURRELDER) and Interest-related derivatives
(INTRATEDER) from "Derivative transactions to which the hedge accounting is applied".

Both tables (i) and (ii) are on the same page:
  (i)  Currency-related derivatives (CURRELDER)
       6 cols: Hedge method | Type | Major hedged item | Contract | Due after 1yr | FV
       Rows: Deferred hedge (currency swaps + USD/EUR) + Fair value hedge
             (forward FX: Sold + USD/EUR/AUD/Other) + Total
  (ii) Interest-related derivatives (INTRATEDER)
       Same 6-col structure.
       Rows: Deferred hedge (interest rate swaps, fixed/floating) + Total

Table classifier: (i) has "forward foreign exchange" / "currency swaps",
                  (ii) has "interest rate swaps" / "interest-related".

Present in:  Q1 annual (pr0515en-3-03.pdf, p56) ONLY
Absent in:   Q3 (pr1114en-06.pdf), Q4 (pr0213en-03.pdf) -- NOT FOUND expected

Run:
  python test_fitz_currelder_intrateder.py                        # Q4 NOT FOUND
  python test_fitz_currelder_intrateder.py path/to/q1.pdf         # Q1 annual
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

# ── Fingerprint (single page has both tables) ─────────────────────────────────
FINGERPRINT = {
    "header":      "derivative transactions to which the hedge accounting is applied",
    "body_labels": ["currency-related derivatives", "interest-related derivatives",
                    "forward foreign exchange", "interest rate swaps"],
    "min_matches": 3,
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
    t = _clean(str(text).split("\n")[0])   # take first line only
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


# ── Table classifiers ─────────────────────────────────────────────────────────
def _table_type(df):
    """Return 'CURRELDER', 'INTRATEDER', or None."""
    full = " ".join(
        str(df.iloc[ri, ci]) for ri in range(len(df))
        for ci in range(min(df.shape[1], 3))
    ).lower()
    if "forward foreign exchange" in full or "currency swaps" in full:
        return "CURRELDER"
    if "interest rate swaps" in full or "fixed-rate" in full:
        return "INTRATEDER"
    return None


# ── DataFrame parser ───────────────────────────────────────────────────────────
def _parse_hedge_df(df):
    """
    Parse a 6-column hedge derivatives table.

    Column layout:
      col0: Hedge accounting method (Deferred / Fair value / Exceptional) -- merged cell
      col1: Type of derivative — fitz packs ALL sub-rows into one cell with \\n
      col2: Major hedged item
      col3: Contract amount  (packed: one value per sub-row)
      col5: Fair value       (packed: one value per sub-row)
      col4: Contract amount due after 1 year -- NOT captured per user spec

    fitz packs each merged cell group into ONE DataFrame row.
    E.g. col1 = "Currency swaps\\nYen-receipt / Foreign\\ncurrency payment\\nU.S. dollars\\nEuros"
    and  col3 = "135,725\\n133,360\\n2,365"  (3 values for 4 label lines)

    Strategy: n_vals = count of numeric values in col3 or col5.
    The first (n_labels - n_vals + 1) labels merge into the compound type-of-derivative header.
    Remaining labels are individual sub-rows, each paired with its own value.
    """
    rows           = []
    current_method = None

    for ri in range(len(df)):
        raw0  = _clean(str(df.iloc[ri, 0]))
        raw1  = str(df.iloc[ri, 1])          # keep \n for _split — do NOT _clean yet

        # Update hedge method from col0 if non-empty
        if raw0 and raw0.lower() not in ("none", ""):
            n0 = _norm(raw0)
            if "deferred" in n0:
                current_method = "deferred"
            elif "fair value" in n0 and "hedge" in n0:
                current_method = "fair_value"
            elif "exceptional" in n0:
                current_method = "exceptional"

        # Total row — single FV value, no sub-expansion needed
        is_total = ("total" in _norm(raw0) or "total" in _norm(raw1))
        if is_total:
            fv    = _parse_num(str(df.iloc[ri, 5])) if df.shape[1] > 5 else None
            contr = _parse_num(str(df.iloc[ri, 3])) if df.shape[1] > 3 else None
            if contr is not None or fv is not None:
                rows.append({"method": "TOTAL", "label": "Total",
                             "contract": contr, "fv": fv})
            continue

        # Split all packed cells (keep raw \n in value cells too)
        labels     = _join_continued(_split(raw1))
        contrs_raw = _split(str(df.iloc[ri, 3])) if df.shape[1] > 3 else []
        fvs_raw    = _split(str(df.iloc[ri, 5])) if df.shape[1] > 5 else []

        contrs = [_parse_num(v) for v in contrs_raw]
        fvs    = [_parse_num(v) for v in fvs_raw]

        # Number of actual data rows = max non-None values across cols 3 and 5
        n_vals = max(len([v for v in contrs if v is not None]),
                     len([v for v in fvs    if v is not None]),
                     len(contrs), len(fvs))
        if n_vals == 0 or not labels:
            continue

        # Merge excess leading labels into compound type-of-derivative header
        n_excess      = max(0, len(labels) - n_vals)
        compound_label = " ".join(labels[:n_excess + 1])
        sub_labels     = labels[n_excess + 1:]

        def _gv(lst, i): return lst[i] if i < len(lst) else None

        # First row = compound type header (aggregate value)
        rows.append({
            "method":   current_method,
            "label":    compound_label,
            "contract": _gv(contrs, 0),
            "fv":       _gv(fvs, 0),
        })

        # Sub-rows (individual currencies / rate types)
        for i, lbl in enumerate(sub_labels):
            n = _norm(lbl)
            if not n or n == "none":
                continue
            rows.append({
                "method":   current_method,
                "label":    _clean(lbl),
                "contract": _gv(contrs, i + 1),
                "fv":       _gv(fvs, i + 1),
            })

    return rows


# ── Extractor ──────────────────────────────────────────────────────────────────
def extract(doc):
    pg = find_page(doc)
    if pg is None:
        print("  CURRELDER / INTRATEDER: NOT FOUND")
        return None, None, None

    print(f"  Page found: p{pg + 1}")
    period = _doc_period(doc, up_to_page=pg)
    print(f"  Period : {period}")

    tabs = doc[pg].find_tables()

    # Write raw TXT
    raw = _OUT / f"raw_fitz_hedge_p{pg + 1}.txt"
    with open(raw, "w", encoding="utf-8") as f:
        f.write(f"Page {pg + 1}  |  {len(tabs.tables)} table(s)\n\n")
        for i, t in enumerate(tabs.tables):
            df = t.to_pandas()
            f.write(f"=== Table {i}  {t.row_count}x{t.col_count}  "
                    f"bbox={[round(x) for x in t.bbox]}\n")
            f.write(df.to_string())
            f.write("\n\n")
    print(f"    Raw TXT -> {raw.name}")

    currelder_rows   = None
    intrateder_rows  = None

    for i, t in enumerate(tabs.tables):
        if t.col_count < 5:
            continue
        df      = t.to_pandas()
        ttype   = _table_type(df)
        if ttype is None:
            continue
        rows = _parse_hedge_df(df)
        if not rows:
            continue
        if ttype == "CURRELDER" and currelder_rows is None:
            currelder_rows = rows
            print(f"  CURRELDER  : Table {i}  {t.row_count}x{t.col_count}  rows={len(rows)}")
        elif ttype == "INTRATEDER" and intrateder_rows is None:
            intrateder_rows = rows
            print(f"  INTRATEDER : Table {i}  {t.row_count}x{t.col_count}  rows={len(rows)}")

    if currelder_rows is None:
        print("  CURRELDER  : no parseable table")
    if intrateder_rows is None:
        print("  INTRATEDER : no parseable table")

    return period, currelder_rows, intrateder_rows


# ── CSV writers ────────────────────────────────────────────────────────────────
def _write_csv(name, period, rows):
    out = _OUT / f"output_fitz_{name.lower()}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Method", "Label", f"Contract_{period}", f"FairValue_{period}"])
        for r in rows:
            w.writerow([r["method"], r["label"],
                        "" if r["contract"] is None else r["contract"],
                        "" if r["fv"]       is None else r["fv"]])
    print(f"    CSV -> {out.name}")


# ── Console display ────────────────────────────────────────────────────────────
def _print_rows(name, period, rows):
    print(f"\n{'-'*80}")
    print(f"  {name}  ({len(rows)} rows)  period={period}")
    print(f"{'-'*80}")
    hdr = f"  {'Method':<14} {'Label':<50} {'Contract':>13} {'FV':>12}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        def _f(v): return f"{v:>13,.0f}" if v is not None else f"{'':>13}"
        print(f"  {(r['method'] or ''):.<14} {r['label']:<50} "
              f"{_f(r['contract'])} {_f(r['fv'])}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    print(f"\nPDF: {pdf_path.name}\n{'='*70}")
    doc = fitz.open(str(pdf_path))

    period, cur_rows, intr_rows = extract(doc)

    if cur_rows:
        _write_csv("CURRELDER", period, cur_rows)
        _print_rows("CURRELDER", period, cur_rows)

    if intr_rows:
        _write_csv("INTRATEDER", period, intr_rows)
        _print_rows("INTRATEDER", period, intr_rows)

    doc.close()
    print(f"\n{'='*70}")
    print(f"  Period     : {period}")
    print(f"  CURRELDER  : {len(cur_rows):3d} rows"   if cur_rows  else "  CURRELDER  : NOT EXTRACTED")
    print(f"  INTRATEDER : {len(intr_rows):3d} rows"  if intr_rows else "  INTRATEDER : NOT EXTRACTED")


if __name__ == "__main__":
    main()
