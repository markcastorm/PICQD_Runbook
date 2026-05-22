import os
import sys
import re
import json
import time
import random
import logging
import subprocess
from datetime import datetime

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

_FINANCIAL_RESULTS_RE = re.compile(
    r'Financial Results for the (?:Fiscal Year|(?:Three|Six|Nine) Months) Ended',
    re.IGNORECASE,
)

# ── Chrome version detection ───────────────────────────────────────────────────

def _get_chrome_version():
    if sys.platform == 'win32':
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Google\Chrome\BLBeacon',
            )
            ver = winreg.QueryValueEx(key, 'version')[0]
            return int(ver.split('.')[0])
        except Exception:
            pass
    for cmd in ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']:
        try:
            out = subprocess.check_output(
                [cmd, '--version'], stderr=subprocess.DEVNULL
            ).decode()
            return int(out.strip().split()[-1].split('.')[0])
        except Exception:
            continue
    return None


# ── Browser setup ──────────────────────────────────────────────────────────────

def _build_driver(download_dir):
    import undetected_chromedriver as uc
    from selenium_stealth import stealth

    opts = uc.ChromeOptions()
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    if config.HEADLESS_MODE:
        opts.add_argument('--headless=new')

    prefs = {
        'download.default_directory': download_dir,
        'download.prompt_for_download': False,
        'download.directory_upgrade': True,
        'plugins.always_open_pdf_externally': True,
        'safebrowsing.enabled': True,
    }
    opts.add_experimental_option('prefs', prefs)

    ver = _get_chrome_version()
    driver = uc.Chrome(options=opts, version_main=ver)

    stealth(
        driver,
        languages=['en-US', 'en'],
        vendor='Google Inc.',
        platform='Win32',
        webgl_vendor='Intel Inc.',
        renderer='Intel Iris OpenGL Engine',
        fix_hairline=True,
    )
    return driver


def _human_delay(lo=0.8, hi=2.5):
    time.sleep(random.uniform(lo, hi))


def _scroll_to(driver, element):
    driver.execute_script('arguments[0].scrollIntoView({block:"center"});', element)
    _human_delay(0.3, 0.8)


# ── State management ───────────────────────────────────────────────────────────

def _load_state():
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_state(state):
    with open(config.STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


# ── HTML parsing helpers (used for both live Selenium and saved HTML) ──────────

def _parse_release_date(date_str):
    """Parse 'May 15, 2026' → datetime for sorting. Unparseable → datetime.min."""
    for fmt in ('%b %d, %Y', '%B %d, %Y'):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return datetime.min


def _parse_financial_results_links(html):
    """Return list of (title, date_str, relative_url) sorted newest-first by date."""
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    for grp in soup.select('div.rptGrp'):
        h3 = grp.find('h3')
        if not h3:
            continue
        a = h3.find('a')
        if not a:
            continue
        title = a.get_text(strip=True)
        if not _FINANCIAL_RESULTS_RE.search(title):
            continue
        # Skip correction/amendment entries — only want original results
        if title.lower().startswith('(correction)') or 'partial correction' in title.lower():
            continue
        date_p = grp.find('p', class_='date')
        date_str = date_p.get_text(strip=True) if date_p else ''
        href = a.get('href', '')
        results.append((title, date_str, href))
    # Sort by parsed release date descending — guarantees links[0] is the latest
    results.sort(key=lambda r: _parse_release_date(r[1]), reverse=True)
    return results


def _parse_announcement_pdf_url(html):
    """Find 'Announcement of Financial Results' PDF link on detail page."""
    soup = BeautifulSoup(html, 'html.parser')
    for span in soup.select('span.relLnk'):
        a = span.find('a', class_='text-link')
        if not a:
            continue
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if 'Announcement of Financial Results' in text and href.endswith('.pdf'):
            return href
    return None


# ── Main download flow ─────────────────────────────────────────────────────────

def download():
    """
    Navigate to the Japan Post Insurance news index, find the latest (or
    configured-year) Financial Results announcement, download the PDF, and
    return a result dict.

    Returns:
        {
            'pdf_path':      str,    local path to downloaded PDF
            'release_date':  str,    'May 15, 2026' (as on website)
            'release_title': str,    full announcement title
            'pdf_url':       str,    full PDF URL
        }
    Raises:
        RuntimeError on failure.
    """
    state = _load_state()

    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    dl_dir = os.path.join(config.DOWNLOAD_DIR, ts, 'PICQD')
    os.makedirs(dl_dir, exist_ok=True)

    driver = None
    try:
        driver = _build_driver(dl_dir)
        driver.set_page_load_timeout(config.WAIT_TIMEOUT)

        logger.info('Loading news index: %s', config.INDEX_URL)
        driver.get(config.INDEX_URL)
        _human_delay(2, 4)

        html = driver.page_source
        links = _parse_financial_results_links(html)

        if not links:
            raise RuntimeError('No Financial Results links found on index page')

        # Filter by RELEASE_DATE (exact match) or RELEASE_YEAR (title substring)
        if config.RELEASE_DATE is not None:
            target = config.RELEASE_DATE.strip()
            links = [l for l in links if l[1].strip() == target]
            if not links:
                raise RuntimeError(
                    f'No Financial Results found for date {config.RELEASE_DATE}'
                )
        elif config.RELEASE_YEAR is not None:
            year_str = str(config.RELEASE_YEAR)
            links = [l for l in links if year_str in l[0]]
            if not links:
                raise RuntimeError(
                    f'No Financial Results found for year {config.RELEASE_YEAR}'
                )

        title, release_date, rel_url = links[0]
        full_detail_url = config.BASE_URL + rel_url

        logger.info('Found: %s | %s', title, release_date)

        # Check cache
        if not config.BYPASS_CACHE:
            last = state.get('last_pdf_url', '')
            last_title = state.get('last_release_title', '')
            if last_title == title and last:
                logger.info('Already processed: %s — skipping (set BYPASS_CACHE=True to force)', title)
                raise RuntimeError(f'Already processed: {title}')

        # Navigate to detail page
        logger.info('Loading detail page: %s', full_detail_url)
        _scroll_to(driver, driver.find_element('tag name', 'body'))
        _human_delay(0.5, 1.5)
        driver.get(full_detail_url)
        _human_delay(2, 4)

        detail_html = driver.page_source
        pdf_rel_url = _parse_announcement_pdf_url(detail_html)

        if not pdf_rel_url:
            raise RuntimeError(
                f'Announcement of Financial Results PDF link not found on: {full_detail_url}'
            )

        full_pdf_url = config.BASE_URL + pdf_rel_url
        logger.info('PDF URL: %s', full_pdf_url)

        # Download PDF via requests (bypass viewer)
        pdf_path = _download_pdf(full_pdf_url, dl_dir, driver)

        # Persist state
        _save_state({
            'last_release_title': title,
            'last_release_date':  release_date,
            'last_pdf_url':       full_pdf_url,
            'processed_at':       datetime.now().isoformat(),
        })

        return {
            'pdf_path':      pdf_path,
            'release_date':  release_date,
            'release_title': title,
            'pdf_url':       full_pdf_url,
        }

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _download_pdf(url, dest_dir, driver=None):
    """Download a PDF to dest_dir. Uses requests with browser-like headers."""
    filename = url.split('/')[-1]
    dest_path = os.path.join(dest_dir, filename)

    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Referer': config.INDEX_URL,
        'Accept': 'application/pdf,*/*',
    }

    # Forward cookies from the Selenium session if available
    cookies = {}
    if driver:
        try:
            for c in driver.get_cookies():
                cookies[c['name']] = c['value']
        except Exception:
            pass

    logger.info('Downloading PDF → %s', dest_path)
    with requests.get(url, headers=headers, cookies=cookies, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

    size_kb = os.path.getsize(dest_path) // 1024
    logger.info('PDF saved: %s (%d KB)', dest_path, size_kb)
    return dest_path
