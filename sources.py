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

import email
import imaplib
import os
import re
import threading
from datetime import date, timedelta
from urllib.parse import quote, quote_plus, urljoin

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
        if src.get("category"):  # source lists one kind of thing (e.g. only CFPs)
            it["category"] = src["category"]
        it["rolling"] = bool(src.get("rolling"))  # no deadline expected (e.g. journal directories)
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

    WB_OPEN = ("expression of interest", "request for proposal", "invitation", "request for bid",
               "specific procurement", "general procurement", "prequalification", "request for quotation")
    WB_DEADLINE_KEYS = ("submission_deadline_date", "bid_deadline_date", "deadline_date", "submission_deadline")

    def worldbank(src):
        """Open World Bank procurement notices only: closed/awarded/cancelled
        notices and anything without a future submission deadline are skipped."""
        h, items, seen = H(), [], set()
        today = core._today_utc()
        for term in (src.get("terms") or cfg.WORLDBANK_TERMS):
            for offset in range(0, src.get("pages", 2) * 100, 100):
                data = jget(h, "https://search.worldbank.org/api/v2/procnotices",
                            params={"format": "json", "rows": 100, "os": offset, "qterm": term,
                                    "srt": "submission_deadline_date", "order": "desc"})
                notices = (data or {}).get("procnotices", [])
                notices = list(notices.values()) if isinstance(notices, dict) else notices
                if not notices:
                    break
                for n in notices:
                    h["raw"] += 1
                    nid = n.get("id")
                    kind = (n.get("notice_type") or "").strip()
                    status = (n.get("notice_status") or "").lower()
                    if nid in seen or "award" in kind.lower() or any(x in status for x in ("closed", "cancel", "award")):
                        continue
                    if kind and not any(k in kind.lower() for k in WB_OPEN):
                        continue
                    deadline = None
                    for key in WB_DEADLINE_KEYS:
                        if n.get(key):
                            try:
                                deadline = dateparser.parse(str(n[key])).date()
                            except (ValueError, OverflowError):
                                pass
                            break
                    if not deadline or deadline < today:
                        continue
                    title = n.get("bid_description") or n.get("project_name") or ""
                    if not core.matches_keywords(f"{title} {n.get('project_name', '')}"):
                        continue
                    seen.add(nid)
                    url = n.get("url") or f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}"
                    it = mk(src, title, url, loc=n.get("project_ctry_name", ""), deadline=deadline,
                            snippet=f"{kind} {n.get('project_name', '')}", extra=kind)
                    if any(k in kind.lower() for k in ("expression of interest", "request for proposal")):
                        it["category"] = "Consultancy"
                    items.append(it)
        return fin(items, h)

    # ---- DOAJ: journals that charge no APC ---------------------------------

    def doaj(src):
        """Directory of Open Access Journals: only journals with has_apc=false,
        matched on theme. Each journal is reported once."""
        h, items, seen = H(), [], set()
        for term in (src.get("terms") or cfg.ACADEMIC_JOURNAL_TERMS):
            query = (f'bibjson.apc.has_apc:false AND (bibjson.keywords:"{term}" '
                     f'OR bibjson.title:"{term}")')
            data = jget(h, "https://doaj.org/api/search/journals/" + quote(query, safe=""),
                        params={"pageSize": src.get("page_size", 25)})
            for r in (data or {}).get("results", []):
                h["raw"] += 1
                bj = r.get("bibjson") or {}
                title = bj.get("title", "")
                home = next((l.get("url") for l in bj.get("link", []) if l.get("type") == "homepage"), "")
                if not title or not home or r.get("id") in seen:
                    continue
                seen.add(r.get("id"))
                pub = (bj.get("publisher") or {}).get("name", "")
                it = mk(src, f"{title} (journal, no APC)", home, extra=f"DOAJ journal, no APC · {pub}".strip(" ·"))
                it["category"], it["apc"], it["location"] = "Academic", "no-fee", "Journal"
                items.append(it)
        return fin(items, h)

    # ---- LinkedIn: your own job-alert emails (no scraping) -----------------

    _LI_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*?-)?(\d{6,})", re.I)
    _LI_GENERIC = {"view job", "apply", "apply now", "see all jobs", "view jobs", "unsubscribe", "easy apply"}

    def _email_html(msg):
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                raw = part.get_payload(decode=True) or b""
                return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""

    def _context(a):
        """Text block around a job link (company / location lines)."""
        node = a
        for _ in range(4):
            node = node.parent
            if node is None:
                break
            txt = node.get_text(" ", strip=True)
            if 40 < len(txt) < 400:
                return txt
        return a.get_text(" ", strip=True)

    def linkedin_alerts(src):
        """Reads LinkedIn job-alert emails from your Gmail over IMAP (read-only)
        using the same app password as the email digest. LinkedIn does not
        allow scraping, so this uses the alerts LinkedIn itself sends you."""
        h = H()
        pw = os.environ.get(cfg.SMTP_PASSWORD_ENV_VAR, "")
        if not pw:
            h["error"] = f"config: {cfg.SMTP_PASSWORD_ENV_VAR} not set (needed to read alert emails)"
            return [], h
        found = {}
        mail = None
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
            mail.login(cfg.SMTP_USERNAME, pw)
            mail.select(src.get("mailbox", "INBOX"), readonly=True)
            since = (date.today() - timedelta(days=src.get("days", 4))).strftime("%d-%b-%Y")
            _, data = mail.search(None, f'(SINCE "{since}" FROM "linkedin.com")')
            ids = data[0].split()[-src.get("max_emails", 60):]
            h["raw"] = len(ids)
            for mid in ids:
                _, msg_data = mail.fetch(mid, "(BODY.PEEK[])")
                soup = BeautifulSoup(_email_html(email.message_from_bytes(msg_data[0][1])), "html.parser")
                for a in soup.find_all("a", href=True):
                    m = _LI_JOB.search(a["href"])
                    text = a.get_text(" ", strip=True)
                    if not m or len(text) < 4 or text.lower() in _LI_GENERIC:
                        continue
                    jid = m.group(1)
                    if jid not in found or len(text) > len(found[jid][0]):
                        found[jid] = (text, _context(a))
        except Exception as exc:
            h["error"] = f"IMAP: {exc}"[:160]
            return [], h
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass

        items = []
        for jid, (title, ctx) in found.items():
            if core.EXCLUDE_RE and core.EXCLUDE_RE.search(f"{title}".lower()):
                continue
            it = mk(src, title, f"https://www.linkedin.com/jobs/view/{jid}/", loc=ctx, snippet=ctx)
            it["rolling"] = True  # fresh alert email; the page sits behind a login so it can't be re-checked
            if it["category"] == "Uncertain":
                it["category"] = "Job"
            it["no_title_dedupe"] = True  # same title at different companies are different jobs
            items.append(it)
        if h["raw"] == 0:
            h["error"] = (f"no LinkedIn alert emails in the last {src.get('days', 4)} days "
                          "(create job alerts on linkedin.com/jobs with email notifications)")
        h["kept"] = len(items)
        return items, h

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
        match = core.topic_matches if src.get("topic_only") else core.matches_keywords
        if "search_url" in src:  # optional {p} placeholder + "pages": N walks result pages
            urls = [src["search_url"].format(q=quote_plus(q), p=pg) for q in qs
                    for pg in range(1, src.get("pages", 1) + 1)]
        else:
            urls = [src["url"]]

        for u in urls:
            try:
                html = page_html(u, src)
            except Exception as exc:
                h["error"] = str(exc)[:160]
                continue
            h["boards"] += list(discover_boards(html))
            soup = BeautifulSoup(html, "html.parser")
            lc = src.get("link_contains")
            for a in soup.select("a[href]"):
                text = a.get_text(" ", strip=True) or a.get("title") or a.get("aria-label") or ""
                href = urljoin(u, a["href"])
                if not text:
                    continue
                h["raw"] += 1
                if not href.startswith("http") or core.is_blocked_url(href):
                    continue
                if lc:  # page is already topic-scoped (e.g. an EasyChair topic list): take every call link
                    if lc not in href or any(x in href for x in src.get("link_exclude", [])):
                        continue
                    if len(text) < 14:  # bare acronym: borrow the table row for context
                        row = a.find_parent(["tr", "li"])
                        if row:
                            text = row.get_text(" ", strip=True)[:140]
                    if core.EXCLUDE_RE and core.EXCLUDE_RE.search(text.lower()):
                        continue
                elif not match(text):
                    continue
                if (text, href) in seen:
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
                    if match(t) and core.has_opportunity_signal(t):
                        seen.add((t, href))
                        items.append(mk(src, t, href))
        if h["raw"] > 0 and h["error"] and items:
            h["error"] = None  # partial success is still success
        h["boards"] = sorted(set(h["boards"]))
        return fin(items, h)

    return {
        "greenhouse": greenhouse, "lever": lever, "smartrecruiters": smartrecruiters,
        "workable": workable, "recruitee": recruitee, "workday": workday,
        "worldbank": worldbank, "org_site": org_site, "doaj": doaj,
        "linkedin_alerts": linkedin_alerts,
    }
