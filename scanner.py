#!/usr/bin/env python3
"""
eLearning / EdTech / Instructional Design Opportunity Scanner
================================================================

Checks a configurable list of GLOBAL sources (ReliefWeb's job API, UNjobs,
Devex, Idealist, plus any pages you add) for listings matching your
keywords, filters out ones that have already closed, and reports only
what you haven't seen before.

USAGE
-----
    python3 scanner.py

All settings (keywords, sources, email, expiration-filtering) live in
config.py — edit that file, not this one, for day-to-day tweaks.
"""

import csv
import json
import logging
import os
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

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

# Phrases that typically precede a deadline on a job/consultancy page.
DEADLINE_TRIGGERS = [
    "deadline", "closing date", "apply by", "application deadline",
    "due date", "closes on", "applications close", "submission deadline",
    "closing on", "last date", "applications due",
]

_detail_page_checks_used = 0  # module-level counter, capped per run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def matches_keywords(text: str) -> bool:
    """True if text contains a wanted keyword and no excluded keyword."""
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
            f, fieldnames=["found_at", "source", "title", "url", "extra"]
        )
        if not file_exists:
            writer.writeheader()
        for item in new_items:
            writer.writerow(item)


def _today_utc():
    return datetime.now(timezone.utc).date()


def find_deadline_in_text(text: str):
    """
    Look for a deadline near a trigger phrase (e.g. "Deadline: 30 Sept
    2026") and try to parse a date out of it. Returns a date object, or
    None if nothing parseable was found. Best-effort, not guaranteed.
    """
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
    """
    Fetch a listing's own page and try to find/parse a deadline.
    Returns (is_expired: bool|None, deadline_date_or_None).
    is_expired is None if we couldn't determine anything (be permissive —
    don't drop a listing just because we failed to check it).
    Respects MAX_DETAIL_PAGE_CHECKS_PER_RUN to avoid hammering sites.
    """
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


# ---------------------------------------------------------------------------
# Source fetchers — each returns a list of dicts:
#   {"source": str, "title": str, "url": str, "extra": str}
# ---------------------------------------------------------------------------

def fetch_reliefweb(source_cfg: dict) -> list:
    """
    Query ReliefWeb's public jobs API for keyword matches. ReliefWeb
    provides a real closing date per job, so expired listings are
    excluded precisely here rather than guessed at.
    """
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
            source_cfg["url"],
            params=params,
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

        # Exclude anything already past its closing date.
        date_info = fields.get("date", {}) or {}
        closing_raw = date_info.get("closing")
        if closing_raw:
            try:
                closing_date = dateparser.parse(closing_raw).date()
                if closing_date < today:
                    skipped_expired += 1
                    continue
            except (ValueError, OverflowError):
                pass  # couldn't parse -> don't penalize the listing

        url = fields.get("url") or fields.get("url_alias") or item.get("href", "")
        org_list = fields.get("source", [])
        org_name = org_list[0]["name"] if org_list else ""
        country_list = fields.get("country", [])
        country_name = country_list[0]["name"] if country_list else ""

        extra_bits = [b for b in (org_name, country_name) if b]
        results.append(
            {
                "source": source_cfg["name"],
                "title": title,
                "url": url,
                "extra": " — ".join(extra_bits),
            }
        )

    if skipped_expired:
        log.info("  (excluded %d already-closed ReliefWeb listing(s))", skipped_expired)
    return results


def fetch_generic_html(source_cfg: dict) -> list:
    """
    Fetch an HTML page and flag any link whose visible text matches a
    keyword. For each match, optionally visit the listing's own page to
    check for a deadline and drop it if that deadline has passed (see
    config.CHECK_DEADLINES_ON_DETAIL_PAGES).
    """
    results = []
    try:
        resp = requests.get(
            source_cfg["url"],
            timeout=config.REQUEST_TIMEOUT_SECONDS,
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

        # Resolve relative URLs
        if href.startswith("/"):
            base = "/".join(source_cfg["url"].split("/")[:3])
            href = base + href
        if not href.startswith("http"):
            continue

        key = (text, href)
        if key in seen_on_page:
            continue
        seen_on_page.add(key)

        remote_ok = is_global_source or has_remote_signal(text)
        if config.REQUIRE_REMOTE_SIGNAL and not remote_ok:
            continue

        extra = "Remote/global" if remote_ok else "Location not confirmed remote"

        is_expired, deadline = check_deadline_via_detail_page(href)
        if is_expired:
            skipped_expired += 1
            continue
        if deadline:
            extra += f" — deadline {deadline.isoformat()}"
        elif config.CHECK_DEADLINES_ON_DETAIL_PAGES:
            extra += " — deadline not found, verify manually"

        results.append(
            {
                "source": source_cfg["name"],
                "title": text,
                "url": href,
                "extra": extra,
            }
        )

    if skipped_expired:
        log.info("  (excluded %d listing(s) with a passed deadline)", skipped_expired)
    return results


FETCHERS = {
    "reliefweb_api": fetch_reliefweb,
    "generic_html": fetch_generic_html,
}


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email_digest(new_items: list) -> None:
    if not config.EMAIL_ENABLED:
        return
    if not new_items:
        return

    password = os.environ.get(config.SMTP_PASSWORD_ENV_VAR)
    if not password:
        log.warning(
            "EMAIL_ENABLED is True but the %s environment variable is not "
            "set — skipping email. See README.md.",
            config.SMTP_PASSWORD_ENV_VAR,
        )
        return

    lines = [f"{len(new_items)} new opportunity(ies) found:\n"]
    for item in new_items:
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
    log.info("Starting scan across %d source(s)...", len(config.SOURCES))
    seen = load_seen()
    all_new = []

    for source_cfg in config.SOURCES:
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
        for item in all_new:
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
