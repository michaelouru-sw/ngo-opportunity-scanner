#!/usr/bin/env python3
"""
eLearning / EdTech / Instructional Design Opportunity Scanner
================================================================

Checks a configurable list of sources (ReliefWeb's job API, plus any
career/procurement pages you add) for listings matching your keywords,
and reports only the ones it hasn't shown you before.

USAGE
-----
    python3 scanner.py

Run it once manually first to make sure it works, then schedule it to
run daily — see README.md for cron (Mac/Linux) and Task Scheduler
(Windows) instructions.

All settings (keywords, sources, email) live in config.py — edit that
file, not this one, for day-to-day tweaks.
"""

import csv
import json
import logging
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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


# ---------------------------------------------------------------------------
# Source fetchers — each returns a list of dicts:
#   {"source": str, "title": str, "url": str, "extra": str}
# ---------------------------------------------------------------------------

def fetch_reliefweb(source_cfg: dict) -> list:
    """Query ReliefWeb's public jobs API for keyword matches."""
    results = []
    query_string = " OR ".join(f'"{kw}"' for kw in config.KEYWORDS)
    params = {
        "appname": source_cfg.get("appname", "opportunity-scanner"),
        "query[value]": query_string,
        "query[operator]": "OR",
        "sort[]": "date.created:desc",
        "limit": 30,
        "fields[include][]": ["title", "url_alias", "date.created", "source.name"],
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

    for item in data.get("data", []):
        fields = item.get("fields", {})
        title = fields.get("title", "")
        if not matches_keywords(title):
            continue
        url = fields.get("url_alias") or item.get("href", "")
        org_list = fields.get("source", [])
        org_name = org_list[0]["name"] if org_list else ""
        results.append(
            {
                "source": source_cfg["name"],
                "title": title,
                "url": url,
                "extra": org_name,
            }
        )
    return results


def fetch_generic_html(source_cfg: dict) -> list:
    """
    Fetch an HTML page and flag any link whose visible text matches a
    keyword. Works reasonably well for WordPress-style job/search pages.
    Sites that heavily JS-render their listings (rare for these targets,
    but possible) won't show results here — see README.md troubleshooting.
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
        key = (text, href)
        if key in seen_on_page:
            continue
        seen_on_page.add(key)
        results.append(
            {
                "source": source_cfg["name"],
                "title": text,
                "url": href,
                "extra": "",
            }
        )
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
        log.info("  -> %d keyword match(es) on this page", len(matches))

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
            print(f"  • [{item['source']}] {item['title']}\n    {item['url']}")
        append_to_log(all_new)
        send_email_digest(all_new)
    else:
        log.info("No new opportunities since last run.")

    save_seen(seen)
    log.info("Done. Full history in %s", LOG_CSV_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
