"""
test_extraction.py
Validate extraction logic on the Q1 annual PDF (pr0515en-3-03.pdf).
Run:  python test_extraction.py
"""
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

try:
    import fitz
except ImportError:
    sys.exit('pip install pymupdf')
try:
    import camelot
    import warnings
    warnings.filterwarnings('ignore')
except ImportError:
    sys.exit('pip install camelot-py[cv]')

ANNUAL = Path(__file__).parent / 'Project_information' / 'pr0515en-3-03.pdf'

# ── helpers ──────────────────────────────────────────────────────────────────

_NUM_RE  = re.compile(r'\(?([\d,]+(?:\.\d+)?)\)?')
_BRKT_RE = re.compile(r'^\([\d,]+(?:\.\d+)?\)$')
_NOTE_RE = re.compile(r'\s*\(\*\d+\)')

def _clean(text):
    return re.sub(r'\s+', ' ', str(text)).strip()

def _norm(text):
    t = _NOTE_RE.sub('', _clean(text)).lower()
    return re.sub(r'[\[\]]', '', t)  # strip [bracket] sub-item notation in simplified reports

def _parse_num(text):
    t = _clean(text)
    if not t or t in ('-', '—', ''):
        return None
    if _BRKT_RE.match(t):
        return -float(t[1:-1].replace(',', ''))
    m = re.match(r'^-?[\d,]+(?:\.\d+)?$', t)
    if m:
        return float(t.replace(',', ''))
    # cell may have mixed text like "135,807  Other liabilities"
    m2 = re.search(r'\([\d,]+\)|[\d,]+', t)
    if m2:
        raw = m2.group()
        if raw.startswith('('):
            return -float(raw[1:-1].replace(',', ''))
        return float(raw.replace(',', ''))
    return None

def _split(cell):
    return [s.strip() for s in str(cell).split('\n') if s.strip()]

def _period_from_header(text):
    """'As of March 31, 2026' → '2026-Q1'"""
    m = re.search(
        r'As of\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})',
        _clean(text), re.I
    )
    if not m:
        return None
    months = {'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
              'july':7,'august':8,'september':9,'october':10,'november':11,'december':12}
    mn = months.get(m.group(1).lower(), 0)
    q  = config.MONTH_TO_QUARTER.get(mn, 0)
    return f'{m.group(3)}-Q{q}' if q else None

def _get_tables(pdf_path, page_1idx, flavor='lattice'):
    try:
        tbls = camelot.read_pdf(str(pdf_path), pages=str(page_1idx),
                                flavor=flavor, suppress_stdout=True)
        return [t.df for t in tbls]
    except Exception as e:
        print(f'  camelot error p{page_1idx}: {e}')
        return []

def _find_page(doc, *keywords, start=0):
    for pg in range(start, len(doc)):
        txt = doc[pg].get_text('text').lower()
        if all(k.lower() in txt for k in keywords):
            return pg
    return None

def _vals_by_fitz(doc, pg, x_min, x_max, y_min=130, y_max=700):
    """
    Extract numeric values from a page by x-coordinate range using fitz.
    Returns list of (y, value) tuples in top-to-bottom order.
    Each number is taken from the leftmost word at each y-level.
    """
    page = doc[pg]
    words = page.get_text('words')  # (x0,y0,x1,y1,word,block,line,word_no)
    candidates = []
    for w in words:
        x0, y0, word = w[0], w[1], w[4]
        if x_min <= x0 <= x_max and y_min <= y0 <= y_max:
            if re.match(r'^[\d,]+$|^\([\d,]+(?:\.\d+)?\)$', word):
                candidates.append((round(y0, 1), x0, word))
    candidates.sort()
    result = []
    prev_y = None
    for y, x0, word in candidates:
        if prev_y is None or abs(y - prev_y) > 3:
            v = _parse_num(word)
            if v is not None:
                result.append((y, v))
            prev_y = y
        # else: ignore duplicate words at same y (take first/leftmost)
    return result

# ── join continuation lines in a label list ──────────────────────────────────

_CONT_WORDS = {'network'}  # uppercase-start words that begin continuation lines

def _join_continued(items):
    """Merge lines that are continuations of the previous label."""
    out = []
    for item in items:
        if out and item:
            if item[0].islower():
                # handle hyphen line-breaks: "held-to-" + "maturity" → no space
                sep = '' if out[-1].endswith('-') else ' '
                out[-1] = out[-1] + sep + item
            elif item.split()[0].lower() in _CONT_WORDS:
                out[-1] = out[-1] + ' ' + item
            else:
                out.append(item)
        else:
            out.append(item)
    return out

# ── sequential label matcher ─────────────────────────────────────────────────

def _seq_match(pdf_labels, label_map):
    """
    Align pdf_labels list to label_map entries sequentially.
    Returns list of (map_index, pdf_label_index) pairs.
    Skips pdf labels not in map; skips map entries not found in pdf.
    """
    matches = []
    map_ptr = 0
    for pi, lbl in enumerate(pdf_labels):
        for mp in range(map_ptr, len(label_map)):
            if _norm(label_map[mp][0]) == _norm(lbl):
                matches.append((mp, pi))
                map_ptr = mp + 1
                break
        # If no match found: skip this PDF label, map_ptr stays unchanged
    return matches

# ═══════════════════════════════════════════════════════════════════════════
# TEST 1 — UNONCONSBS
# ═══════════════════════════════════════════════════════════════════════════

def test_unonconsbs(pdf_path, doc):
    print('\n=== UNONCONSBS ===')
    # Use body labels to avoid matching the Table of Contents on p1
    pg = _find_page(doc, 'Monetary claims bought', 'Policy loans', 'Agency accounts receivable')
    if pg is None:
        print('  NOT FOUND'); return {}, None
    print(f'  Found on p{pg+1}')
    dfs = _get_tables(pdf_path, pg+1)
    if not dfs:
        print('  No tables'); return {}, None
    df = dfs[0]

    # Detect period columns from header row
    periods = []
    for ci in range(df.shape[1]):
        hdr = ' '.join(_split(str(df.iloc[0, ci])))
        p = _period_from_header(hdr)
        if p and p not in periods:
            periods.append(p)
    print(f'  Periods: {periods}')

    # Find main data row (col0 has ASSETS block)
    data_row = None
    for ri in range(len(df)):
        items = _split(str(df.iloc[ri, 0]))
        if any('ASSETS' in i.upper() for i in items[:2]) and len(items) > 10:
            data_row = ri
            break
    if data_row is None:
        print('  Data row not found'); return {}, None

    # Extract labels and values
    raw_labels = _split(str(df.iloc[data_row, 0]))
    raw_labels = _join_continued(raw_labels)
    # Strip ASSETS: header
    if raw_labels and re.match(r'ASSETS', raw_labels[0], re.I):
        raw_labels = raw_labels[1:]

    # Value columns: find cols whose header parsed to a period
    val_cols = []
    for ci in range(df.shape[1]):
        hdr = ' '.join(_split(str(df.iloc[0, ci])))
        p = _period_from_header(hdr)
        if p:
            val_cols.append((ci, p))

    # Parse values per period column
    val_by_period = {}
    for ci, p in val_cols[:2]:
        raw_vals = _split(str(df.iloc[data_row, ci]))
        val_by_period[p] = [_parse_num(v) for v in raw_vals]

    # Total assets row
    total_by_period = {}
    for ri in range(len(df)):
        if _norm(str(df.iloc[ri, 0])) == 'total assets':
            for ci, p in val_cols[:2]:
                total_by_period[p] = _parse_num(str(df.iloc[ri, ci]))
            break

    # Sequential label matching
    result = {p: {} for p in periods}
    matches = _seq_match(raw_labels, config.UNONCONSBS_LABELS)
    for map_idx, pdf_idx in matches:
        lbl, code = config.UNONCONSBS_LABELS[map_idx]
        full_code = f'PICQD.UNONCONSBS.{code}'
        for p, vals in val_by_period.items():
            result[p][full_code] = vals[pdf_idx] if pdf_idx < len(vals) else None

    # Total
    for p, v in total_by_period.items():
        if p in result:
            result[p]['PICQD.UNONCONSBS.ASSET.TOTAL.Q'] = v

    # Report
    for p in sorted(result):
        non_none = sum(1 for v in result[p].values() if v is not None)
        print(f'  {p}: {non_none}/{len(result[p])} filled')
        # spot-check
        sc = result[p].get('PICQD.UNONCONSBS.ASSET.CASHDEP.Q')
        tot = result[p].get('PICQD.UNONCONSBS.ASSET.TOTAL.Q')
        print(f'    Cash and deposits={sc}, Total assets={tot}')

    return result, pg


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2 — UCONSBS
# ═══════════════════════════════════════════════════════════════════════════

def test_uconsbs(pdf_path, doc, start=0):
    print('\n=== UCONSBS ===')
    # title + body label: 'Unaudited Consolidated' is NOT a substring of 'Non-Consolidated'
    pg = _find_page(doc, 'Unaudited Consolidated Balance Sheets', 'Cash and deposits', start=start)
    if pg is None:
        print('  NOT FOUND'); return {}
    print(f'  Found on p{pg+1}')

    # ── Detect periods from camelot header row ──────────────────────────────
    dfs = _get_tables(pdf_path, pg+1)
    if not dfs:
        print('  No tables'); return {}
    df = dfs[0]

    periods = []
    period_x = {}  # period → approximate x-centre in fitz coords
    for ci in range(df.shape[1]):
        hdr = ' '.join(_split(str(df.iloc[0, ci])))
        p = _period_from_header(hdr)
        if p and p not in periods:
            periods.append(p)
    print(f'  Periods: {periods}')

    # ── Labels from camelot col0 (works fine for the label column) ──────────
    data_row = None
    for ri in range(len(df)):
        items = _split(str(df.iloc[ri, 0]))
        if any('ASSETS' in i.upper() for i in items[:2]) and len(items) > 5:
            data_row = ri
            break
    if data_row is None:
        print('  Data row not found'); return {}

    raw_labels = _join_continued(_split(str(df.iloc[data_row, 0])))
    if raw_labels and re.match(r'ASSETS', raw_labels[0], re.I):
        raw_labels = raw_labels[1:]

    # ── Value columns: camelot if counts match, fitz if displaced ──────────
    # Q1 annual 6-column two-column layout causes camelot to lose ~3 values in col2.
    # Q4/Q2/Q3 simplified 3-column tables have no displacement — camelot is correct.
    camelot_c1 = [_parse_num(v) for v in _split(str(df.iloc[data_row, 1]))]
    camelot_c2 = [_parse_num(v) for v in _split(str(df.iloc[data_row, 2]))] if df.shape[1] > 2 else []

    val_by_period = {}
    total_by_period = {}

    if len(camelot_c1) == len(camelot_c2):
        # Camelot aligned: simple 3-column layout (Q4, Q2, Q3)
        val_by_period[periods[0]] = camelot_c1
        if len(periods) > 1:
            val_by_period[periods[1]] = camelot_c2
        for ri in range(len(df)):
            if _norm(str(df.iloc[ri, 0])) == 'total assets':
                if periods:
                    total_by_period[periods[0]] = _parse_num(str(df.iloc[ri, 1]))
                if len(periods) > 1 and df.shape[1] > 2:
                    total_by_period[periods[1]] = _parse_num(str(df.iloc[ri, 2]))
                break
    else:
        # Camelot displaced (col1 vs col2 count mismatch) → use fitz by 'Amount' header x
        # 'Amount' sub-header words reliably locate each value column regardless of period
        page_words = doc[pg].get_text('words')
        amount_xs = sorted(set(
            round(w[0])
            for w in page_words
            if w[4].lower() == 'amount' and w[1] < 150
        ))
        # amount_xs: first two entries are always the ASSETS 2025 / current-period columns
        bands = [(amount_xs[i] - 20, amount_xs[i] + 45)
                 for i in range(min(2, len(amount_xs)))]
        while len(bands) < 2:
            bands.append(bands[-1] if bands else (190, 260))

        # Determine y_min just below the Amount headers (avoids header text itself)
        amount_ys = [w[1] for w in page_words if w[4].lower() == 'amount' and w[1] < 150]
        y_min = max(amount_ys) + 5 if amount_ys else 130

        for i, p in enumerate(periods[:2]):
            x_min, x_max = bands[i]
            fitz_vals = _vals_by_fitz(doc, pg, x_min, x_max, y_min=y_min)
            if fitz_vals:
                val_by_period[p] = [v for _, v in fitz_vals[:-1]]
                total_by_period[p] = fitz_vals[-1][1]

    # ── Sequential label matching ────────────────────────────────────────────
    result = {p: {} for p in periods}
    matches = _seq_match(raw_labels, config.UCONSBS_LABELS)
    for map_idx, pdf_idx in matches:
        lbl, code = config.UCONSBS_LABELS[map_idx]
        full_code = f'PICQD.UCONSBS.{code}'
        for p, vals in val_by_period.items():
            result[p][full_code] = vals[pdf_idx] if pdf_idx < len(vals) else None

    for p, v in total_by_period.items():
        if p in result:
            result[p]['PICQD.UCONSBS.ASSET.TOTAL.Q'] = v

    for p in sorted(result):
        non_none = sum(1 for v in result[p].values() if v is not None)
        print(f'  {p}: {non_none}/{len(result[p])} filled')
        sc  = result[p].get('PICQD.UCONSBS.ASSET.CASHDEP.Q')
        tot = result[p].get('PICQD.UCONSBS.ASSET.TOTAL.Q')
        print(f'    Cash and deposits={sc}  (expected 2025: 1976083, 2026: 1752984)')
        print(f'    Total assets={tot}  (expected 2025: 59555692, 2026: 58442160)')

    return result


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3 — FAIRVAL
# ═══════════════════════════════════════════════════════════════════════════

def test_fairval(pdf_path, doc, fallback_period=None):
    print('\n=== FAIRVAL ===')
    # Use a body label so we don't match TOC entry for this section
    pg = _find_page(doc, 'Fair Values of Financial Instruments', 'Monetary claims bought')
    if pg is None:
        print('  NOT FOUND'); return {}
    print(f'  Found on p{pg+1}')
    dfs = _get_tables(pdf_path, pg+1)
    if not dfs:
        print('  No tables'); return {}
    df = dfs[0]

    # Detect current period from page title (single period table)
    # Fall back: use doc title period
    cur_period = None
    for pg2 in range(max(0, pg-1), min(len(doc), pg+2)):
        txt = doc[pg2].get_text('text')
        m = re.search(r'(?:Fiscal Year|(?:Three|Six|Nine) Months) Ended (\w+) \d{1,2}, (\d{4})', txt, re.I)
        if m:
            months = {'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
                      'july':7,'august':8,'september':9,'october':10,'november':11,'december':12}
            mn = months.get(m.group(1).lower(), 0)
            q  = config.MONTH_TO_QUARTER.get(mn, 0)
            cur_period = f'{m.group(2)}-Q{q}' if q else None
            break

    # Find main data row
    data_row = None
    for ri in range(len(df)):
        if len(_split(str(df.iloc[ri, 0]))) > 4:
            data_row = ri
            break
    if cur_period is None:
        cur_period = fallback_period
    if data_row is None or cur_period is None:
        print(f'  data_row={data_row} cur_period={cur_period}'); return {}

    raw_labels = [_NOTE_RE.sub('', l) for l in _split(str(df.iloc[data_row, 0]))]
    cons_vals  = _split(str(df.iloc[data_row, 1]))
    fv_vals    = _split(str(df.iloc[data_row, 2]))

    # Total assets row
    total_cons = total_fv = None
    for ri in range(len(df)):
        if _norm(str(df.iloc[ri, 0])) == 'total assets':
            total_cons = _parse_num(str(df.iloc[ri, 1]))
            total_fv   = _parse_num(str(df.iloc[ri, 2]))
            break

    result = {cur_period: {}}
    matches = _seq_match(raw_labels, config.FAIRVAL_LABELS)
    for map_idx, pdf_idx in matches:
        lbl, cons_code, fv_code = config.FAIRVAL_LABELS[map_idx]
        result[cur_period][f'PICQD.{cons_code}'] = (
            _parse_num(cons_vals[pdf_idx]) if pdf_idx < len(cons_vals) else None)
        result[cur_period][f'PICQD.{fv_code}'] = (
            _parse_num(fv_vals[pdf_idx]) if pdf_idx < len(fv_vals) else None)

    result[cur_period]['PICQD.FAIRVAL.ASSET.CONS.TOTAL.Q']   = total_cons
    result[cur_period]['PICQD.FAIRVAL.ASSET.FAIRVAL.TOTAL.Q'] = total_fv

    non_none = sum(1 for v in result[cur_period].values() if v is not None)
    print(f'  {cur_period}: {non_none}/{len(result[cur_period])} filled')
    mc = result[cur_period].get('PICQD.FAIRVAL.ASSET.CONS.MONCLAIM.Q')
    print(f'    Monetary claims cons={mc} (expected 21229)')
    return result


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4 — HELDMAT / POLRES
# ═══════════════════════════════════════════════════════════════════════════

def _extract_bonds_table(df, prefix, label_map, cur_period):
    """Extract a HELDMAT or POLRES table (shape 6,4)."""
    result = {cur_period: {}}
    SECTIONS = [
        ('exceeds', 'exceed', 1),
        ('does not exceed', 'notexc', 3),
    ]
    for section_kw, section_key, row_idx in SECTIONS:
        row_lbl_items = _split(str(df.iloc[row_idx, 0]))
        row_lbl_items = _join_continued(row_lbl_items)
        # Skip 2 header lines ("Those for which...", "...balance sheet amount")
        sec_labels = [l for l in row_lbl_items
                      if not re.match(r'those for which|balance sheet amount|consolidated', l, re.I)]
        cons_vals = _split(str(df.iloc[row_idx, 1]))
        fv_vals   = _split(str(df.iloc[row_idx, 2]))

        cfg = label_map[section_key]
        for li, lbl in enumerate(sec_labels):
            for ci_cfg, (cfg_lbl, cfg_suf) in enumerate(cfg):
                if _norm(cfg_lbl) == _norm(lbl):
                    result[cur_period][f'PICQD.{prefix}.CONS.{section_key.upper()}.{cfg_suf}'] = (
                        _parse_num(cons_vals[li]) if li < len(cons_vals) else None)
                    result[cur_period][f'PICQD.{prefix}.FAIRVAL.{section_key.upper()}.{cfg_suf}'] = (
                        _parse_num(fv_vals[li]) if li < len(fv_vals) else None)
                    break
    return result


def test_heldmat_polres(pdf_path, doc, cur_period):
    print('\n=== HELDMAT + POLRES ===')
    pg = _find_page(doc, 'Held-to-maturity Bonds', 'fair value exceeds')
    if pg is None:
        print('  NOT FOUND'); return {}
    print(f'  Found on p{pg+1}')
    dfs = _get_tables(pdf_path, pg+1)
    if len(dfs) < 2:
        print(f'  Only {len(dfs)} table(s) found (need 2)'); return {}

    r_heldmat = _extract_bonds_table(dfs[0], 'HELDMAT', config.BONDS_SEC_LABELS, cur_period)
    r_polres  = _extract_bonds_table(dfs[1], 'POLRES',  config.BONDS_SEC_LABELS, cur_period)

    for nm, r in [('HELDMAT', r_heldmat), ('POLRES', r_polres)]:
        non_none = sum(1 for v in r[cur_period].values() if v is not None)
        print(f'  {nm} {cur_period}: {non_none}/{len(r[cur_period])} filled')
        sample = next(iter(r[cur_period].items()), None)
        if sample:
            print(f'    sample: {sample[0]}={sample[1]}')

    merged = {cur_period: {}}
    merged[cur_period].update(r_heldmat.get(cur_period, {}))
    merged[cur_period].update(r_polres.get(cur_period, {}))
    return merged


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5 — ASALSEC
# ═══════════════════════════════════════════════════════════════════════════

def test_asalsec(pdf_path, doc, cur_period):
    print('\n=== ASALSEC ===')
    pg = _find_page(doc, 'Available-for-sale Securities', 'fair value exceeds', 'Cost')
    if pg is None:
        # fallback: find page with ASALSEC context
        pg = _find_page(doc, 'Available-for-sale Securities', 'Consolidated balance sheet amount', 'Cost')
    if pg is None:
        print('  NOT FOUND'); return {}
    print(f'  Found on p{pg+1}')
    dfs = _get_tables(pdf_path, pg+1)
    # Table 0 = ASALSEC, Table 1 = ASALSECSOLD-like bonds sold table (skip)
    if not dfs:
        print('  No tables'); return {}
    df = dfs[0]

    result = {cur_period: {}}
    SECTIONS = [
        ('exceed', 1),
        ('notexc', 3),
    ]
    for section_key, row_idx in SECTIONS:
        row_lbl_items = _join_continued(_split(str(df.iloc[row_idx, 0])))
        sec_labels = [l for l in row_lbl_items
                      if not re.match(r'those for which|balance sheet amount|consolidated', l, re.I)]
        cons_vals = _split(str(df.iloc[row_idx, 1]))
        cost_vals = _split(str(df.iloc[row_idx, 2]))

        cfg = config.ASALSEC_LABELS[section_key]
        matches = _seq_match(sec_labels, cfg)
        for map_idx, pdf_idx in matches:
            lbl, cons_suf, cost_suf = cfg[map_idx]
            result[cur_period][f'PICQD.ASALSEC.{cons_suf}'] = (
                _parse_num(cons_vals[pdf_idx]) if pdf_idx < len(cons_vals) else None)
            result[cur_period][f'PICQD.ASALSEC.{cost_suf}'] = (
                _parse_num(cost_vals[pdf_idx]) if pdf_idx < len(cost_vals) else None)

    non_none = sum(1 for v in result[cur_period].values() if v is not None)
    print(f'  {cur_period}: {non_none}/{len(result[cur_period])} filled')
    sc = result[cur_period].get('PICQD.ASALSEC.CONS.EXCEED.BOND.Q')
    print(f'    Bonds (exceed) cons={sc}')
    return result


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6 — MONHELD + ASALSECSOLD (same page)
# ═══════════════════════════════════════════════════════════════════════════

def test_monheld_asalsecsold(pdf_path, doc, cur_period):
    print('\n=== MONHELD + ASALSECSOLD ===')
    pg = _find_page(doc, 'Available-for-sale Securities Sold during the Fiscal Year')
    if pg is None:
        print('  ASALSECSOLD NOT FOUND'); return {}
    print(f'  Found on p{pg+1}')
    dfs = _get_tables(pdf_path, pg+1)
    if not dfs:
        print('  No tables'); return {}

    result = {cur_period: {}}

    # ASALSECSOLD = Table 0
    df_sold = dfs[0]
    data_row = None
    for ri in range(len(df_sold)):
        items = _split(str(df_sold.iloc[ri, 0]))
        if len(items) > 3 and any('bond' in i.lower() for i in items):
            data_row = ri
            break
    if data_row is not None:
        raw_labels = _join_continued(_split(str(df_sold.iloc[data_row, 0])))
        sal_vals  = _split(str(df_sold.iloc[data_row, 1]))
        gain_vals = _split(str(df_sold.iloc[data_row, 2]))
        los_vals  = _split(str(df_sold.iloc[data_row, 3]))

        cfg = config.ASALSECSOLD_LABELS
        matches = _seq_match(raw_labels, cfg)
        for map_idx, pdf_idx in matches:
            lbl, sal_suf, gain_suf, los_suf = cfg[map_idx]
            result[cur_period][f'PICQD.ASALSECSOLD.{sal_suf}']  = (
                _parse_num(sal_vals[pdf_idx]) if pdf_idx < len(sal_vals) else None)
            result[cur_period][f'PICQD.ASALSECSOLD.{gain_suf}'] = (
                _parse_num(gain_vals[pdf_idx]) if pdf_idx < len(gain_vals) else None)
            result[cur_period][f'PICQD.ASALSECSOLD.{los_suf}']  = (
                _parse_num(los_vals[pdf_idx]) if pdf_idx < len(los_vals) else None)
        non_none = sum(1 for k, v in result[cur_period].items()
                       if 'ASALSECSOLD' in k and v is not None)
        print(f'  ASALSECSOLD {cur_period}: {non_none} filled')
        sc = result[cur_period].get('PICQD.ASALSECSOLD.SAL.BOND.Q')
        print(f'    Bonds Sales={sc} (expected 467360)')

    # MONHELD = Table 1 (shape 3,6)
    if len(dfs) > 1:
        df_mh = dfs[1]
        for ri in range(len(df_mh)):
            cell0 = _norm(str(df_mh.iloc[ri, 0]))
            if 'specified' in cell0 or 'money held in trust' in cell0:
                result[cur_period]['PICQD.MONHELD.CONS.SPECMONHELD.Q'] = _parse_num(str(df_mh.iloc[ri, 1]))
                result[cur_period]['PICQD.MONHELD.COST.SPECMONHELD.Q'] = _parse_num(str(df_mh.iloc[ri, 2]))
                print(f'  MONHELD: cons={result[cur_period]["PICQD.MONHELD.CONS.SPECMONHELD.Q"]} cost={result[cur_period]["PICQD.MONHELD.COST.SPECMONHELD.Q"]}')
                break

    return result


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7 — CURRELDER + INTRATEDER (same page)
# ═══════════════════════════════════════════════════════════════════════════

def test_derivatives(pdf_path, doc, cur_period):
    print('\n=== CURRELDER + INTRATEDER ===')
    pg = _find_page(doc, 'Fair value hedge accounting', 'Forward foreign exchange')
    if pg is None:
        pg = _find_page(doc, 'Derivative transactions', 'hedge accounting is applied')
    if pg is None:
        print('  NOT FOUND'); return {}
    print(f'  Found on p{pg+1}')
    dfs = _get_tables(pdf_path, pg+1)
    if not dfs:
        print('  No tables'); return {}

    result = {cur_period: {}}

    # ── CURRELDER: Table 1 (shape 4,6), Row 2 = Fair value hedge / Forward forex ──
    currelder_df = None
    for df in dfs:
        # Find the table that has 'fair value hedge' in it
        txt = ' '.join(str(df.iloc[ri, ci])
                       for ri in range(len(df)) for ci in range(df.shape[1])).lower()
        if 'fair value hedge accounting' in txt and 'forward foreign' in txt:
            currelder_df = df
            break

    if currelder_df is not None:
        fv_hedge_row = None
        for ri in range(len(currelder_df)):
            items = _split(str(currelder_df.iloc[ri, 1]))
            if any('forward foreign' in i.lower() for i in items):
                fv_hedge_row = ri
                break
        if fv_hedge_row is not None:
            items_col1 = _split(str(currelder_df.iloc[fv_hedge_row, 1]))
            # Find 'Sold' start index
            sold_idx = next((i for i, x in enumerate(items_col1)
                             if _norm(x) == 'sold'), None)
            if sold_idx is not None:
                deriv_labels = items_col1[sold_idx:]  # [Sold, USD, EUR, AUD, Other]
                contr_vals   = _split(str(currelder_df.iloc[fv_hedge_row, 3]))
                fv_vals      = _split(str(currelder_df.iloc[fv_hedge_row, 5]))

                for li, lbl in enumerate(deriv_labels):
                    for map_idx, (cfg_lbl, contr_suf, fv_suf) in enumerate(config.CURRELDER_LABELS[:-1]):
                        if _norm(cfg_lbl) == _norm(lbl):
                            result[cur_period][f'PICQD.{contr_suf}'] = (
                                _parse_num(contr_vals[li]) if li < len(contr_vals) else None)
                            result[cur_period][f'PICQD.{fv_suf}'] = (
                                _parse_num(fv_vals[li]) if li < len(fv_vals) else None)
                            break

        # Total row (last row of currelder_df)
        total_row = len(currelder_df) - 1
        result[cur_period]['PICQD.CURRELDER.CONTR.TOTAL.Q'] = _parse_num(
            str(currelder_df.iloc[total_row, 3]))
        result[cur_period]['PICQD.CURRELDER.FAIRVAL.TOTAL.Q'] = _parse_num(
            str(currelder_df.iloc[total_row, 5]))

        cr_cnt = sum(1 for k, v in result[cur_period].items() if 'CURRELDER' in k and v is not None)
        print(f'  CURRELDER {cur_period}: {cr_cnt} non-None')
        sc = result[cur_period].get('PICQD.CURRELDER.CONTR.SOLD.Q')
        fvt = result[cur_period].get('PICQD.CURRELDER.FAIRVAL.TOTAL.Q')
        print(f'    Sold contract={sc} (expected 1298661), FV total={fvt} (expected -71187)')

    # ── INTRATEDER: last table on the page (shape 3,6) ──
    intrateder_df = None
    for df in reversed(dfs):
        txt = ' '.join(str(df.iloc[ri, ci])
                       for ri in range(len(df)) for ci in range(df.shape[1])).lower()
        if 'interest rate swap' in txt or 'deferred hedge' in txt:
            intrateder_df = df
            break

    if intrateder_df is not None:
        for ri in range(len(intrateder_df)):
            row_txt = _norm(' '.join(_split(str(intrateder_df.iloc[ri, 0]))))
            if 'deferred hedge' in row_txt or 'deferred' in row_txt:
                for map_idx, (cfg_lbl, contr_suf, fv_suf) in enumerate(config.INTRATEDER_LABELS):
                    if 'deferred' in _norm(cfg_lbl):
                        result[cur_period][f'PICQD.{contr_suf}'] = _parse_num(str(intrateder_df.iloc[ri, 3]))
                        result[cur_period][f'PICQD.{fv_suf}']    = _parse_num(str(intrateder_df.iloc[ri, 5]))
                        break
            typ_items = _split(str(intrateder_df.iloc[ri, 1]))
            if any('exceptional' in i.lower() for i in typ_items):
                result[cur_period]['PICQD.INTRATEDER.CONTR.EXCTREATINT.Q'] = _parse_num(
                    str(intrateder_df.iloc[ri, 3]))
                result[cur_period]['PICQD.INTRATEDER.FAIRVAL.EXCTREATINT.Q'] = _parse_num(
                    str(intrateder_df.iloc[ri, 5]))

        itr_cnt = sum(1 for k, v in result[cur_period].items() if 'INTRATEDER' in k and v is not None)
        print(f'  INTRATEDER {cur_period}: {itr_cnt} non-None')
        d = result[cur_period].get('PICQD.INTRATEDER.CONTR.DEFHEDGE.Q')
        fvd = result[cur_period].get('PICQD.INTRATEDER.FAIRVAL.DEFHEDGE.Q')
        print(f'    Deferred contract={d} (expected 300000), FV={fvd} (expected -72803)')

    return result


# ═══════════════════════════════════════════════════════════════════════════
# TEST 8 — AHELDMAT
# ═══════════════════════════════════════════════════════════════════════════

def test_aheldmat(pdf_path, doc):
    print('\n=== AHELDMAT ===')
    pg = _find_page(doc, 'Assets held-to-maturity in trust',
                    'assets held for reserves in trust')
    if pg is None:
        print('  NOT FOUND'); return {}
    print(f'  Found on p{pg+1}')
    dfs = _get_tables(pdf_path, pg+1)
    # Table 2 is the AHELDMAT table (index 2)
    if len(dfs) < 3:
        print(f'  Only {len(dfs)} tables (need table idx 2)'); return {}
    df = dfs[2]

    # Detect year columns from row 0
    yr1_col = yr2_col = None
    for ci in range(df.shape[1]):
        cell = _clean(str(df.iloc[0, ci]))
        if cell in ('2025', '2026', 'March 31, 2025', 'March 31, 2026'):
            if yr1_col is None:
                yr1_col = ci
            elif yr2_col is None:
                yr2_col = ci
                break

    # row 1 has "Book value" — find the "Book value" column for each year
    # col1 = Book value yr1, col6 = Book value yr2 (from probe)
    bv_col1 = 1  # Book value for first year period
    bv_col2 = 6  # Book value for second year period

    # Determine periods from row 0
    yr1_str = _clean(str(df.iloc[0, bv_col1])) if bv_col1 < df.shape[1] else ''
    yr2_str = _clean(str(df.iloc[0, bv_col2])) if bv_col2 < df.shape[1] else ''

    _MONTHS = {'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
               'july':7,'august':8,'september':9,'october':10,'november':11,'december':12}

    def _yr_to_period(yr_str):
        yr_str = yr_str.strip()
        if re.match(r'^\d{4}$', yr_str):
            return f'{yr_str}-Q1'
        p = _period_from_header(yr_str)
        if p:
            return p
        m = re.search(r'(\w+)\s+\d{1,2},?\s+(\d{4})', yr_str, re.I)
        if m:
            mn = _MONTHS.get(m.group(1).lower(), 0)
            q = config.MONTH_TO_QUARTER.get(mn, 0)
            if q:
                return f'{m.group(2)}-Q{q}'
        return yr_str

    p1 = _yr_to_period(yr1_str)
    p2 = _yr_to_period(yr2_str)
    print(f'  Periods: [{p1}, {p2}]')

    result = {}
    for p in [p1, p2]:
        if p:
            result[p] = {}

    for ri in range(len(df)):
        raw_lbl = re.sub(r'-\s+', '-', _clean(' '.join(_split(str(df.iloc[ri, 0])))))
        if not raw_lbl:
            continue
        for cfg_lbl, cfg_suf in config.AHELDMAT_LABELS:
            if _norm(cfg_lbl) in _norm(raw_lbl) or _norm(raw_lbl) in _norm(cfg_lbl):
                full_code = f'PICQD.{cfg_suf}'
                if p1 and bv_col1 < df.shape[1]:
                    result.setdefault(p1, {})[full_code] = _parse_num(
                        str(df.iloc[ri, bv_col1]))
                if p2 and bv_col2 < df.shape[1]:
                    result.setdefault(p2, {})[full_code] = _parse_num(
                        str(df.iloc[ri, bv_col2]))
                break

    for p in sorted(result):
        non_none = sum(1 for v in result[p].values() if v is not None)
        print(f'  {p}: {non_none}/{len(result[p])} filled')
        for k, v in result[p].items():
            print(f'    {k}={v}')

    return result


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ANNUAL
    print(f'Testing extraction on: {pdf_path}')
    doc = fitz.open(str(pdf_path))

    # Detect current period
    cur_period = None
    for pg in range(min(3, len(doc))):
        txt = doc[pg].get_text('text')
        m = re.search(r'(?:Fiscal Year|(?:Three|Six|Nine) Months) Ended (\w+) \d{1,2}, (\d{4})', txt, re.I)
        if m:
            months = {'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
                      'july':7,'august':8,'september':9,'october':10,'november':11,'december':12}
            mn = months.get(m.group(1).lower(), 0)
            q = config.MONTH_TO_QUARTER.get(mn, 0)
            cur_period = f'{m.group(2)}-Q{q}'
            break
    print(f'Current period: {cur_period}')

    all_results = {}

    r, unon_pg = test_unonconsbs(str(pdf_path), doc)
    for p, d in r.items():
        all_results.setdefault(p, {}).update(d)

    r = test_uconsbs(str(pdf_path), doc, start=(unon_pg or 0) + 1)
    for p, d in r.items():
        all_results.setdefault(p, {}).update(d)

    r = test_fairval(str(pdf_path), doc, cur_period)
    for p, d in r.items():
        all_results.setdefault(p, {}).update(d)

    r = test_heldmat_polres(str(pdf_path), doc, cur_period)
    for p, d in r.items():
        all_results.setdefault(p, {}).update(d)

    r = test_asalsec(str(pdf_path), doc, cur_period)
    for p, d in r.items():
        all_results.setdefault(p, {}).update(d)

    r = test_monheld_asalsecsold(str(pdf_path), doc, cur_period)
    for p, d in r.items():
        all_results.setdefault(p, {}).update(d)

    r = test_derivatives(str(pdf_path), doc, cur_period)
    for p, d in r.items():
        all_results.setdefault(p, {}).update(d)

    r = test_aheldmat(str(pdf_path), doc)
    for p, d in r.items():
        all_results.setdefault(p, {}).update(d)

    doc.close()

    print('\n\n=== SUMMARY ===')
    for p in sorted(all_results):
        vals = all_results[p]
        total = len(config.COLUMN_CODES)
        filled = sum(1 for c in config.COLUMN_CODES if vals.get(c) is not None)
        print(f'{p}: {filled}/{total} columns filled ({100*filled//total}%)')

    print('\nDone.')


if __name__ == '__main__':
    main()
