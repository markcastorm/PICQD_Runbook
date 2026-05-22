"""
extractor.py — Production extraction module.

Opens a single PDF, runs all table extractors, and returns:
  list of (period: str, data: dict[column_code -> value | None])

Annual Q1 PDFs (two period columns in BS tables) return TWO records:
  [(prior_period, prior_data), (current_period, current_data)]
Other PDFs return a single record:
  [(period, data)]

All 228 COLUMN_CODES from config.py are present as keys in every data dict.
"""
import re
import fitz
from pathlib import Path

from config import (
    COLUMN_CODES,
    UNONCONSBS_LABELS, UCONSBS_LABELS,
    FAIRVAL_LABELS,
    BONDS_SEC_LABELS,
    ASALSEC_LABELS,
    ASALSECSOLD_LABELS,
    CURRELDER_LABELS, INTRATEDER_LABELS,
    AHELDMAT_LABELS,
)

import fitz_bs                    as _bs
import fitz_fairval               as _fv
import fitz_bonds                 as _bonds
import fitz_asalsec               as _asalsec
import fitz_asalsecsold_monheld   as _sold
import fitz_currelder_intrateder  as _curr
import fitz_aheldmat              as _aheldt

# ── Normalise label text for comparison ───────────────────────────────────────
_NOTE = re.compile(r"\s*\(\*\d*\)")

def _norm(text):
    t = _NOTE.sub("", re.sub(r"\s+", " ", str(text))).lower().strip()
    return re.sub(r"[\[\]]", "", t)


# ── Generic sequential label matcher ─────────────────────────────────────────
def _seq_match(rows, label_map, get_vals, contains=False):
    """
    Walk label_map entries in order; for each entry scan forward in rows.
    - Match (exact or substring when contains=True): emit value(s), advance both.
    - No match in rows: config entry stays absent (None).
    - PDF rows not matching any config entry: silently skipped.

    label_map entries: (label, code1, code2, ...) — any number of codes.
    get_vals(row) must return a list matching len(entry) - 1.

    Returns dict {code: value}.
    """
    result = {}
    row_ptr = 0
    for entry in label_map:
        cfg_label = entry[0]
        codes     = entry[1:]
        cfg_n     = _norm(cfg_label)
        # Scan forward; if not found, row_ptr stays unchanged
        for j in range(row_ptr, len(rows)):
            row_n = _norm(rows[j].get('label', ''))
            match = (cfg_n in row_n) if contains else (cfg_n == row_n)
            if match:
                vals = get_vals(rows[j])
                for code, val in zip(codes, vals):
                    result[code] = val
                row_ptr = j + 1
                break
    return result


# ── Balance sheets ────────────────────────────────────────────────────────────
def _map_bs(rows, period, label_map, prefix):
    """Map BS rows (section/label/values dict) → column codes for one period."""
    assets = [r for r in rows if r.get('section') == 'ASSETS']
    def get_v(row):
        return [row['values'].get(period)]
    return _seq_match(assets, [(lbl, prefix + sfx) for lbl, sfx in label_map], get_v)


# ── FAIRVAL ───────────────────────────────────────────────────────────────────
def _map_fairval(rows):
    """Map FAIRVAL ASSETS rows → CONS + FAIRVAL column codes."""
    assets = [r for r in rows if r.get('section') == 'ASSETS']
    def get_v(row):
        return [row.get('cons'), row.get('fv')]
    return _seq_match(assets, [(lbl, 'PICQD.' + c, 'PICQD.' + f) for lbl, c, f in FAIRVAL_LABELS], get_v)


# ── HELDMAT / POLRES ──────────────────────────────────────────────────────────
def _map_bonds(rows, table_prefix):
    """Map HELDMAT or POLRES rows → CONS + FAIRVAL per section."""
    result = {}
    for section_key, entries in BONDS_SEC_LABELS.items():
        sec_rows = [r for r in rows if r.get('section') == section_key]
        sec_up   = section_key.upper()
        def get_v(row, _=None):
            return [row.get('cons'), row.get('fv')]
        label_map = [(lbl, f'PICQD.{table_prefix}.CONS.{sec_up}.{sfx}',
                           f'PICQD.{table_prefix}.FAIRVAL.{sec_up}.{sfx}')
                     for lbl, sfx in entries]
        result.update(_seq_match(sec_rows, label_map, get_v))
    return result


# ── ASALSEC ───────────────────────────────────────────────────────────────────
def _map_asalsec(rows):
    """Map ASALSEC rows → CONS + COST column codes per section."""
    result = {}
    for section_key, entries in ASALSEC_LABELS.items():
        sec_rows = [r for r in rows if r.get('section') == section_key]
        def get_v(row, _=None):
            return [row.get('cons'), row.get('cost')]
        label_map = [(lbl, 'PICQD.ASALSEC.' + c, 'PICQD.ASALSEC.' + co) for lbl, c, co in entries]
        result.update(_seq_match(sec_rows, label_map, get_v))
    return result


# ── MONHELD ───────────────────────────────────────────────────────────────────
def _map_monheld(row):
    if row is None:
        return {}
    return {
        'PICQD.MONHELD.CONS.SPECMONHELD.Q': row.get('cons'),
        'PICQD.MONHELD.COST.SPECMONHELD.Q': row.get('cost'),
    }


# ── ASALSECSOLD ───────────────────────────────────────────────────────────────
def _map_asalsecsold(rows):
    """Sequential match; 'Japanese local government bonds' skipped (not in config)."""
    def get_v(row):
        return [row.get('sales'), row.get('gains'), row.get('losses')]
    label_map = [(lbl, 'PICQD.ASALSECSOLD.' + s, 'PICQD.ASALSECSOLD.' + g, 'PICQD.ASALSECSOLD.' + l)
                 for lbl, s, g, l in ASALSECSOLD_LABELS]
    return _seq_match(rows, label_map, get_v)


# ── CURRELDER ─────────────────────────────────────────────────────────────────
def _map_currelder(rows):
    """
    Only fair_value rows + TOTAL.
    Config label 'Sold' matches 'Forward foreign exchange Sold' via contains match.
    """
    fv_rows = [r for r in rows if r.get('method') in ('fair_value', 'TOTAL')]
    def get_v(row):
        return [row.get('contract'), row.get('fv')]
    label_map = [(lbl, 'PICQD.' + c, 'PICQD.' + f) for lbl, c, f in CURRELDER_LABELS]
    return _seq_match(fv_rows, label_map, get_v, contains=True)


# ── INTRATEDER ────────────────────────────────────────────────────────────────
def _map_intrateder(rows):
    """Match rows by method name."""
    result = {}
    method_map = {
        'deferred':    INTRATEDER_LABELS[0],
        'exceptional': INTRATEDER_LABELS[1] if len(INTRATEDER_LABELS) > 1 else None,
    }
    for row in rows:
        method = row.get('method', '').lower()
        entry  = method_map.get(method)
        if entry:
            _, contr_col, fv_col = entry
            result['PICQD.' + contr_col] = row.get('contract')
            result['PICQD.' + fv_col]    = row.get('fv')
    return result


# ── AHELDMAT ──────────────────────────────────────────────────────────────────
def _map_aheldmat(rows, use_bv1=False):
    """Use bv1 for prior period, bv2 for current period (default)."""
    key = 'bv1' if use_bv1 else 'bv2'
    def get_v(row):
        return [row.get(key)]
    label_map = [(lbl, 'PICQD.' + sfx) for lbl, sfx in AHELDMAT_LABELS]
    return _seq_match(rows, label_map, get_v)


# ── Main entry point ──────────────────────────────────────────────────────────
def _set_module_out(pdf_dir):
    """Point every fitz module's _OUT to the per-PDF output directory."""
    for mod in (_bs, _fv, _bonds, _asalsec, _sold, _curr, _aheldt):
        mod._OUT = pdf_dir


def extract_all(pdf_path, run_dir=None):
    """
    Extract all 228 data points from a PDF.

    Parameters
    ----------
    pdf_path : path to the PDF file.
    run_dir  : Path-like, the shared timestamped run folder.
               Created under config.EXTRACTOR_DIR if not supplied.

    Returns
    -------
    records : list of (period: str, data: dict[code -> float|None])
        Annual Q1 PDFs (two BS period columns) return two records:
            [(prior_period, prior_data), (current_period, current_data)]
        Other PDFs return one record:
            [(period, data)]
        All records are sorted ascending by period.
    """
    import config as _cfg
    pdf_path = Path(pdf_path)

    # ── Per-PDF output directory ───────────────────────────────────────────────
    if run_dir is None:
        from datetime import datetime
        run_dir = Path(_cfg.EXTRACTOR_DIR) / datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = Path(run_dir)
    pdf_dir = run_dir / pdf_path.stem
    pdf_dir.mkdir(parents=True, exist_ok=True)
    _set_module_out(pdf_dir)

    doc = fitz.open(str(pdf_path))
    print(f"\n{'='*70}")
    print(f"PDF : {pdf_path.name}")
    print(f"{'='*70}")

    # ── Balance sheets ────────────────────────────────────────────────────────
    print("\n[1] Non-Consolidated Balance Sheets")
    pg1, p1, r1 = _bs.extract(doc, "UNONCONSBS", _bs.TABLE_FINGERPRINTS["UNONCONSBS"])

    print("\n[2] Consolidated Balance Sheets")
    pg2, p2, r2 = _bs.extract(doc, "UCONSBS", _bs.TABLE_FINGERPRINTS["UCONSBS"],
                               start=(pg1 + 1) if pg1 is not None else 0)

    # Determine periods — BS tables are the authoritative source
    all_periods = p1 if p1 else (p2 if p2 else [])
    has_prior   = len(all_periods) >= 2

    if has_prior:
        prior_period = all_periods[0]   # e.g. '2025-Q1'
        curr_period  = all_periods[-1]  # e.g. '2026-Q1'
    elif all_periods:
        prior_period = None
        curr_period  = all_periods[0]
    else:
        prior_period = None
        curr_period  = "UNKNOWN"

    # Initialise result dicts
    result_curr  = {code: None for code in COLUMN_CODES}
    result_prior = {code: None for code in COLUMN_CODES} if has_prior else None

    # Map BS for current period (and prior if available)
    if r1 and p1:
        result_curr.update(_map_bs(r1, curr_period, UNONCONSBS_LABELS, 'PICQD.UNONCONSBS.'))
        if has_prior:
            result_prior.update(_map_bs(r1, prior_period, UNONCONSBS_LABELS, 'PICQD.UNONCONSBS.'))
        n = sum(1 for c in result_curr if 'UNONCONSBS' in c and result_curr[c] is not None)
        print(f"    mapped {n} values (current period)")

    if r2 and p2:
        result_curr.update(_map_bs(r2, curr_period, UCONSBS_LABELS, 'PICQD.UCONSBS.'))
        if has_prior:
            result_prior.update(_map_bs(r2, prior_period, UCONSBS_LABELS, 'PICQD.UCONSBS.'))
        n = sum(1 for c in result_curr if 'UCONSBS' in c and result_curr[c] is not None)
        print(f"    mapped {n} values (current period)")

    # ── Investment / derivatives tables (current period only) ─────────────────
    print("\n[3] Fair Values of Financial Instruments")
    pf, fv_rows = _fv.extract(doc)
    if fv_rows:
        result_curr.update(_map_fairval(fv_rows))
        print(f"    mapped {sum(1 for c in result_curr if 'FAIRVAL' in c and result_curr[c] is not None)} values")

    print("\n[4] Held-to-maturity / Policy-reserve-matching Bonds")
    pb, h_rows, pol_rows = _bonds.extract(doc)
    if h_rows:
        result_curr.update(_map_bonds(h_rows, 'HELDMAT'))
        print(f"    HELDMAT: mapped {sum(1 for c in result_curr if '.HELDMAT.' in c and result_curr[c] is not None)} values")
    if pol_rows:
        result_curr.update(_map_bonds(pol_rows, 'POLRES'))
        print(f"    POLRES : mapped {sum(1 for c in result_curr if '.POLRES.' in c and result_curr[c] is not None)} values")

    print("\n[5] Available-for-sale Securities")
    pa, asal_rows = _asalsec.extract(doc)
    if asal_rows:
        result_curr.update(_map_asalsec(asal_rows))
        print(f"    mapped {sum(1 for c in result_curr if 'ASALSEC.' in c and result_curr[c] is not None)} values")

    print("\n[6] Available-for-sale Securities Sold / Money Held in Trust")
    ps, sold_rows, monheld_row = _sold.extract(doc)
    if sold_rows:
        result_curr.update(_map_asalsecsold(sold_rows))
        print(f"    ASALSECSOLD: mapped {sum(1 for c in result_curr if 'ASALSECSOLD' in c and result_curr[c] is not None)} values")
    result_curr.update(_map_monheld(monheld_row))
    if monheld_row:
        print(f"    MONHELD: mapped 2 values")

    print("\n[7] Currency / Interest-rate Derivatives")
    pc, cur_rows, intr_rows = _curr.extract(doc)
    if cur_rows:
        result_curr.update(_map_currelder(cur_rows))
        print(f"    CURRELDER : mapped {sum(1 for c in result_curr if 'CURRELDER' in c and result_curr[c] is not None)} values")
    if intr_rows:
        result_curr.update(_map_intrateder(intr_rows))
        print(f"    INTRATEDER: mapped {sum(1 for c in result_curr if 'INTRATEDER' in c and result_curr[c] is not None)} values")

    print("\n[8] Assets Held-to-maturity in Trust")
    pa1, pa2, aheldt_rows = _aheldt.extract(doc)
    if aheldt_rows:
        result_curr.update(_map_aheldmat(aheldt_rows, use_bv1=False))
        print(f"    mapped {sum(1 for c in result_curr if 'AHELDMAT' in c and result_curr[c] is not None)} values (current)")
        if has_prior:
            result_prior.update(_map_aheldmat(aheldt_rows, use_bv1=True))
            print(f"    mapped {sum(1 for c in result_prior if 'AHELDMAT' in c and result_prior[c] is not None)} values (prior)")

    doc.close()

    # Build records list (ascending by period)
    records = []
    if has_prior and result_prior is not None:
        records.append((prior_period, result_prior))
    records.append((curr_period, result_curr))

    # Summary
    print(f"\n{'='*70}")
    print(f"PDF : {pdf_path.name}")
    if has_prior:
        fp = sum(1 for v in result_prior.values() if v is not None)
        print(f"  Prior   : {prior_period}  {fp} / {len(COLUMN_CODES)} filled")
    fc = sum(1 for v in result_curr.values() if v is not None)
    print(f"  Current : {curr_period}  {fc} / {len(COLUMN_CODES)} filled")
    print(f"{'='*70}\n")

    return records
