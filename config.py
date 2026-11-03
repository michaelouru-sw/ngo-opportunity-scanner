"""
Configuration for the eLearning/EdTech Opportunity Scanner.

Edit this file to change what it searches for, where it looks, and
whether/how it emails you. Nothing here needs to be a Python expert
to edit — just change the values inside the quotes/brackets.
"""

# ---------------------------------------------------------------------------
# KEYWORDS
# A listing is flagged as a match if its title/text contains ANY of these
# (case-insensitive). Keep this list focused — too broad and you'll get
# noise; too narrow and you'll miss things.
# ---------------------------------------------------------------------------
KEYWORDS = [
    "instructional design",
    "instructional designer",
    "e-learning",
    "elearning",
    "learning management system",
    "lms",
    "learning experience design",
    "learning experience designer",
    "learning designer",
    "learning technologist",
    "curriculum developer",
    "course developer",
    "moodle",
    "curriculum digitization",
    "digital learning",
    "capacity building consultant",
    "edtech consultant",
]

# Words that, if present, should EXCLUDE an otherwise-matching listing.
EXCLUDE_KEYWORDS = [
    "unpaid",
    "volunteer",
    "intern",       # whole-word match: also catches "internship", not "international"
    "internship",
    "asistente",    # Spanish-language postings
]

# ---------------------------------------------------------------------------
# CATEGORIZATION: Jobs / Consultancies / Grants
# Every matched listing is classified into one of these three so the email
# digest and CSV can group them. This is best-effort keyword matching, not
# guaranteed — ambiguous listings fall into "Uncertain" rather than being
# force-fit into the wrong bucket. Order matters in the code (grants and
# consultancy signals are checked before job signals, since a job posting
# rarely also says "call for proposals").
#
# "Consultancy" deliberately covers BOTH individual-consultant and
# firm/company bids (RFPs, ToRs, EOIs are almost always open to either
# unless a listing explicitly says "individuals only" or "firms only") —
# so Instracta-eligible and personal-capacity opportunities both land here.
# ---------------------------------------------------------------------------
GRANT_KEYWORDS = [
    "call for proposals", "request for applications", "grant opportunity",
    "funding opportunity", "sub-grant", "subgrant", "seed grant",
    "grant application", "notice of funding", "rfa ",
]

CONSULTANCY_KEYWORDS = [
    "consultant", "consultancy", "individual contractor", "terms of reference",
    "request for proposals", "rfp", "expression of interest", "eoi",
    "call for consultancy", "firm or individual", "short-term expert",
    "sti ", "long-term expert", "lte ",
]

JOB_KEYWORDS = [
    "vacancy", "vacancies",
    "job opening", "we are hiring", "is hiring", "hiring",
    "recruitment", "full-time position", "permanent position", "staff position",
    "career opportunity", "job opportunity", "job posting",
]

# Extra generic phrases that mark something as an actual opportunity
# (not a vendor/blog/product page) but aren't specific enough to assign a
# category on their own — combined with GRANT/CONSULTANCY/JOB_KEYWORDS
# below to decide whether to keep a web-search result at all.
OPPORTUNITY_SIGNAL_EXTRA_KEYWORDS = [
    "position available", "role available", "apply now", "how to apply",
    "application deadline", "submit your application", "open position",
    "now recruiting", "we're recruiting",
]

# ---------------------------------------------------------------------------
# NOISE FILTERING FOR OPEN-WEB SEARCH
# The open web search (below) will happily return vendor product pages,
# blog posts, and "best LMS for nonprofits" listicles that mention your
# topic keywords without being an actual opportunity. Two defenses:
#
# 1. REQUIRE_OPPORTUNITY_SIGNAL_FOR_WEB_SEARCH: a web-search result must
#    contain a job/consultancy/grant signal word (not just a topic keyword)
#    to be kept at all. This is what filters out "Moodle LMS Development,
#    Hosting & Consulting" (no signal word) while keeping "Consultant —
#    Instructional Design | UNITAR" (has "consultant").
#
# 2. DOMAIN_BLOCKLIST / URL_PATTERN_BLOCKLIST: known vendor/blog/aggregator
#    sites and generic category/search-listing pages (not specific
#    postings) are skipped outright regardless of wording. Add to these
#    lists whenever a repeat offender shows up in your digest.
# ---------------------------------------------------------------------------
REQUIRE_OPPORTUNITY_SIGNAL_FOR_WEB_SEARCH = True

DOMAIN_BLOCKLIST = [
    "moodle.org", "moodle.com", "docs.moodle.org",
    "raccoongang.com", "ispring.com", "softwareadvice.com", "ruzuku.com",
    "evolmind.com", "dynamind-elearning.com", "teachers.institute",
    "groundwork1.com", "elearningtrendz.com", "thelearningnuggets.com",
    "kiron.digital", "beyondkey.com", "learnstream.io", "openlms.net",
    "christytuckerlearning.com", "humentum.org",
]

# Any of these substrings appearing anywhere in a URL causes it to be
# skipped — used for generic category/tag/search-result pages rather than
# specific postings, and personal profile pages.
URL_PATTERN_BLOCKLIST = [
    "/skills/",       # unjobs.org tag/category pages, not specific postings
    "/themes/",       # same
    "linkedin.com/in/",   # personal profile, not a job posting
    "linkedin.com/jobs/moodle-jobs",  # generic aggregator listing page
    "indeed.com/q-", # generic Indeed search-results page, not one posting
    "jooble.org/jobs-",  # generic Jooble search-results page
]

# ---------------------------------------------------------------------------
# REMOTE SIGNAL
# ---------------------------------------------------------------------------
REMOTE_SIGNAL_KEYWORDS = [
    "remote", "home-based", "home based", "telecommute", "virtual",
    "worldwide", "any location", "work from anywhere", "global consultant",
]

REQUIRE_REMOTE_SIGNAL = False

# ---------------------------------------------------------------------------
# EXPIRATION FILTERING
# ReliefWeb's API gives a real closing date, so those are filtered
# precisely. For scraped/web-search sources, the scanner opens each match's
# own page and looks for a deadline phrase ("deadline", "closing date",
# "apply by", etc.) and drops it if that date has passed. If no date can be
# found at all, it's kept but flagged "deadline not found — verify manually".
# ---------------------------------------------------------------------------
CHECK_DEADLINES_ON_DETAIL_PAGES = True
MAX_DETAIL_PAGE_CHECKS_PER_RUN = 80  # only NEW items are checked, so this is rarely hit

# ---------------------------------------------------------------------------
# OPEN-WEB SEARCH (reaches institutional sites you haven't hardcoded)
# In addition to fixed sources below, the scanner runs keyword searches
# against DuckDuckGo's HTML search (no API key needed, no JS required) —
# this is what reaches UNESCO/UNICEF/World Bank/foundation pages etc. that
# aren't in the fixed SOURCES list. Each topic below is combined with a
# category modifier ("consultancy", "vacancy", "grant") to build queries,
# e.g. "instructional design consultancy NGO".
#
# MAX_WEB_SEARCH_QUERIES_PER_RUN caps how many searches run per execution —
# keep this modest; DuckDuckGo can temporarily block an IP that queries it
# too aggressively. At ~1 query/second this is a light load.
# ---------------------------------------------------------------------------
WEB_SEARCH_ENABLED = True
WEB_SEARCH_TOPICS = [
    "instructional design",
    "e-learning",
    "learning management system Moodle",
    "digital learning",
]
WEB_SEARCH_CATEGORY_MODIFIERS = ["consultancy NGO", "vacancy NGO", "grant education"]
MAX_WEB_SEARCH_QUERIES_PER_RUN = 10  # queries rotate daily so every combination gets covered
MAX_RESULTS_PER_WEB_SEARCH_QUERY = 8

# ---------------------------------------------------------------------------
# SOURCES
# Fixed sources checked every run, in addition to the open-web search above.
#
# Types supported:
#   "reliefweb_api" — ReliefWeb's public jobs API (global, real closing dates)
#   "generic_html"  — scans a listing/search page for keyword-matching links
#
# NOTE: HTML-scraped sources can break if a site redesigns its page, and
# some (DevelopmentAid, Idealist) gate full detail behind a membership
# login — scraping will surface titles/snippets from public pages only.
# ---------------------------------------------------------------------------
SOURCES = [
    {
        # ReliefWeb retired API v1 and disables any appname that has not been
        # approved (https://apidoc.reliefweb.int/). Request an appname, then
        # set it as the RELIEFWEB_APPNAME environment variable / Actions secret.
        # Until then this source is skipped (and reported in the health section).
        "name": "ReliefWeb Jobs (global)",
        "type": "reliefweb_api",
        "url": "https://api.reliefweb.int/v2/jobs",
        "appname_env": "RELIEFWEB_APPNAME",
    },
    {
        "name": "UNjobs — Instructional Design",
        "type": "generic_html",
        "url": "https://unjobs.org/skills/instructional-design",
        "css_selector": "a",
        "inherently_global": True,
    },
    {
        "name": "UNjobs — E-learning",
        "type": "generic_html",
        "url": "https://unjobs.org/skills/e-learning",
        "css_selector": "a",
        "inherently_global": True,
    },
    {
        "name": "UNjobs — Learning Management Systems",
        "type": "generic_html",
        "url": "https://unjobs.org/skills/learning-management-systems",
        "css_selector": "a",
        "inherently_global": True,
    },
    {
        "name": "Devex Jobs — Instructional Design",
        "type": "org_site", "render": True, "expect_matches": True,
        "url": "https://www.devex.com/jobs/search?filter%5Bkeywords%5D=instructional+design",
        "css_selector": "a",
        "inherently_global": True,
    },
    {
        "name": "Idealist — Instructional Design (remote)",
        "type": "org_site", "render": True, "expect_matches": True,
        "url": "https://www.idealist.org/en/jobs?q=instructional%20design&type=REMOTE",
        "css_selector": "a",
        "inherently_global": True,
        # JavaScript-rendered site — may return little via plain HTML fetch.
    },
    {
        "name": "DevelopmentAid — Jobs",
        "type": "org_site", "render": True, "expect_matches": True,
        "url": "https://www.developmentaid.org/jobs?keywords=instructional+design",
        "css_selector": "a",
        "inherently_global": True,
        # Full listings require a paid membership; public page surfaces
        # titles/previews only.
    },
    {
        "name": "DevelopmentAid — Tenders (consultancies)",
        "type": "org_site", "render": True, "expect_matches": True,
        "url": "https://www.developmentaid.org/tenders/search?keywords=instructional+design",
        "css_selector": "a",
        "inherently_global": True,
    },
    {
        "name": "DevelopmentAid — Grants",
        "type": "org_site", "render": True, "expect_matches": True,
        "url": "https://www.developmentaid.org/grants/search?keywords=education",
        "css_selector": "a",
        "inherently_global": True,
    },

    # -----------------------------------------------------------------------
    # ORGANISATION SITES — SEEDS. Reached from a sandbox without internet, so
    # these URLs are unverified: the first Actions run's source-health lines
    # show which ones need fixing. Failures are isolated and reported, never
    # fatal. Options per source: "render": True (JavaScript pages),
    # "search_url": "...{q}..." (site search), "wp_search": True (WordPress
    # sites), "region": "Kenya/Africa" (treat unlabelled results as local),
    # "terms": [...] (override ORG_SEARCH_TERMS).
    # ATS boards found on these pages are auto-discovered and scanned too.
    # -----------------------------------------------------------------------
    {"name": "World Bank Procurement (consulting notices)", "type": "worldbank"},
    {"name": "UNDP Jobs", "type": "org_site", "url": "https://jobs.undp.org/cj_view_jobs.cfm"},
    {"name": "UNICEF Jobs", "type": "org_site", "url": "https://jobs.unicef.org/en-us/listing/", "render": True},
    {"name": "UNESCO Careers", "type": "org_site", "url": "https://careers.unesco.org/", "render": True},
    {"name": "ILO Jobs", "type": "org_site", "url": "https://jobs.ilo.org/", "render": True},
    {"name": "FAO Jobs", "type": "org_site", "url": "https://jobs.fao.org/", "render": True},
    {"name": "WHO Careers", "type": "org_site", "url": "https://careers.who.int/careersection/ex/jobsearch.ftl", "render": True},
    {"name": "UNITAR Vacancies", "type": "org_site", "url": "https://unitar.org/vacancy-announcements"},
    {"name": "UNGM Notices", "type": "org_site", "url": "https://www.ungm.org/Public/Notice", "render": True},
    {"name": "Medair Tenders", "type": "org_site", "url": "https://medair.org/tenders/"},
    {"name": "NGO Tenders — ID consultants", "type": "org_site", "url": "https://ngotenders.net/instructional-design-consultants/"},
    {"name": "fundsforNGOs", "type": "org_site", "search_url": "https://www.fundsforngos.org/?s={q}", "require_signal": True},
    {"name": "BrighterMonday Kenya", "type": "org_site", "search_url": "https://www.brightermonday.co.ke/jobs?q={q}", "region": "Kenya/Africa"},
    {"name": "Corporate Staffing Kenya", "type": "org_site", "search_url": "https://www.corporatestaffing.co.ke/?s={q}", "region": "Kenya/Africa"},
    {"name": "KENET", "type": "org_site", "url": "https://www.kenet.or.ke", "wp_search": True, "region": "Kenya/Africa"},
    {"name": "APHRC", "type": "org_site", "url": "https://aphrc.org", "wp_search": True, "region": "Kenya/Africa"},

    # ATS boards you know about can be added directly (types: greenhouse,
    # lever, smartrecruiters, workable, recruitee, workday), e.g.:
    # {"name": "Example Org", "type": "lever", "token": "example", "quiet": True},
    # {"name": "Example UN agency", "type": "workday", "host": "x.wd3.myworkdayjobs.com",
    #  "tenant": "x", "site": "Careers", "quiet": True},
]

# ---------------------------------------------------------------------------
# PERFORMANCE
# ---------------------------------------------------------------------------
MAX_WORKERS = 8            # parallel page fetches (sources and deadline checks)
REQUEST_RETRIES = 2        # automatic retries on 429/5xx/connection errors
SEEN_RETENTION_DAYS = 365  # forget dedup entries older than this

# ---------------------------------------------------------------------------
# RELEVANCE SCORING (higher = shown first; "TOP PICKS" in the digest)
# Tuned for a Nairobi-based consultant/lead: remote or Kenya/Africa first,
# consultancies and grants above jobs, onsite-elsewhere roles pushed down.
# ---------------------------------------------------------------------------
HOME_REGION_KEYWORDS = ["kenya", "nairobi", "east africa", "africa", "sub-saharan"]
CATEGORY_SCORE = {"Consultancy": 3, "Grant": 2, "Job": 1, "Uncertain": 0}
REMOTE_SCORE = 3
HOME_REGION_SCORE = 3
ONSITE_ELSEWHERE_PENALTY = -2
PREFERRED_TERMS = {          # term -> points (each counted once)
    "moodle": 2, "lms": 1, "learning management": 1,
    "instructional design": 1, "learning experience": 1,
    "digital learning": 1, "ai in education": 2, "e-learning": 1, "elearning": 1,
    "lead": 1, "senior": 1, "consultant": 1,
}
TOP_PICK_MIN_SCORE = 6

# ---------------------------------------------------------------------------
# ORGANISATION-SITE SEARCH
# Terms used for ATS searches, site-search URLs and WordPress search.
# ---------------------------------------------------------------------------
ORG_SEARCH_TERMS = [
    "instructional design", "e-learning", "learning management system",
    "moodle", "digital learning",
]
MAX_DYNAMIC_BOARDS = 60  # auto-discovered ATS boards kept under watch

# Site-targeted web queries: find postings on the platforms organisations
# publish on, even when no aggregator lists them. Each filter x term is a query.
WEB_SEARCH_SITE_FILTERS = [
    "site:myworkdayjobs.com", "site:boards.greenhouse.io", "site:jobs.lever.co",
    "site:apply.workable.com", "site:jobs.smartrecruiters.com", "site:recruitee.com",
]
WEB_SEARCH_SITE_TERMS = ["instructional design", "e-learning consultant", "moodle LMS"]

# Optional Brave Search API (free tier ~2,000 queries/month): far more
# reliable from cloud IPs than scraping DuckDuckGo, and returns fresh results.
# Get a key at https://brave.com/search/api/ and add it as the BRAVE_API_KEY
# repo secret. Without it the scanner falls back to DuckDuckGo.
BRAVE_API_KEY_ENV = "BRAVE_API_KEY"
BRAVE_MAX_QUERIES_PER_RUN = 30

# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------
LOG_CSV_PATH = "opportunities_log.csv"
SEEN_STORE_PATH = "seen_opportunities.json"

# ---------------------------------------------------------------------------
# EMAIL (optional) — digest is grouped into Jobs / Consultancies / Grants
# ---------------------------------------------------------------------------
EMAIL_ENABLED = True

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = "michaelouru2@gmail.com"
SMTP_PASSWORD_ENV_VAR = "OPPORTUNITY_SCANNER_SMTP_PASSWORD"
EMAIL_FROM = "michaelouru2@gmail.com"
EMAIL_TO = "michaelouru2@gmail.com"
EMAIL_SUBJECT = "New eLearning/EdTech opportunities found"

# ---------------------------------------------------------------------------
# NETWORK
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT_SECONDS = 25
USER_AGENT = "Mozilla/5.0 (compatible; OpportunityScanner/1.0; personal use)"
