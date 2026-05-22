"""
test_fitz_bonds.py
Extract Held-to-maturity Bonds (HELDMAT) and Policy-reserve-matching Bonds (POLRES)
using PyMuPDF find_tables().

Both tables:
  - Single period (current report date)
  - 4 columns: label | Consolidated BS amount | Fair value | Difference
  - 2 sections per table: exceed / notexc  +  Total row
  - HELDMAT: no Foreign securities/bonds rows
  - POLRES : includes Foreign securities + Foreign bonds rows

Page detection uses fingerprints (header + body labels) -- NOT hardcoded pages.
Usually same page (Q1 p53, Q3 p43) but handles different pages if needed.

Present in:  Q1 annual (pr0515en-3-03.pdf), Q3 (pr1114en-06.pdf)
Absent in:   Q4 simplified (pr0213en-03.pdf) -- NOT FOUND expected

Run:
  python test_fitz_bonds.py                                 # Q4 (NOT FOUND expected)
  python test_fitz_bonds.py path/to/q1_or_q3.pdf           # Q1 or Q3
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
# "held-to-maturity bonds" alone would match AHELDMAT page (p16).
# Body label "those for which fair value exceeds" is unique to the securities notes pages.
FINGERPRINTS = {
    "HELDMAT": {
        "header":      "held-to-maturity bonds",
        "body_labels": ["those for which fair value exceeds",
                        "japanese corporate bonds",
                        "japanese local government bonds",
                        "subtotal"],
        "min_matches": 3,
    },
    "POLRES": {
        "header":      "policy-reserve-matching bonds",
        "body_labels": ["those for which fair value exceeds",
                        "foreign securities",
                        "foreign bonds",
                        "japanese corporate bonds"],
        "min_matches": 3,
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────
_NOTE_RE = re.compile(r"\s*\(\*\d+\)")
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
    """Merge label lines: lowercase start or 'amount' continuation -> join to previous."""
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
    """
    Scan doc (up to up_to_page) for all 'As of' dates; return the LATEST one.
    Needed because prior-year column headers appear before the current-period header.
    """
    limit   = (up_to_page + 1) if up_to_page is not None else len(doc)
    pat     = re.compile(r"[Aa]s of\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})")
    latest  = None
    latest_key = (0, 0)   # (year, quarter)
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


# ── Table classifier ──────────────────────────────────────────────────────────
def _has_foreign(df):
    """POLRES tables contain 'foreign securities' / 'foreign bonds' in col0."""
    full = " ".join(str(df.iloc[ri, 0]) for ri in range(len(df))).lower()
    return "foreign" in full


# ── DataFrame parser ───────────────────────────────────────────────────────────
def _parse_bonds_df(df):
    """
    Parse a HELDMAT or POLRES DataFrame (6 rows x 4 cols from fitz).

    fitz packs section header + all data labels into ONE cell separated by \n.
    After _join_continued the first item is the section header text; the rest
    are the actual financial labels. val_idx is NOT incremented for section headers.

    Row 0: column headers (col0 = None/empty) -- skipped by strip check.
    Rows 1,3: data rows (packed).
    Rows 2,4: Subtotal.  Row 5: Total.
    """
    rows    = []
    section = None

    for ri in range(len(df)):
        raw0    = str(df.iloc[ri, 0])
        stripped = raw0.strip()

        # Skip empty / None rows (row 0 column headers)
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

            # Section header text — switch section, consume NO value slot
            if n.startswith("those for which fair value exceeds"):
                section = "exceed"
                continue
            if n.startswith("those for which fair value does not"):
                section = "notexc"
                continue

            # Grand total row
            if n == "total":
                rows.append({
                    "section": "TOTAL",
                    "label":   "Total",
                    "cons":    _g(col1_raw, val_idx),
                    "fv":      _g(col2_raw, val_idx),
                    "diff":    _g(col3_raw, val_idx),
                })
                val_idx += 1
                continue

            if section is None:
                val_idx += 1
                continue

            rows.append({
                "section": section,
                "label":   _clean(label),
                "cons":    _g(col1_raw, val_idx),
                "fv":      _g(col2_raw, val_idx),
                "diff":    _g(col3_raw, val_idx),
            })
            val_idx += 1

    return rows


# ── Extractor ──────────────────────────────────────────────────────────────────
def extract(doc):
    """
    Find HELDMAT and POLRES pages (may be same page).
    Returns (period, heldmat_rows, polres_rows).
    """
    heldmat_pg = find_page(doc, "HELDMAT")
    polres_pg  = find_page(doc, "POLRES", start=heldmat_pg if heldmat_pg is not None else 0)

    if heldmat_pg is None and polres_pg is None:
        print("  HELDMAT: NOT FOUND")
        print("  POLRES : NOT FOUND")
        return None, None, None

    # Get period from earliest table page (scans from doc start)
    earliest_pg = min(pg for pg in [heldmat_pg, polres_pg] if pg is not None)
    period = _doc_period(doc, up_to_page=earliest_pg)

    heldmat_rows = None
    polres_rows  = None

    # Collect all pages to process (deduplicated)
    pages_to_scan = list(dict.fromkeys(
        pg for pg in [heldmat_pg, polres_pg] if pg is not None
    ))

    for pg in pages_to_scan:
        tabs = doc[pg].find_tables()

        # Write raw TXT
        raw = _OUT / f"raw_fitz_bonds_p{pg + 1}.txt"
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
            df   = t.to_pandas()
            rows = _parse_bonds_df(df)
            if not rows:
                continue
            if _has_foreign(df):
                if polres_rows is None:
                    polres_rows = rows
                    print(f"  POLRES : p{pg + 1} Table {i}  {t.row_count}x{t.col_count}  rows={len(rows)}")
            else:
                if heldmat_rows is None:
                    heldmat_rows = rows
                    print(f"  HELDMAT: p{pg + 1} Table {i}  {t.row_count}x{t.col_count}  rows={len(rows)}")

    if heldmat_rows is None:
        print("  HELDMAT: NOT FOUND")
    if polres_rows is None:
        print("  POLRES : NOT FOUND")

    return period, heldmat_rows, polres_rows


# ── CSV writer ─────────────────────────────────────────────────────────────────
def write_csv(name, period, rows):
    out = _OUT / f"output_fitz_{name.lower()}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Section", "Label",
                    f"Cons_BS_{period}", f"FairValue_{period}", f"Diff_{period}"])
        for r in rows:
            w.writerow([r["section"], r["label"],
                        "" if r["cons"] is None else r["cons"],
                        "" if r["fv"]   is None else r["fv"],
                        "" if r["diff"] is None else r["diff"]])
    print(f"    CSV -> {out.name}")


# ── Console display ────────────────────────────────────────────────────────────
def print_table(name, period, rows):
    print(f"\n{'-'*85}")
    print(f"  {name}  ({len(rows)} rows)  period={period}")
    print(f"{'-'*85}")
    hdr = (f"  {'Sec':<10} {'Label':<46}"
           f" {'Cons BS':>14} {'Fair Value':>14} {'Diff':>12}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        def _f(v): return f"{v:>14,.0f}" if v is not None else f"{'':>14}"
        print(f"  {r['section']:<10} {r['label']:<46}"
              f" {_f(r['cons'])} {_f(r['fv'])} {_f(r['diff'])}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    print(f"\nPDF: {pdf_path.name}\n{'='*70}")
    doc = fitz.open(str(pdf_path))

    period, heldmat_rows, polres_rows = extract(doc)

    if heldmat_rows:
        write_csv("HELDMAT", period, heldmat_rows)
        print_table("HELDMAT", period, heldmat_rows)

    if polres_rows:
        write_csv("POLRES", period, polres_rows)
        print_table("POLRES", period, polres_rows)

    doc.close()
    print(f"\n{'='*70}")
    print(f"  Period  : {period}")
    print(f"  HELDMAT : {len(heldmat_rows):3d} rows" if heldmat_rows else "  HELDMAT : NOT EXTRACTED")
    print(f"  POLRES  : {len(polres_rows):3d} rows"  if polres_rows  else "  POLRES  : NOT EXTRACTED")


if __name__ == "__main__":
    main()
