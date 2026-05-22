"""
extract_unonconsbs_csv.py
Extract UNONCONSBS table from Q1 annual PDF → CSV for validation.
Run:  python extract_unonconsbs_csv.py
"""
import re, sys, csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

try:
    import fitz
except ImportError:
    sys.exit('pip install pymupdf')
try:
    import camelot, warnings
    warnings.filterwarnings('ignore')
except ImportError:
    sys.exit('pip install camelot-py[cv]')

ANNUAL  = Path(__file__).parent / 'Project_information' / 'pr0515en-3-03.pdf'
SAMPLE  = Path(__file__).parent / 'Project_information' / 'PICQD_DATA_20260515.xlsx - 2025-Q4.csv'
OUT_CSV = Path(__file__).parent / 'output_unonconsbs.csv'

# ── helpers ────────────────────────────────────────────────────────────────────
_NOTE_RE = re.compile(r'\s*\(\*\d+\)')
_BRKT_RE = re.compile(r'^\([\d,]+(?:\.\d+)?\)$')
_CONT_WORDS = {'network'}

def _clean(t): return re.sub(r'\s+', ' ', str(t)).strip()
def _norm(t):  return _NOTE_RE.sub('', _clean(t)).lower()

def _parse_num(text):
    t = _clean(text)
    if not t or t in ('-', '—'): return None
    if _BRKT_RE.match(t): return -float(t[1:-1].replace(',', ''))
    if re.match(r'^-?[\d,]+(?:\.\d+)?$', t): return float(t.replace(',', ''))
    m = re.search(r'\([\d,]+\)|[\d,]+', t)
    if m:
        raw = m.group()
        return -float(raw[1:-1].replace(',', '')) if raw.startswith('(') else float(raw.replace(',', ''))
    return None

def _split(cell): return [s.strip() for s in str(cell).split('\n') if s.strip()]

def _period_from_header(text):
    m = re.search(r'As of\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})', _clean(text), re.I)
    if not m: return None
    months = {'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
              'july':7,'august':8,'september':9,'october':10,'november':11,'december':12}
    mn = months.get(m.group(1).lower(), 0)
    q  = config.MONTH_TO_QUARTER.get(mn, 0)
    return f'{m.group(3)}-Q{q}' if q else None

def _join_continued(items):
    out = []
    for item in items:
        if out and item:
            if item[0].islower():
                sep = '' if out[-1].endswith('-') else ' '
                out[-1] = out[-1] + sep + item
            elif item.split()[0].lower() in _CONT_WORDS:
                out[-1] = out[-1] + ' ' + item
            else:
                out.append(item)
        else:
            out.append(item)
    return out

def _seq_match(pdf_labels, label_map):
    matches = []
    map_ptr = 0
    for pi, lbl in enumerate(pdf_labels):
        for mp in range(map_ptr, len(label_map)):
            if _norm(label_map[mp][0]) == _norm(lbl):
                matches.append((mp, pi))
                map_ptr = mp + 1
                break
    return matches

def _find_page(doc, *keywords, start=0):
    for pg in range(start, len(doc)):
        txt = doc[pg].get_text('text').lower()
        if all(k.lower() in txt for k in keywords):
            return pg
    return None

# ── extract UNONCONSBS ─────────────────────────────────────────────────────────
def extract_unonconsbs(pdf_path, doc):
    pg = _find_page(doc, 'Monetary claims bought', 'Policy loans', 'Agency accounts receivable')
    if pg is None:
        print('UNONCONSBS page not found'); return {}
    print(f'Found UNONCONSBS on p{pg+1}')

    tbls = camelot.read_pdf(str(pdf_path), pages=str(pg+1), flavor='lattice', suppress_stdout=True)
    if not tbls:
        print('No tables'); return {}
    df = tbls[0].df

    # detect period columns
    val_cols = []
    for ci in range(df.shape[1]):
        p = _period_from_header(' '.join(_split(str(df.iloc[0, ci]))))
        if p and p not in [x[1] for x in val_cols]:
            val_cols.append((ci, p))

    periods = [p for _, p in val_cols[:2]]
    print(f'Periods: {periods}')

    # find main data row
    data_row = None
    for ri in range(len(df)):
        items = _split(str(df.iloc[ri, 0]))
        if any('ASSETS' in i.upper() for i in items[:2]) and len(items) > 10:
            data_row = ri; break
    if data_row is None:
        print('Data row not found'); return {}

    raw_labels = _join_continued(_split(str(df.iloc[data_row, 0])))
    if raw_labels and re.match(r'ASSETS', raw_labels[0], re.I):
        raw_labels = raw_labels[1:]

    val_by_period = {}
    for ci, p in val_cols[:2]:
        val_by_period[p] = [_parse_num(v) for v in _split(str(df.iloc[data_row, ci]))]

    total_by_period = {}
    for ri in range(len(df)):
        if _norm(str(df.iloc[ri, 0])) == 'total assets':
            for ci, p in val_cols[:2]:
                total_by_period[p] = _parse_num(str(df.iloc[ri, ci]))
            break

    # build result — keyed by COLUMN_CODE
    result = {p: {} for p in periods}
    matches = _seq_match(raw_labels, config.UNONCONSBS_LABELS)
    for map_idx, pdf_idx in matches:
        lbl, code = config.UNONCONSBS_LABELS[map_idx]
        full_code = f'PICQD.UNONCONSBS.{code}'
        for p, vals in val_by_period.items():
            result[p][full_code] = vals[pdf_idx] if pdf_idx < len(vals) else None

    for p, v in total_by_period.items():
        if p in result:
            result[p]['PICQD.UNONCONSBS.ASSET.TOTAL.Q'] = v

    return result

# ── UNONCONSBS column codes & headers ─────────────────────────────────────────
UNONCONS_CODES = [c for c in config.COLUMN_CODES if c.startswith('PICQD.UNONCONSBS.')]
UNONCONS_HDRS  = [config.COLUMN_HEADERS[config.COLUMN_CODES.index(c)] for c in UNONCONS_CODES]

# ── load sample CSV for comparison ────────────────────────────────────────────
def load_sample():
    if not SAMPLE.exists():
        print(f'Sample CSV not found: {SAMPLE}'); return {}
    with open(SAMPLE, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    if len(rows) < 3:
        return {}
    codes = rows[0]
    # build dict: period → {code: value}
    sample = {}
    for row in rows[2:]:
        if not row or not row[0].strip():
            continue
        period = row[0].strip()
        sample[period] = {}
        for ci, code in enumerate(codes):
            if ci < len(row):
                sample[period][code] = row[ci]
    return sample

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    doc = fitz.open(str(ANNUAL))
    extracted = extract_unonconsbs(str(ANNUAL), doc)
    doc.close()

    if not extracted:
        print('No data extracted'); return

    sample = load_sample()

    # ── write output CSV ───────────────────────────────────────────────────────
    OUT_CSV.parent.mkdir(exist_ok=True)
    with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['period'] + UNONCONS_CODES)
        w.writerow([''] + UNONCONS_HDRS)
        for period in sorted(extracted):
            row = [period]
            for code in UNONCONS_CODES:
                row.append(extracted[period].get(code))
            w.writerow(row)
    print(f'\nCSV written to: {OUT_CSV}')

    # ── comparison vs sample ───────────────────────────────────────────────────
    print('\n-- Comparison vs sample CSV --')
    ok = fail = miss = 0
    for period in sorted(extracted):
        if period not in sample:
            print(f'  {period}: not in sample CSV (new period)')
            continue
        print(f'\n  {period}:')
        for code in UNONCONS_CODES:
            ext_val = extracted[period].get(code)
            smp_raw = sample[period].get(code, '')
            smp_val = None if not smp_raw.strip() else float(smp_raw.replace(',', '')) if smp_raw.strip() not in ('-', '') else None
            hdr = code.split('.')[-2]  # short name for display
            if ext_val is None and smp_val is None:
                miss += 1; continue
            if ext_val == smp_val:
                ok += 1
            else:
                fail += 1
                print(f'    MISMATCH {code}: extracted={ext_val}  sample={smp_val}')
    total = ok + fail + miss
    print(f'\n  TOTAL: {ok} match, {fail} mismatch, {miss} both-None  ({total} checked)')

    # ── console table ─────────────────────────────────────────────────────────
    print('\n-- Extracted values --')
    hdr_row = f"{'Code':<50} " + '  '.join(f'{p:>12}' for p in sorted(extracted))
    print(hdr_row)
    print('-' * 80)
    for code in UNONCONS_CODES:
        vals = '  '.join(f'{str(extracted[p].get(code)):>12}' for p in sorted(extracted))
        label = code.replace('PICQD.UNONCONSBS.', '')
        print(f'{label:<50} {vals}')

if __name__ == '__main__':
    main()
