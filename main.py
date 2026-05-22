"""
main.py — PICQD pipeline entry point.

Full pipeline (scrape + extract + generate):
    python main.py

Manual extraction from local PDF(s) (skip scraper):
    python main.py path/to/file.pdf [path/to/file2.pdf ...]
"""
import sys
import logging

import orchestrator


def _run_manual(pdf_paths):
    """Extract from local PDFs and write output without scraping."""
    from datetime import datetime
    from pathlib import Path
    import config
    import extractor
    import file_generator

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Shared run directory for all PDFs in this invocation
    ts      = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = Path(config.EXTRACTOR_DIR) / ts

    records = []
    for p in pdf_paths:
        path = Path(p)
        if not path.exists():
            print(f"ERROR: file not found: {p}")
            sys.exit(1)
        recs = extractor.extract_all(path, run_dir=run_dir)
        records.extend(recs)
        for period, data in recs:
            filled = sum(1 for v in data.values() if v is not None)
            print(f"  {period}  {filled}/228 filled")

    records.sort(key=lambda r: r[0])
    date_str = datetime.now().strftime('%Y%m%d')
    file_generator.generate(records, date_str)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        # Manual mode: PDF paths supplied on command line
        _run_manual(sys.argv[1:])
    else:
        # Full pipeline: scrape → extract → generate
        sys.exit(orchestrator.main())
