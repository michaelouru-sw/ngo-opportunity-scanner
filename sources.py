"""
Organisation-site and ATS discovery layer.

Most organisations publish vacancies and tenders on their own careers page,
usually powered by an applicant-tracking system (ATS) with a public JSON
API. Reading those directly is faster and more accurate than scraping
search results, and finds postings that never reach job aggregators.

Fetcher types provided here (all return (items, health) like scanner.py):
  greenhouse | lever | smartrecruiters | workable | recruitee | workday
      structured ATS APIs; need only a board token (see config.SOURCES)
  org_site
      any organisation page: plain or JavaScript-rendered (Playwright),
      optional search-URL template ({q}) and WordPress site search
  worldbank
      World Bank procurement notices (consulting / EOI), with deadlines

Self-expanding: every ATS board found in an organisation page, search hit or
matching job URL is remembered and scanned on later runs (discover_boards /
board_to_source). Dead boards drop out automatically.

Built against each service's documented response shape; nothing here is
assumed to work until a run's source-health report says so.
"""

import re
import threading
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ---------------------------------------------------------------------------
# ATS board discovery
# ---------------------------------------------------------------------------

_ATS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_\-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_\-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9_\-]+)", re.I)),
    ("recruitee", re.compile(r"https?://([a-z0-9\-]+)\.recruitee\.com", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_\-]+)", re.I)),
]
_WORKDAY = re.compile(
    r"https?://([a-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_\-]+)", re.I)
_BAD_TOKENS = {"embed", "api", "j", "v1", "jobs", "careers", "www", "static", "assets", "apply", "job"}
_BAD_SITES = {"wday", "job", "details", "login"}


def discover_boards(text: str) -> set:
    """ATS boards referenced anywhere in text/HTML, as 'kind:ident' keys."""
    found = set()
    if not text:
        return found
    for kind, rx in _ATS:
        for m in rx.finditer(text):
            tok = m.group(1).lower()
            if tok not in _BAD_TOKENS:
                found.add(f"{kind}:{tok}")
    for m in _WORKDAY.finditer(text):
        tenant, wd, site = m.group(1).lower(), m.group(2).lower(), m.group(3)
        if site.lower() not in _BAD_SITES:
            found.add(f"workday:{tenant}.{wd}|{tenant}|{site}")
    return found


def board_to_source(key: str) -> dict:
    kind, ident = key.split(":", 1)
    if kind == "workday":
        host, tenant, site = ident.split("|")
        return {"name": f"Board workday:{tenant}/{site}", "type": "workday", "_key": key, "quiet": True,
                "host": f"{host}.myworkdayjobs.com", "tenant": tenant, "site": site}
    return {"name": f"Board {key}", "type": kind, "_key": key, "quiet": True, "token": ident}


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

_RENDER_LOCK = threading.Lock()  # one headless browser at a time: slower, but robust


def build_fetchers(core) -> dict:
    cfg, S = core.config, core.SESSION
    timeout = cfg.REQUEST_TIMEOUT_SECONDS

    def H():
        return {"raw": 0, "kept": 0, "error": None, "boards": []}

    def fin(items, h):
        h["kept"] = len(items)
        if h["raw"] == 0 and not h["error"]:
            h["error"] = "returned nothing parsable (empty, blocked, or format changed)"
        return items, h

    def terms(src):
        return (src.get("terms") or cfg.ORG_SEARCH_TERMS)[: src.get("max_queries", 4)]

    def mk(src, title, url, loc="", deadline=None, extra="", snippet=""):
        it = core._item(src["name"], title, url, snippet=f"{snippet} {loc}".strip(), extra=extra, deadline=deadline)
        it["location"] = core.classify_location(f"{title} {loc}")
        if src.get("region") and it["location"] == "Elsewhere/unconfirmed":
            it["location"] = src["region"]
        return it

    def jget(h, url, **kw):
        try:
            r = S.get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # network, HTTP, or JSON errors all mean "no data"
            h["error"] = str(exc)[:160]
            return None

    def jpost(h, url, payload):
        try:
            r = S.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            h["error"] = str(exc)[:160]
            return None

    # ---- ATS APIs ---------------------------------------------------------

    def greenhouse(src):
        h = H()
        data = jget(h, f"https://boards-api.greenhouse.io/v1/boards/{src['token']}/jobs")
        items = []
        for j in (data or {}).get("jobs", []):
            h["raw"] += 1
            t = j.get("title", "")
            if core.matches_keywords(t):
                items.append(mk(src, t, j.get("absolute_url", ""), loc=(j.get("location") or {}).get("name", "")))
        return fin(items, h)

    def lever(src):
        h = H()
        data = jget(h, f"https://api.lever.co/v0/postings/{src['token']}", params={"mode": "json"})
        items = []
        for j in data if isinstance(data, list) else []:
            h["raw"] += 1
            t = j.get("text", "")
            if core.matches_keywords(t):
                loc = (j.get("categories") or {}).get("location", "")
                if j.get("workplaceType") == "remote":
                    loc += " remote"
                items.append(mk(src, t, j.get("hostedUrl", ""), loc=loc))
        return fin(items, h)

    def smartrecruiters(src):
        h, items, seen = H(), [], set()
        for term in terms(src):
            data = jget(h, f"https://api.smartrecruiters.com/v1/companies/{src['token']}/postings",
                        params={"q": term, "limit": 100})
            for j in (data or {}).get("content", []):
                h["raw"] += 1
                pid, t = j.get("id"), j.get("name", "")
                if pid in seen or not core.matches_keywords(t):
                    continue
                seen.add(pid)
                loc = j.get("location") or {}
                locs = " ".join(str(loc.get(k, "")) for k in ("city", "country")) + (" remote" if loc.get("remote") else "")
                items.append(mk(src, t, f"https://jobs.smartrecruiters.com/{src['token']}/{pid}", loc=locs))
        return fin(items, h)

    def workable(src):
        h, items, seen = H(), [], set()
        for term in terms(src):
            data = jpost(h, f"https://apply.workable.com/api/v3/accounts/{src['token']}/jobs",
                         {"query": term, "location": [], "department": [], "worktype": [], "remote": []})
            for j in (data or {}).get("results", []):
                h["raw"] += 1
                code, t = j.get("shortcode"), j.get("title", "")
                if code in seen or not core.matches_keywords(t):
                    continue
                seen.add(code)
                loc = j.get("location") or {}
                locs = f"{loc.get('city', '')} {loc.get('country', '')}" + (" remote" if j.get("remote") or j.get("workplace") == "remote" else "")
                items.append(mk(src, t, f"https://apply.workable.com/{src['token']}/j/{code}/", loc=locs))
        return fin(items, h)

    def recruitee(src):
        h = H()
        data = jget(h, f"https://{src['token']}.recruitee.com/api/offers/")
        items = []
        for j in (data or {}).get("offers", []):
            h["raw"] += 1
            t = j.get("title", "")
            if core.matches_keywords(t):
                items.append(mk(src, t, j.get("careers_url", ""),
                                loc=f"{j.get('location', '')}" + (" remote" if j.get("remote") else "")))
        return fin(items, h)

    def workday(src):
        h, items, seen = H(), [], set()
        base = f"https://{src['host']}/wday/cxs/{src['tenant']}/{src['site']}/jobs"
        for term in terms(src):
            data = jpost(h, base, {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
            for j in (data or {}).get("jobPostings", []):
                h["raw"] += 1
                path, t = j.get("externalPath", ""), j.get("title", "")
                if path in seen or not core.matches_keywords(t):
                    continue
                seen.add(path)
                items.append(mk(src, t, f"https://{src['host']}/{src['site']}{path}", loc=j.get("locationsText", "")))
        return fin(items, h)

    # ---- World Bank procurement notices ----------------------------------

    def worldbank(src):
        h, items, seen = H(), [], set()
        for term in terms(src):
            data = jget(h, "https://search.worldbank.org/api/v2/procnotices",
                        params={"format": "json", "rows": 50, "qterm": term,
                                "srt": "submission_date", "order": "desc"})
            notices = (data or {}).get("procnotices", [])
            for n in notices.values() if isinstance(notices, dict) else notices:
                h["raw"] += 1
                nid = n.get("id")
                title = n.get("bid_description") or n.get("project_name") or ""
                if nid in seen or not core.matches_keywords(title):
                    continue
                seen.add(nid)
                deadline = None
                raw_dl = n.get("submission_deadline_date")
                if raw_dl:
                    try:
                        deadline = dateparser.parse(raw_dl).date()
                    except (ValueError, OverflowError):
                        pass
                kind = n.get("notice_type", "")
                url = n.get("url") or f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}"
                items.append(mk(src, title, url, loc=n.get("project_ctry_name", ""), deadline=deadline,
                                snippet=kind, extra=kind))
        return fin(items, h)

    # ---- Any organisation page -------------------------------------------

    def render(url, src):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("playwright not installed (needed for JavaScript-rendered pages)")
        with _RENDER_LOCK, sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_context().new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if src.get("wait_selector"):
                    page.wait_for_selector(src["wait_selector"], timeout=15000)
                else:
                    page.wait_for_timeout(4000)
                for _ in range(3):  # trigger lazy-loaded / infinite-scroll lists
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(700)
                return page.content()
            finally:
                browser.close()

    def page_html(url, src):
        if src.get("render"):
            return render(url, src)
        r = S.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text

    def org_site(src):
        h, items, seen = H(), [], set()
        qs = terms(src)
        urls = [src["search_url"].format(q=quote_plus(q)) for q in qs] if "search_url" in src else [src["url"]]

        for u in urls:
            try:
                html = page_html(u, src)
            except Exception as exc:
                h["error"] = str(exc)[:160]
                continue
            h["boards"] += list(discover_boards(html))
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a[href]"):
                text = a.get_text(" ", strip=True) or a.get("title") or a.get("aria-label") or ""
                href = urljoin(u, a["href"])
                if not text:
                    continue
                h["raw"] += 1
                if not href.startswith("http") or core.is_blocked_url(href) or (text, href) in seen:
                    continue
                if not core.matches_keywords(text):
                    continue
                if src.get("require_signal") and not core.has_opportunity_signal(text):
                    continue
                seen.add((text, href))
                items.append(mk(src, text, href))

        if src.get("wp_search"):  # WordPress REST site search, for small NGO sites
            base = src["url"].rstrip("/")
            for q in qs:
                data = jget(h, f"{base}/wp-json/wp/v2/search",
                            params={"search": q, "per_page": 20})
                for r in data if isinstance(data, list) else []:
                    h["raw"] += 1
                    t, href = r.get("title", ""), r.get("url", "")
                    if (t, href) in seen or not href:
                        continue
                    if core.matches_keywords(t) and core.has_opportunity_signal(t):
                        seen.add((t, href))
                        items.append(mk(src, t, href))
        if h["raw"] > 0 and h["error"] and items:
            h["error"] = None  # partial success is still success
        h["boards"] = sorted(set(h["boards"]))
        return fin(items, h)

    return {
        "greenhouse": greenhouse, "lever": lever, "smartrecruiters": smartrecruiters,
        "workable": workable, "recruitee": recruitee, "workday": workday,
        "worldbank": worldbank, "org_site": org_site,
    }
