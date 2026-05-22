"""
orchestrator.py — PICQD pipeline coordinator.

Flow:
  1. scraper.download()        → {pdf_path, release_date, release_title, pdf_url}
  2. extractor.extract_all()   → (period, data_dict)
  3. file_generator.generate() → writes Excel + ZIP to output/

Returns 0 on success, 1 on failure.
"""
import logging
import re
from datetime import datetime

from datetime import datetime
from pathlib import Path

import config
import scraper
import extractor
import file_generator

logger = logging.getLogger(__name__)

_MONTH_ABBR = {
    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
    'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
    'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
}


def _to_date_str(release_date_text):
    """
    Convert release date string from website to YYYYMMDD.
    Input examples: 'May 15, 2026'  'Feb 13, 2026'  'Aug 08, 2025'
    """
    m = re.match(
        r'(\w+)\s+(\d{1,2}),?\s+(\d{4})',
        release_date_text.strip(),
        re.IGNORECASE,
    )
    if m:
        month_key = m.group(1)[:3].lower()
        mm  = _MONTH_ABBR.get(month_key, '00')
        dd  = m.group(2).zfill(2)
        yy  = m.group(3)
        return f"{yy}{mm}{dd}"
    # Fallback: today
    logger.warning("Could not parse release date '%s', using today", release_date_text)
    return datetime.now().strftime('%Y%m%d')


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    logger.info('=' * 60)
    logger.info('PICQD Pipeline — Starting')
    logger.info('=' * 60)

    # ── Step 1: Scrape and download PDF ───────────────────────────────────────
    logger.info('--- Step 1: Downloading PDF ---')
    try:
        dl = scraper.download()
    except RuntimeError as exc:
        logger.error('Scraper failed: %s', exc)
        return 1
    except Exception as exc:
        logger.error('Scraper unexpected error: %s', exc, exc_info=True)
        return 1

    pdf_path     = dl['pdf_path']
    release_date = dl['release_date']   # e.g. 'May 15, 2026'
    date_str     = _to_date_str(release_date)

    logger.info('PDF: %s', pdf_path)
    logger.info('Release date: %s → %s', release_date, date_str)

    # ── Step 2: Extract all tables ────────────────────────────────────────────
    logger.info('--- Step 2: Extracting data ---')
    run_dir = Path(config.EXTRACTOR_DIR) / datetime.now().strftime('%Y%m%d_%H%M%S')
    try:
        records = extractor.extract_all(pdf_path, run_dir=run_dir)
    except Exception as exc:
        logger.error('Extraction failed: %s', exc, exc_info=True)
        return 1

    period = records[-1][0]  # latest (current) period
    filled = sum(1 for _, d in records for v in d.values() if v is not None)
    logger.info('Extracted %d period(s): %s  filled=%d total',
                len(records), ', '.join(r[0] for r in records), filled)

    # ── Step 3: Generate output files ─────────────────────────────────────────
    logger.info('--- Step 3: Generating output files ---')
    try:
        out_dir = file_generator.generate(records, date_str)
    except Exception as exc:
        logger.error('File generation failed: %s', exc, exc_info=True)
        return 1

    logger.info('=' * 60)
    logger.info('PICQD Pipeline — Complete')
    logger.info('  Periods   : %s', ', '.join(r[0] for r in records))
    logger.info('  Filled    : %d total non-null values', filled)
    logger.info('  Output    : %s', out_dir)
    logger.info('=' * 60)
    return 0
