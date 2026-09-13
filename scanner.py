#!/usr/bin/env python3
"""
eLearning / EdTech / Instructional Design Opportunity Scanner
================================================================

Checks GLOBAL fixed sources (ReliefWeb, UNjobs, Devex, Idealist,
DevelopmentAid) PLUS runs open keyword searches across the wider web
(via DuckDuckGo's HTML search — no API key needed) so institutional sites
you haven't hardcoded still get caught. Classifies every match as a Job,
Consultancy, or Grant, filters out anything already past its deadline,
and reports only what you haven't seen before — grouped by category in
both the console output and the email digest.

USAGE
-----
    python3 scanner.py

All settings (keywords, sources, categorization, email) live in
config.py — edit that file, not this one, for day-to-day tweaks.
"""

import csv
import json
import logging
import os
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scanner")

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_CSV_PATH = SCRIPT_DIR / config.LOG_CSV_PATH
SEEN_STORE_PATH = SCRIPT_DIR / config.SEEN_STORE_PATH

DEADLINE_TRIGGERS = [
    "deadline", "closing date", "apply by", "application deadline",
    "due date", "closes on", "applications close", "submission deadline",
    "closing on", "last date", "applications due",
]

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

_detail_page_checks_used = 0
_web_search_queries_used = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def matches_keywords(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if any(bad.lower() in lowered for bad in config.EXCLUDE_KEYWORDS):
        return False
    return any(kw.lower() in lowered for kw in config.KEYWORDS)


def has_remote_signal(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in config.REMOTE_SIGNAL_KEYWORDS)


def classify_opportunity(text: str) -> str:
    """
    Best-effort classification into Grant / Consultancy / Job / Uncertain.
    Grant and consultancy signals are checked before job signals, since
    those are the more distinctive phrasings.
    """
    if not text:
        return "Uncertain"
    lowered = text.lower()
    if any(kw in lowered for kw in config.GRANT_KEYWORDS):
        return "Grant"
    if any(kw in lowered for kw in config.CONSULTANCY_KEYWORDS):
        return "Consultancy"
    if any(kw in lowered for kw in config.JOB_KEYWORDS):
        return "Job"
    return "Uncertain"


def load_seen() -> set:
    if SEEN_STORE_PATH.exists():
        try:
            with open(SEEN_STORE_PATH, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read seen-store (%s), starting fresh.", exc)
    return set()


def save_seen(seen: set) -> None:
    with open(SEEN_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2)


def append_to_log(new_items: list) -> None:
    file_exists = LOG_CSV_PATH.exists()
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["found_at", "category", "source", "title", "url", "extra"]
        )
        if not file_exists:
            writer.writeheader()
        for item in new_items:
            writer.writerow(item)


def _today_utc():
    return datetime.now(timezone.utc).date()


def find_deadline_in_text(text: str):
    if not text:
        return None
    lowered = text.lower()
    for trigger in DEADLINE_TRIGGERS:
        idx = lowered.find(trigger)
        if idx == -1:
            continue
        window = text[idx: idx + len(trigger) + 60]
        try:
            parsed = dateparser.parse(window, fuzzy=True, default=None)
        except (ValueError, OverflowError):
            continue
        if parsed:
            return parsed.date()
    return None


def check_deadline_via_detail_page(url: str):
    global _detail_page_checks_used
    if not config.CHECK_DEADLINES_ON_DETAIL_PAGES:
        return None, None
    if _detail_page_checks_used >= config.MAX_DETAIL_PAGE_CHECKS_PER_RUN:
        return None, None
    _detail_page_checks_used += 1

    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": config.USER_AGENT},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.debug("Could not fetch detail page %s for deadline check: %s", url, exc)
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    deadline = find_deadline_in_text(text)
    if deadline is None:
        return None, None
    return (deadline < _today_utc()), deadline


def _build_result(source_name, title, href, is_global_source, snippet=""):
    """Shared post-processing for generic_html and web_search matches:
    remote labeling, classification, and expiration checking."""
    remote_ok = is_global_source or has_remote_signal(title + " " + snippet)
    if config.REQUIRE_REMOTE_SIGNAL and not remote_ok:
        return None

    category = classify_opportunity(title + " " + snippet)

    is_expired, deadline = check_deadline_via_detail_page(href)
    if is_expired:
        return "EXPIRED"

    extra_bits = ["Remote/global" if remote_ok else "Location not confirmed remote"]
    if deadline:
        extra_bits.append(f"deadline {deadline.isoformat()}")
    elif config.CHECK_DEADLINES_ON_DETAIL_PAGES:
        extra_bits.append("deadline not found, verify manually")

    return {
        "source": source_name,
        "title": title,
        "url": href,
        "category": category,
        "extra": " — ".join(extra_bits),
    }


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------

def fetch_reliefweb(source_cfg: dict) -> list:
    results = []
    query_string = " OR ".join(f'"{kw}"' for kw in config.KEYWORDS)
    params = {
        "appname": source_cfg.get("appname", "opportunity-scanner"),
        "query[value]": query_string,
        "query[operator]": "OR",
        "sort[]": "date.created:desc",
        "limit": 40,
        "fields[include][]": [
            "title", "url", "url_alias", "date.created", "date.closing",
            "source.name", "country.name",
        ],
    }
    try:
        resp = requests.get(
            source_cfg["url"], params=params,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": config.USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.error("[%s] request failed: %s", source_cfg["name"], exc)
        return results

    today = _today_utc()
    skipped_expired = 0

    for item in data.get("data", []):
        fields = item.get("fields", {})
        title = fields.get("title", "")
        if not matches_keywords(title):
            continue

        date_info = fields.get("date", {}) or {}
        closing_raw = date_info.get("closing")
        if closing_raw:
            try:
                closing_date = dateparser.parse(closing_raw).date()
                if closing_date < today:
                    skipped_expired += 1
                    continue
            except (ValueError, OverflowError):
                pass

        url = fields.get("url") or fields.get("url_alias") or item.get("href", "")
        org_list = fields.get("source", [])
        org_name = org_list[0]["name"] if org_list else ""
        country_list = fields.get("country", [])
        country_name = country_list[0]["name"] if country_list else ""

        extra_bits = [b for b in (org_name, country_name) if b]
        results.append({
            "source": source_cfg["name"],
            "title": title,
            "url": url,
            "category": classify_opportunity(title),
            "extra": " — ".join(extra_bits),
        })

    if skipped_expired:
        log.info("  (excluded %d already-closed ReliefWeb listing(s))", skipped_expired)
    return results


def fetch_generic_html(source_cfg: dict) -> list:
    results = []
    try:
        resp = requests.get(
            source_cfg["url"], timeout=config.REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": config.USER_AGENT},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("[%s] request failed: %s", source_cfg["name"], exc)
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    selector = source_cfg.get("css_selector", "a")
    seen_on_page = set()
    is_global_source = source_cfg.get("inherently_global", False)
    skipped_expired = 0

    for tag in soup.select(selector):
        text = tag.get_text(strip=True)
        href = tag.get("href")
        if not text or not href:
            continue
        if not matches_keywords(text):
            continue

        if href.startswith("/"):
            base = "/".join(source_cfg["url"].split("/")[:3])
            href = base + href
        if not href.startswith("http"):
            continue

        key = (text, href)
        if key in seen_on_page:
            continue
        seen_on_page.add(key)

        result = _build_result(source_cfg["name"], text, href, is_global_source)
        if result == "EXPIRED":
            skipped_expired += 1
            continue
        if result:
            results.append(result)

    if skipped_expired:
        log.info("  (excluded %d listing(s) with a passed deadline)", skipped_expired)
    return results


def fetch_web_search(source_cfg: dict) -> list:
    """
    Runs keyword searches across the open web via DuckDuckGo's HTML search
    (no API key required). This is what catches institutional sites
    (foundations, UN agencies, universities) that aren't hardcoded above.
    Queries are capped by MAX_WEB_SEARCH_QUERIES_PER_RUN across the whole
    run, with a short pause between requests to stay polite.
    """
    global _web_search_queries_used
    results = []
    if not config.WEB_SEARCH_ENABLED:
        return results

    seen_urls_this_source = set()

    for topic in config.WEB_SEARCH_TOPICS:
        for modifier in config.WEB_SEARCH_CATEGORY_MODIFIERS:
            if _web_search_queries_used >= config.MAX_WEB_SEARCH_QUERIES_PER_RUN:
                log.info("  (reached MAX_WEB_SEARCH_QUERIES_PER_RUN cap — stopping web search)")
                return results
            _web_search_queries_used += 1

            query = f"{topic} {modifier}"
            log.info("  web-searching: %s", query)
            try:
                resp = requests.post(
                    DDG_HTML_ENDPOINT,
                    data={"q": query},
                    timeout=config.REQUEST_TIMEOUT_SECONDS,
                    headers={"User-Agent": config.USER_AGENT},
                )
                resp.raise_for_status()
            except requests.RequestException as exc:
                log.warning("  web search request failed for '%s': %s", query, exc)
                time.sleep(1)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.select("a.result__a")[: config.MAX_RESULTS_PER_WEB_SEARCH_QUERY]

            for link in links:
                title = link.get_text(strip=True)
                href = link.get("href")
                if not title or not href or not href.startswith("http"):
                    continue
                if not matches_keywords(title):
                    continue
                if href in seen_urls_this_source:
                    continue
                seen_urls_this_source.add(href)

                snippet_tag = link.find_parent().find_next_sibling(
                    "a", class_="result__snippet"
                ) if link.find_parent() else None
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

                result = _build_result(
                    f"Web search ({modifier})", title, href,
                    is_global_source=True, snippet=snippet,
                )
                if result and result != "EXPIRED":
                    results.append(result)

            time.sleep(1)  # be polite to DuckDuckGo between queries

    return results


FETCHERS = {
    "reliefweb_api": fetch_reliefweb,
    "generic_html": fetch_generic_html,
}


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

CATEGORY_ORDER = ["Job", "Consultancy", "Grant", "Uncertain"]
CATEGORY_HEADINGS = {
    "Job": "JOBS",
    "Consultancy": "CONSULTANCIES (individual or firm)",
    "Grant": "GRANTS",
    "Uncertain": "UNCERTAIN CATEGORY (please check manually)",
}


def group_by_category(items: list) -> dict:
    grouped = {cat: [] for cat in CATEGORY_ORDER}
    for item in items:
        grouped.setdefault(item.get("category", "Uncertain"), []).append(item)
    return grouped


def send_email_digest(new_items: list) -> None:
    if not config.EMAIL_ENABLED or not new_items:
        return

    password = os.environ.get(config.SMTP_PASSWORD_ENV_VAR)
    if not password:
        log.warning(
            "EMAIL_ENABLED is True but the %s environment variable is not "
            "set — skipping email. See README.md.",
            config.SMTP_PASSWORD_ENV_VAR,
        )
        return

    grouped = group_by_category(new_items)
    lines = [f"{len(new_items)} new opportunity(ies) found:\n"]
    for category in CATEGORY_ORDER:
        items = grouped.get(category, [])
        if not items:
            continue
        lines.append(f"=== {CATEGORY_HEADINGS[category]} ({len(items)}) ===\n")
        for item in items:
            lines.append(f"- [{item['source']}] {item['title']}")
            lines.append(f"  {item['url']}")
            if item.get("extra"):
                lines.append(f"  ({item['extra']})")
            lines.append("")

    body = "\n".join(lines)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = config.EMAIL_SUBJECT
    msg["From"] = config.EMAIL_FROM
    msg["To"] = config.EMAIL_TO

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.REQUEST_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(config.SMTP_USERNAME, password)
            server.sendmail(config.EMAIL_FROM, [config.EMAIL_TO], msg.as_string())
        log.info("Email digest sent to %s.", config.EMAIL_TO)
    except (smtplib.SMTPException, OSError) as exc:
        log.error("Failed to send email digest: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global _detail_page_checks_used, _web_search_queries_used
    _detail_page_checks_used = 0
    _web_search_queries_used = 0

    sources_to_run = list(config.SOURCES)
    if config.WEB_SEARCH_ENABLED:
        sources_to_run.append({"name": "Open web search", "type": "web_search"})

    log.info("Starting scan across %d source(s)...", len(sources_to_run))
    seen = load_seen()
    all_new = []

    for source_cfg in sources_to_run:
        if source_cfg["type"] == "web_search":
            fetcher = fetch_web_search
        else:
            fetcher = FETCHERS.get(source_cfg["type"])
        if fetcher is None:
            log.warning("Unknown source type '%s' for '%s' — skipping.",
                        source_cfg["type"], source_cfg["name"])
            continue

        log.info("Checking: %s", source_cfg["name"])
        matches = fetcher(source_cfg)
        log.info("  -> %d keyword match(es) kept from this source", len(matches))

        for m in matches:
            uid = m["url"] or f"{m['source']}::{m['title']}"
            if uid in seen:
                continue
            seen.add(uid)
            m["found_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            all_new.append(m)

    if all_new:
        log.info("Found %d NEW opportunity(ies):", len(all_new))
        grouped = group_by_category(all_new)
        for category in CATEGORY_ORDER:
            items = grouped.get(category, [])
            if not items:
                continue
            print(f"\n=== {CATEGORY_HEADINGS[category]} ({len(items)}) ===")
            for item in items:
                print(f"  • [{item['source']}] {item['title']}\n    {item['url']}\n    {item['extra']}")
        append_to_log(all_new)
        send_email_digest(all_new)
    else:
        log.info("No new opportunities since last run.")

    save_seen(seen)
    log.info("Done. Full history in %s", LOG_CSV_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
