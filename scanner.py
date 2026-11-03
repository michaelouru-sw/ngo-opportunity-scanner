#!/usr/bin/env python3
"""
eLearning / EdTech / Instructional Design Opportunity Scanner
================================================================

Scans fixed global sources (ReliefWeb, UNjobs, Devex, Idealist,
DevelopmentAid) plus a rotating set of open-web searches, classifies each
match (Job / Consultancy / Grant), scores it for relevance (remote or
Kenya/Africa first, consultancies and grants above jobs), drops anything
past its deadline, and reports only what is genuinely new.

What makes a run fast and accurate:
  * sources are fetched in parallel, with one pooled session and retries
  * dedup happens BEFORE deadline checks, so the page-fetch budget is spent
    only on new items (previously it was burned re-checking old ones)
  * reposts are caught by title as well as URL (UNjobs re-issues new IDs)
  * deadlines come from structured data and explicit date patterns, not
    fuzzy parsing (which could silently invent "today")
  * every source is health-tracked; silent failures are reported in the digest

USAGE
-----
    python3 scanner.py

All settings live in config.py.
"""

import csv
import json
import logging
import os
import re
import smtplib
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
import sources as srcmod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scanner")

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_CSV_PATH = SCRIPT_DIR / config.LOG_CSV_PATH
SEEN_STORE_PATH = SCRIPT_DIR / config.SEEN_STORE_PATH

CSV_FIELDS_V2 = [
    "found_at", "category", "score", "source", "title", "url",
    "location", "deadline", "extra",
]
CSV_FIELDS = CSV_FIELDS_V2 + ["apc"]
CATEGORY_ORDER = ["Consultancy", "Academic", "Grant", "Job", "Uncertain"]
CATEGORY_HEADINGS = {
    "Consultancy": "CONSULTANCIES (individual or firm)",
    "Academic": "ACADEMIC: calls for papers, chapters, journals",
    "Grant": "GRANTS",
    "Job": "JOBS",
    "Uncertain": "UNCERTAIN CATEGORY (please check manually)",
}
TITLE_DEDUPE_DAYS = getattr(config, "TITLE_DEDUPE_DAYS", 45)
DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
HEALTH_ALERT_AFTER_RUNS = 3  # alert once at 3 empty runs, then weekly

DEADLINE_TRIGGERS = [
    "application deadline", "submission deadline", "closing date", "deadline",
    "apply by", "due date", "closes on", "applications close", "closing on",
    "last date", "applications due", "apply before",
    "abstract submission deadline", "abstract deadline", "abstracts due",
    "paper submission deadline", "manuscript due", "manuscripts due", "submissions due",
    "submissions close", "deadline for submission", "submit by",
]
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?[\s\-/.]+{_MONTH}[\s\-/.,]+\d{{2,4}}\b", re.I),
    re.compile(rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.I),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
]


# ---------------------------------------------------------------------------
# Compiled matchers (whole-word, so "lms" no longer matches inside "palms")
# ---------------------------------------------------------------------------

def _compile(terms):
    parts = sorted({re.escape(t.strip().lower()) for t in terms if t.strip()}, key=len, reverse=True)
    if not parts:
        return None
    # optional trailing "s" so "consultant" also matches "consultants"
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")s?(?![a-z0-9])")


KEYWORD_RE = _compile(config.KEYWORDS)
EXCLUDE_RE = _compile(config.EXCLUDE_KEYWORDS)
REMOTE_RE = _compile(config.REMOTE_SIGNAL_KEYWORDS)
HOME_RE = _compile(config.HOME_REGION_KEYWORDS)
GRANT_RE = _compile(config.GRANT_KEYWORDS)
CONSULT_RE = _compile(config.CONSULTANCY_KEYWORDS)
JOB_RE = _compile(config.JOB_KEYWORDS)
SIGNAL_RE = _compile(
    config.GRANT_KEYWORDS + config.CONSULTANCY_KEYWORDS
    + config.JOB_KEYWORDS + config.OPPORTUNITY_SIGNAL_EXTRA_KEYWORDS
    + config.ACADEMIC_SIGNAL_KEYWORDS + config.GRANT_EXTRA_SIGNALS
)
PREFERRED_RES = [(_compile([t]), pts) for t, pts in config.PREFERRED_TERMS.items()]
ACADEMIC_RE = _compile(config.ACADEMIC_SIGNAL_KEYWORDS)
ACAD_TOPIC_RE = _compile(config.ACADEMIC_TOPICS)
GRANT_TOPIC_RE = _compile(config.GRANT_TOPICS)
GRANT_SIGNAL_RE = _compile(config.GRANT_KEYWORDS + config.GRANT_EXTRA_SIGNALS)

# APC detection (best effort). "No fee" wins over "fee": hybrid pages often
# say "no APC for the subscription route; OA option costs $X".
APC_NO_FEE_RE = re.compile(
    r"\bno(?!\s+(?:later|longer|more|less|earlier))\b[^.]{0,40}?"
    r"\b(?:apcs?|article[- ]processing[- ]charges?|publication[- ]fees?|submission[- ]fees?|"
    r"author[- ]fees?|publication[- ]charges?|fees?)\b"
    r"|free of charge|free to (?:publish|authors?)|diamond open access|zero apc"
    r"|without (?:any )?(?:apcs?|fees|charges)|(?:does|do) not charge|no cost to (?:the )?authors?",
    re.I)
_CUR = r"(?:[$€£]|usd|eur|gbp|chf)\s?\d"
APC_FEE_RE = re.compile(
    r"(?:\bapcs?\b|article[- ]processing[- ]charges?|publication[- ]fees?|open[- ]access[- ]fees?)[^.]{0,80}?" + _CUR
    + r"|" + _CUR + r"[\d,.]*[^.]{0,60}?(?:\bapcs?\b|article[- ]processing[- ]charge|publication[- ]fee)",
    re.I)


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=config.REQUEST_RETRIES, backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=None,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=config.MAX_WORKERS * 2)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = config.USER_AGENT
    return session


SESSION = make_session()


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def topic_matches(text: str) -> bool:
    """Topic only (no signal word needed): for sources that already list
    only calls, e.g. a conference-CFP index."""
    if not text:
        return False
    lowered = text.lower()
    if EXCLUDE_RE and EXCLUDE_RE.search(lowered):
        return False
    return any(rx and rx.search(lowered) for rx in (KEYWORD_RE, ACAD_TOPIC_RE, GRANT_TOPIC_RE))


def matches_keywords(text: str) -> bool:
    """Job keywords, OR academic topic + call signal, OR grant topic + funding signal."""
    if not text:
        return False
    lowered = text.lower()
    if EXCLUDE_RE and EXCLUDE_RE.search(lowered):
        return False
    if KEYWORD_RE and KEYWORD_RE.search(lowered):
        return True
    if ACAD_TOPIC_RE and ACADEMIC_RE and ACAD_TOPIC_RE.search(lowered) and ACADEMIC_RE.search(lowered):
        return True
    return bool(GRANT_TOPIC_RE and GRANT_SIGNAL_RE
                and GRANT_TOPIC_RE.search(lowered) and GRANT_SIGNAL_RE.search(lowered))


def detect_apc(text: str) -> str:
    """'no-fee' (stated free), 'fee' (an APC amount is stated), or 'unknown'."""
    if not text:
        return "unknown"
    if APC_NO_FEE_RE.search(text):
        return "no-fee"
    if APC_FEE_RE.search(text):
        return "fee"
    return "unknown"


def has_remote_signal(text: str) -> bool:
    return bool(text and REMOTE_RE and REMOTE_RE.search(text.lower()))


def has_opportunity_signal(text: str) -> bool:
    return bool(text and SIGNAL_RE and SIGNAL_RE.search(text.lower()))


def is_blocked_url(url: str) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return (any(d in lowered for d in config.DOMAIN_BLOCKLIST)
            or any(p in lowered for p in config.URL_PATTERN_BLOCKLIST))


def classify_opportunity(text: str) -> str:
    if not text:
        return "Uncertain"
    lowered = text.lower()
    if ACADEMIC_RE and ACADEMIC_RE.search(lowered):
        return "Academic"
    if GRANT_SIGNAL_RE and GRANT_SIGNAL_RE.search(lowered):
        return "Grant"
    if CONSULT_RE and CONSULT_RE.search(lowered):
        return "Consultancy"
    if JOB_RE and JOB_RE.search(lowered):
        return "Job"
    return "Uncertain"


def classify_location(text: str) -> str:
    """Remote / Kenya-Africa / Elsewhere (unconfirmed). Evidence-based: only
    what the title/snippet says, instead of labelling every UNjobs result
    'Remote/global' as the old version did."""
    lowered = (text or "").lower()
    if REMOTE_RE and REMOTE_RE.search(lowered):
        return "Remote"
    if HOME_RE and HOME_RE.search(lowered):
        return "Kenya/Africa"
    return "Elsewhere/unconfirmed"


def score_item(item: dict) -> int:
    text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
    s = config.CATEGORY_SCORE.get(item.get("category", "Uncertain"), 0)
    loc = item.get("location", "")
    if item.get("category") in config.LOCATION_NEUTRAL_CATEGORIES:
        pass
    elif loc == "Remote":
        s += config.REMOTE_SCORE
    elif loc == "Kenya/Africa":
        s += config.HOME_REGION_SCORE
    else:
        s += config.ONSITE_ELSEWHERE_PENALTY
    if item.get("apc") == "no-fee":
        s += config.APC_NO_FEE_SCORE
    for rx, pts in PREFERRED_RES:
        if rx and rx.search(text):
            s += pts
    return s


def _host(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def url_key(url: str) -> str:
    return "u:" + url


def title_key(title: str, url: str) -> str:
    toks = re.findall(r"[a-z0-9]+", title.lower())
    return f"t:{_host(url)}|{' '.join(toks[:5])}"


def unwrap_ddg(href: str) -> str:
    """DuckDuckGo sometimes wraps result links in a redirect (…?uddg=<url>)."""
    if href and "uddg=" in href:
        target = parse_qs(urlparse(href).query).get("uddg")
        if target:
            return unquote(target[0])
    return href


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

def _today_utc():
    return datetime.now(timezone.utc).date()


def _parse_date(raw: str):
    try:
        if re.match(r"\d{4}-\d{2}-\d{2}", raw):  # ISO: dayfirst would swap month/day
            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        else:
            d = dateparser.parse(raw, dayfirst=True, fuzzy=False).date()
    except (ValueError, OverflowError, TypeError):
        return None
    today = _today_utc()
    # sanity window: ignores junk like "5/5/2005" or far-future placeholders
    if today - timedelta(days=365) <= d <= today + timedelta(days=730):
        return d
    return None


def find_deadline_in_text(text: str):
    """First explicit date appearing shortly after a deadline phrase."""
    if not text:
        return None
    lowered = text.lower()
    hits = []
    for trig in DEADLINE_TRIGGERS:
        for m in re.finditer(re.escape(trig), lowered):
            hits.append(m.start() + len(trig))
    for end in sorted(hits):
        window = text[end: end + 80]
        for pat in DATE_PATTERNS:
            m = pat.search(window)
            if m:
                d = _parse_date(m.group(0))
                if d:
                    return d
    return None


def find_deadline_structured(soup: BeautifulSoup):
    """schema.org JobPosting.validThrough in JSON-LD, when a site provides it."""
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        for m in re.finditer(r'"validThrough"\s*:\s*"([^"]+)"', raw or ""):
            d = _parse_date(m.group(1))
            if d:
                return d
    return None


CLOSED_RE = re.compile(
    r"\b(?:this\s+(?:job|vacancy|position|call|opportunity|tender|notice|posting)\s+(?:has\s+)?(?:closed|expired|ended)"
    r"|no\s+longer\s+(?:accepting|available|open)|(?:position|vacancy)\s+(?:has\s+been\s+)?filled"
    r"|(?:applications?|submissions?|call)\s+(?:are\s+|is\s+|has\s+|have\s+)?(?:now\s+)?closed"
    r"|(?:vacancy|job|posting|listing)\s+(?:has\s+)?expired|contract\s+award|award\s+notice|notice\s+of\s+award)\b", re.I)
POSTED_TRIGGERS = ["date posted", "posted on", "posted:", "published on", "published:", "date published",
                   "posting date", "publication date", "date of publication"]
MAX_POST_AGE_DAYS = getattr(config, "MAX_POST_AGE_DAYS", 75)


def _parse_past_date(raw: str):
    try:
        if re.match(r"\d{4}-\d{2}-\d{2}", raw):
            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        else:
            d = dateparser.parse(raw, dayfirst=True, fuzzy=False).date()
    except (ValueError, OverflowError, TypeError):
        return None
    today = _today_utc()
    return d if today - timedelta(days=1500) <= d <= today else None


def find_posted_date(soup: BeautifulSoup, text: str):
    """When the posting was published: JSON-LD datePosted, else a date after 'posted on' etc."""
    for tag in soup.find_all("script", type="application/ld+json"):
        m = re.search(r'"datePosted"\s*:\s*"([^"]+)"', tag.string or tag.get_text() or "")
        if m:
            d = _parse_past_date(m.group(1))
            if d:
                return d
    lowered = text.lower()
    for trig in POSTED_TRIGGERS:
        i = lowered.find(trig)
        if i >= 0:
            window = text[i + len(trig): i + len(trig) + 60]
            for pat in DATE_PATTERNS:
                m = pat.search(window)
                if m:
                    d = _parse_past_date(m.group(0))
                    if d:
                        return d
    return None


def looks_stale(*texts: str) -> bool:
    """Only past years mentioned (e.g. 'Call for papers 2023') and no current/future year."""
    years = [int(y) for t in texts for y in re.findall(r"\b(20\d\d)\b", t or "")]
    return bool(years) and max(years) < _today_utc().year


def fetch_detail(url: str, want_apc: bool = False):
    """One page request -> (deadline|None, apc, state). state: 'ok', 'closed'
    (page says closed/awarded), 'stale' (old posting date or only past years
    mentioned, and no deadline) or 'error' (could not fetch)."""
    try:
        resp = SESSION.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.debug("detail fetch failed %s: %s", url, exc)
        return None, ("unknown" if want_apc else ""), "error"
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    deadline = find_deadline_structured(soup) or find_deadline_in_text(text)
    apc = detect_apc(text[:300000]) if want_apc else ""
    state = "ok"
    if CLOSED_RE.search(text[:6000]):
        state = "closed"
    elif not deadline:
        posted = find_posted_date(soup, text[:200000])
        if posted and (_today_utc() - posted).days > MAX_POST_AGE_DAYS:
            state = "stale"
        elif not posted and looks_stale(text[:2500]):
            state = "stale"
    return deadline, apc, state


# ---------------------------------------------------------------------------
# Source fetchers — each returns (items, health)
# health = {"raw": links/records examined, "kept": matches, "error": str|None}
# ---------------------------------------------------------------------------

def _item(source, title, url, snippet="", extra="", deadline=None):
    text = f"{title} {snippet}"
    return {
        "source": source, "title": title, "url": url,
        "category": classify_opportunity(text),
        "location": classify_location(text),
        "snippet": snippet, "extra": extra, "deadline": deadline, "apc": "",
    }


def fetch_reliefweb(source_cfg: dict):
    health = {"raw": 0, "kept": 0, "error": None}
    appname = os.environ.get(source_cfg.get("appname_env", ""), "") or source_cfg.get("appname", "")
    if not appname:
        health["error"] = (f"config: {source_cfg.get('appname_env', 'appname')} not set "
                           "(ReliefWeb needs a registered appname)")
        return [], health

    query_string = " OR ".join(f'"{kw}"' for kw in config.KEYWORDS)
    params = {
        "appname": appname,
        "query[value]": query_string,
        "query[operator]": "OR",
        "sort[]": "date.created:desc",
        "limit": 100,
        "fields[include][]": [
            "title", "url", "url_alias", "date.created", "date.closing",
            "source.name", "country.name",
        ],
    }
    try:
        resp = SESSION.get(source_cfg["url"], params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        health["error"] = str(exc)[:160]
        log.error("[%s] request failed: %s", source_cfg["name"], exc)
        return [], health

    today = _today_utc()
    items = []
    for rec in data.get("data", []):
        health["raw"] += 1
        fields = rec.get("fields", {})
        title = fields.get("title", "")
        if not matches_keywords(title):
            continue
        closing = None
        closing_raw = (fields.get("date") or {}).get("closing")
        if closing_raw:
            try:
                closing = dateparser.parse(closing_raw).date()
            except (ValueError, OverflowError):
                closing = None
        if closing and closing < today:
            continue
        url = fields.get("url") or fields.get("url_alias") or rec.get("href", "")
        org = (fields.get("source") or [{}])[0].get("name", "")
        country = (fields.get("country") or [{}])[0].get("name", "")
        loc_text = f"{title} {country}"
        extra = " — ".join(b for b in (org, country) if b)
        items.append(_item(source_cfg["name"], title, url, snippet=country, extra=extra, deadline=closing))
        items[-1]["location"] = classify_location(loc_text)
    health["kept"] = len(items)
    return items, health


def fetch_generic_html(source_cfg: dict):
    health = {"raw": 0, "kept": 0, "error": None}
    try:
        resp = SESSION.get(source_cfg["url"], timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        health["error"] = str(exc)[:160]
        log.error("[%s] request failed: %s", source_cfg["name"], exc)
        return [], health

    soup = BeautifulSoup(resp.text, "html.parser")
    base = "/".join(source_cfg["url"].split("/")[:3])
    seen_on_page, items = set(), []
    for tag in soup.select(source_cfg.get("css_selector", "a")):
        text, href = tag.get_text(strip=True), tag.get("href")
        if not text or not href:
            continue
        health["raw"] += 1
        if not matches_keywords(text):
            continue
        if href.startswith("/"):
            href = base + href
        if not href.startswith("http") or is_blocked_url(href):
            continue
        if (text, href) in seen_on_page:
            continue
        seen_on_page.add((text, href))
        items.append(_item(source_cfg["name"], text, href))
    health["kept"] = len(items)
    if health["raw"] < 5 and not items:
        health["error"] = "page returned almost no links (JavaScript-rendered, blocked, or changed)"
    return items, health


def _brave_key() -> str:
    return os.environ.get(getattr(config, "BRAVE_API_KEY_ENV", "BRAVE_API_KEY"), "")


def _web_queries():
    """Topic x modifier queries plus site-targeted queries aimed at the ATS
    platforms organisations publish on. Rotates across days so the per-run
    cap never permanently starves later queries."""
    combos = [(f"{t} {m}", m) for t in config.WEB_SEARCH_TOPICS
              for m in config.WEB_SEARCH_CATEGORY_MODIFIERS]
    for site in getattr(config, "WEB_SEARCH_SITE_FILTERS", []):
        for term in getattr(config, "WEB_SEARCH_SITE_TERMS", []):
            combos.append((f'{site} "{term}"', "ATS site"))
    year = str(_today_utc().year)
    for q in getattr(config, "ACADEMIC_WEB_QUERIES", []):
        combos.append((q.format(year=year), "Academic"))
    for q in getattr(config, "TENDER_WEB_QUERIES", []):
        combos.append((q.format(year=year), "Tender"))
    for q in getattr(config, "GRANT_WEB_QUERIES", []):
        combos.append((q.format(year=year), "Grant (relevant)"))
    if not combos:
        return []
    cap = config.MAX_WEB_SEARCH_QUERIES_PER_RUN
    if _brave_key():
        cap = getattr(config, "BRAVE_MAX_QUERIES_PER_RUN", 30)
    cap = min(cap, len(combos))
    start = (_today_utc().toordinal() * cap) % len(combos)
    return [combos[(start + i) % len(combos)] for i in range(cap)]


def _search_ddg(query: str):
    resp = SESSION.post(DDG_HTML_ENDPOINT, data={"q": query}, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for link in soup.select("a.result__a")[: config.MAX_RESULTS_PER_WEB_SEARCH_QUERY]:
        parent = link.find_parent()
        snip = parent.find_next_sibling("a", class_="result__snippet") if parent else None
        out.append((link.get_text(strip=True), unwrap_ddg(link.get("href") or ""),
                    snip.get_text(strip=True) if snip else ""))
    return out


def _search_brave(query: str, key: str):
    resp = SESSION.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": 20, "freshness": "pm"},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=config.REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return [
        (r.get("title", ""), r.get("url", ""),
         BeautifulSoup(r.get("description", ""), "html.parser").get_text(" ", strip=True))
        for r in resp.json().get("web", {}).get("results", [])
    ]


def fetch_web_search(source_cfg: dict):
    health = {"raw": 0, "kept": 0, "error": None, "boards": []}
    if not config.WEB_SEARCH_ENABLED:
        return [], health

    key = _brave_key()
    engine = "Brave" if key else "DuckDuckGo"
    items, seen_urls, failures = [], set(), 0
    queries = _web_queries()
    log.info("  web search via %s, %d queries", engine, len(queries))
    for query, modifier in queries:
        try:
            results = _search_brave(query, key) if key else _search_ddg(query)
        except requests.RequestException as exc:
            failures += 1
            log.warning("  web search failed for '%s': %s", query, exc)
            time.sleep(1)
            continue

        for title, href, snippet in results:
            health["raw"] += 1
            if not title or not href.startswith("http") or is_blocked_url(href):
                continue
            health["boards"] += list(srcmod.discover_boards(href))
            if not matches_keywords(title) or href in seen_urls:
                continue
            seen_urls.add(href)
            if config.REQUIRE_OPPORTUNITY_SIGNAL_FOR_WEB_SEARCH and not has_opportunity_signal(f"{title} {snippet}"):
                continue
            items.append(_item(f"Web search ({modifier})", title, href, snippet=snippet))
        time.sleep(1.1)  # stay under free-tier rate limits (Brave: 1 req/s)

    health["kept"] = len(items)
    health["boards"] = sorted(set(health["boards"]))
    if queries and failures == len(queries):
        health["error"] = f"all {engine} requests failed (rate-limited, blocked, or bad API key)"
    return items, health


FETCHERS = {
    "reliefweb_api": fetch_reliefweb,
    "generic_html": fetch_generic_html,
    "web_search": fetch_web_search,
}
FETCHERS.update(srcmod.build_fetchers(sys.modules[__name__]))


# ---------------------------------------------------------------------------
# State: seen-store (urls + titles with dates) and per-source health
# ---------------------------------------------------------------------------

def ensure_csv_schema() -> int:
    """Self-heal the log: older versions wrote rows with 5 or 6 columns under
    a 5-column header, which broke GitHub's CSV viewer and Excel filters.
    Rewrites any non-conforming file to CSV_FIELDS. Returns rows migrated."""
    if not LOG_CSV_PATH.exists():
        return 0
    with open(LOG_CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] == CSV_FIELDS:
        return 0

    if rows[0] == CSV_FIELDS_V2:  # previous schema: just add the apc column
        out = [dict(zip(CSV_FIELDS_V2, r), apc="") for r in rows[1:] if len(r) == len(CSV_FIELDS_V2)]
        tmp = LOG_CSV_PATH.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(out)
        tmp.replace(LOG_CSV_PATH)
        log.info("Added apc column to %d log row(s).", len(out))
        return len(out)

    out = []
    for r in rows[1:]:
        if not r:
            continue
        if len(r) == 5:
            found, source, title, url, extra = r
            category = classify_opportunity(title)
        elif len(r) == 6:
            found, category, source, title, url, extra = r
        else:
            log.warning("Skipping malformed log row with %d fields: %r", len(r), r[:3])
            continue
        m = re.search(r"deadline (\d{4}-\d{2}-\d{2})", extra)
        deadline = m.group(1) if m else ""
        extra = re.sub(r"^Remote/global( — )?", "", extra).replace("deadline not found, verify manually", "").strip(" —")
        item = {"title": title, "category": category, "location": classify_location(title)}
        out.append({
            "found_at": found, "category": category, "score": score_item(item),
            "source": source, "title": title, "url": url,
            "location": item["location"], "deadline": deadline, "extra": extra, "apc": "",
        })

    tmp = LOG_CSV_PATH.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(out)
    tmp.replace(LOG_CSV_PATH)
    log.info("Migrated %d log row(s) to the current CSV schema.", len(out))
    return len(out)


def _csv_rows():
    if not LOG_CSV_PATH.exists():
        return []
    with open(LOG_CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_state() -> dict:
    today = _today_utc().isoformat()
    state = {"version": 2, "seen": {}, "health": {}, "boards": {}}
    if SEEN_STORE_PATH.exists():
        try:
            with open(SEEN_STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read seen-store (%s), starting fresh.", exc)
            data = None
        if isinstance(data, list):  # v1 format: bare list of URLs
            state["seen"] = {url_key(u): today for u in data}
            for row in _csv_rows():  # seed title keys so old reposts stay quiet
                if row.get("title") and row.get("url"):
                    state["seen"][title_key(row["title"], row["url"])] = (row.get("found_at") or today)[:10]
        elif isinstance(data, dict):
            state["seen"] = data.get("seen", {})
            state["health"] = data.get("health", {})
            state["boards"] = data.get("boards", {})
    # prune
    now = _today_utc()
    keep = {}
    for k, d in state["seen"].items():
        try:
            age = (now - datetime.strptime(d[:10], "%Y-%m-%d").date()).days
        except ValueError:
            age = 0
        limit = TITLE_DEDUPE_DAYS if k.startswith("t:") else config.SEEN_RETENTION_DAYS
        if age <= limit:
            keep[k] = d
    state["seen"] = keep
    return state


def save_state(state: dict) -> None:
    with open(SEEN_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def deadline_verdict(it: dict, today) -> str:
    """'ok' or why the item must not be reported. Hard rule: a deadline
    before the run date is never reported. Tenders/grants/CFPs/consultancies
    must have a verified future deadline unless the source is rolling."""
    d = it.get("deadline")
    if d:
        return "expired" if d < today else "ok"
    if it.get("_detail") in ("closed", "stale"):
        return it["_detail"]
    if looks_stale(it.get("title", ""), it.get("snippet", "")):
        return "stale"
    if it.get("category") in getattr(config, "REQUIRE_DEADLINE_CATEGORIES", []) and not it.get("rolling"):
        return "no_deadline"
    return "ok"


def prune_log() -> int:
    """Move rows that are expired or junk out of the live log into the archive,
    so the dashboard and log never show a deadline before today."""
    if not LOG_CSV_PATH.exists():
        return 0
    today = _today_utc()
    keep, gone = [], []
    with open(LOG_CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        dl = (r.get("deadline") or "").strip()
        try:
            d = datetime.strptime(dl, "%Y-%m-%d").date() if dl else None
        except ValueError:
            d = None
        junk = (" " in (r.get("url") or "").strip()
                or re.search(r"award|cancel", r.get("extra") or "", re.I)
                or (r.get("url") or "").rstrip("/").endswith("brightermonday.co.ke/jobs"))
        try:
            age = (today - datetime.strptime((r.get("found_at") or "")[:10], "%Y-%m-%d").date()).days
        except ValueError:
            age = 0
        stale = not d and (looks_stale(r.get("title", ""))
                           or age > MAX_POST_AGE_DAYS
                           or (r.get("category") in getattr(config, "REQUIRE_DEADLINE_CATEGORIES", [])
                               and "DOAJ" not in (r.get("extra") or "")
                               and "linkedin.com" not in (r.get("url") or "")))
        (gone if (d and d < today) or junk or stale else keep).append(r)
    if gone:
        arch = LOG_CSV_PATH.with_name("opportunities_archive.csv")
        new = not arch.exists()
        with open(arch, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(gone)
        with open(LOG_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(keep)
    return len(gone)


def append_to_log(items: list) -> None:
    exists = LOG_CSV_PATH.exists()
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        for it in items:
            w.writerow({**it, "deadline": it["deadline"].isoformat() if it.get("deadline") else ""})


def update_health(state: dict, name: str, health: dict, expect_matches: bool = True) -> "str | None":
    """Tracks consecutive failing runs; returns an alert string when due.
    A run fails on an error, or (for sources that should always list
    something) on zero matches. Org careers pages and ATS boards often have
    no relevant opening, so for them only errors count."""
    rec = state["health"].setdefault(name, {"zero_runs": 0, "last_ok": None})
    if health["error"] or (expect_matches and health["kept"] == 0):
        rec["zero_runs"] += 1
    else:
        rec["zero_runs"], rec["last_ok"] = 0, _today_utc().isoformat()
    n = rec["zero_runs"]
    if n >= HEALTH_ALERT_AFTER_RUNS and (n - HEALTH_ALERT_AFTER_RUNS) % 7 == 0:
        why = health["error"] or "no matching results"
        return f"{name}: failing for {n} runs in a row ({why}). Last worked: {rec['last_ok'] or 'never'}."
    return None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _line_detail(it: dict) -> str:
    bits = [] if it.get("category") == "Academic" else [it["location"]]  # location is moot for papers
    bits.append(f"deadline {it['deadline'].isoformat()}" if it.get("deadline") else "deadline unknown")
    if it.get("category") == "Academic":
        bits.append({"no-fee": "no APC (stated)", "fee": "APC charged"}.get(it.get("apc"), "APC unknown, verify"))
    if it.get("extra"):
        bits.append(it["extra"])
    return " | ".join(bits)


def build_digest(items: list, alerts: list) -> str:
    lines = [f"{len(items)} new opportunity(ies), best matches first.\n"]
    top = [i for i in items if i["score"] >= config.TOP_PICK_MIN_SCORE]
    if top:
        lines.append(f"=== TOP PICKS ({len(top)}) ===\n")
        for it in top:
            lines += [f"- [{it['category']}, score {it['score']}] {it['title']}",
                      f"  {it['url']}", f"  {_line_detail(it)}", ""]
    for cat in CATEGORY_ORDER:
        group = [i for i in items if i["category"] == cat and i not in top]
        if not group:
            continue
        lines.append(f"=== {CATEGORY_HEADINGS[cat]} ({len(group)}) ===\n")
        for it in group:
            lines += [f"- [score {it['score']}] {it['title']}", f"  {it['url']}",
                      f"  {_line_detail(it)}", ""]
    if alerts:
        lines.append("=== SOURCE HEALTH — needs attention ===")
        lines += [f"- {a}" for a in alerts]
    return "\n".join(lines)


def send_email_digest(items: list, alerts: list) -> None:
    if not config.EMAIL_ENABLED or not (items or alerts):
        return
    password = os.environ.get(config.SMTP_PASSWORD_ENV_VAR)
    if not password:
        log.warning("EMAIL_ENABLED but %s is not set — skipping email.", config.SMTP_PASSWORD_ENV_VAR)
        return
    top = sum(1 for i in items if i["score"] >= config.TOP_PICK_MIN_SCORE)
    subject = config.EMAIL_SUBJECT
    if items:
        subject += f" ({len(items)} new, {top} top picks)"
    elif alerts:
        subject = "Opportunity scanner: source health alert"
    limit = getattr(config, "MAX_ITEMS_IN_EMAIL", 40)
    body = build_digest(items[:limit], alerts)
    if len(items) > limit:
        body += f"\n\n+ {len(items) - limit} more lower-scored item(s): see the dashboard."
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, config.EMAIL_FROM, config.EMAIL_TO
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.REQUEST_TIMEOUT_SECONDS) as s:
            s.starttls()
            s.login(config.SMTP_USERNAME, password)
            s.sendmail(config.EMAIL_FROM, [config.EMAIL_TO], msg.as_string())
        log.info("Email digest sent to %s.", config.EMAIL_TO)
    except (smtplib.SMTPException, OSError) as exc:
        log.error("Failed to send email digest: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ensure_csv_schema()
    pruned = prune_log()
    if pruned:
        log.info("Archived %d expired/closed row(s) from the live log", pruned)
    state = load_state()
    seen = state["seen"]
    now_s = _today_utc().isoformat()

    sources = list(config.SOURCES)
    sources += [srcmod.board_to_source(k) for k in sorted(state["boards"])]
    if config.WEB_SEARCH_ENABLED:
        sources.append({"name": "Open web search", "type": "web_search"})
    log.info("Starting scan across %d source(s)...", len(sources))

    def run(src):
        fetcher = FETCHERS.get(src["type"])
        if fetcher is None:
            return src, [], {"raw": 0, "kept": 0, "error": f"unknown type {src['type']}"}
        try:
            items, health = fetcher(src)
        except Exception as exc:  # one broken source must never sink the run
            log.exception("[%s] fetcher crashed", src["name"])
            return src, [], {"raw": 0, "kept": 0, "error": f"crashed: {exc}"[:160], "boards": []}
        return src, items, health

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(config.MAX_WORKERS, len(sources))) as pool:
        outcomes = list(pool.map(run, sources))

    alerts, candidates = [], []
    for src, items, health in outcomes:
        log.info("%-42s raw=%-4d kept=%-3d %s", src["name"], health["raw"], health["kept"],
                 f"ERROR: {health['error']}" if health["error"] else "")
        expect = src.get("expect_matches", src["type"] in ("generic_html", "web_search", "reliefweb_api"))
        alert = update_health(state, src["name"], health, expect)
        if src.get("quiet"):  # auto-discovered boards: never alert, drop after 5 failures
            alert = None
            if state["health"][src["name"]]["zero_runs"] >= 5:
                state["boards"].pop(src.get("_key"), None)
        if alert:
            alerts.append(alert)
        candidates.extend(items)
        for key in health.get("boards", []):
            state["boards"].setdefault(key, now_s)

    # boards behind relevant postings are worth scanning in full from now on
    for it in candidates:
        for key in srcmod.discover_boards(it["url"]):
            state["boards"].setdefault(key, now_s)
    cap = getattr(config, "MAX_DYNAMIC_BOARDS", 60)
    if len(state["boards"]) > cap:
        newest = sorted(state["boards"].items(), key=lambda kv: kv[1], reverse=True)[:cap]
        state["boards"] = dict(newest)

    # 1) dedupe FIRST (url + repost-by-title), 2) only then spend page fetches
    new, batch_keys = [], set()
    for it in candidates:
        keys = {url_key(it["url"])}
        if not it.get("no_title_dedupe"):
            keys.add(title_key(it["title"], it["url"]))
        if keys & seen.keys() or keys & batch_keys:
            continue
        batch_keys |= keys
        it["_keys"] = keys
        new.append(it)

    def wants_apc(i):
        return i["category"] == "Academic" and not i.get("apc")

    need_detail = [i for i in new if (not i.get("deadline") or wants_apc(i)) and "linkedin.com" not in i["url"]] \
        if config.CHECK_DEADLINES_ON_DETAIL_PAGES else []  # LinkedIn pages sit behind a login wall
    need_detail = need_detail[: config.MAX_DETAIL_PAGE_CHECKS_PER_RUN]
    if need_detail:
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
            results = pool.map(lambda i: fetch_detail(i["url"], wants_apc(i)), need_detail)
            for it, (d, apc, state_) in zip(need_detail, results):
                it["_detail"] = state_
                if d and not it.get("deadline"):
                    it["deadline"] = d
                if apc:
                    it["apc"] = apc
    checked = {id(i) for i in need_detail}
    unchecked_needed = [] if not config.CHECK_DEADLINES_ON_DETAIL_PAGES else [i for i in new if (not i.get("deadline") or wants_apc(i))
                        and "linkedin.com" not in i["url"] and id(i) not in checked]
    today, final, expired, apc_dropped, no_dl = _today_utc(), [], 0, 0, 0
    deferred = {id(i) for i in unchecked_needed} | {id(i) for i in new if i.get("_detail") == "error"}
    for it in new:
        keys = it.pop("_keys")
        if id(it) in deferred:
            continue  # not checked yet (budget/network): retry next run instead of losing it
        for k in keys:
            seen[k] = now_s  # decided either way: never re-fetched
        verdict = deadline_verdict(it, today)
        if verdict != "ok":
            if verdict == "expired":
                expired += 1
            else:
                no_dl += 1
            continue
        if it["category"] == "Academic":
            it["apc"] = it.get("apc") or "unknown"
            if it["apc"] == "fee" and config.EXCLUDE_APC_FEES:
                apc_dropped += 1
                continue
        elif config.REQUIRE_REMOTE_SIGNAL and it["location"] != "Remote":
            continue
        it["score"] = score_item(it)
        it["found_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        final.append(it)
    final.sort(key=lambda i: (-i["score"], CATEGORY_ORDER.index(i["category"])))

    log.info("%d candidate(s) -> %d new, %d expired, %d closed/stale/no-deadline, %d dropped for APC fees, %d to report (%.1fs); %d auto-discovered board(s) tracked",
             len(candidates), len(new), expired, no_dl, apc_dropped, len(final), time.time() - t0, len(state["boards"]))

    if final:
        print("\n" + build_digest(final, []))
        append_to_log(final)
    else:
        log.info("No new opportunities since last run.")
    for a in alerts:
        log.warning("SOURCE HEALTH: %s", a)

    send_email_digest(final, alerts)
    save_state(state)
    log.info("Done. Full history in %s", LOG_CSV_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
