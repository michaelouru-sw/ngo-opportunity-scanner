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
    "vacancy", "job vacancy", "job opening", "we are hiring", "is hiring",
    "recruitment", "full-time position", "permanent position", "staff position",
    "career opportunity", "job opportunity",
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
MAX_DETAIL_PAGE_CHECKS_PER_RUN = 40  # caps total extra requests per run

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
MAX_WEB_SEARCH_QUERIES_PER_RUN = 10
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
        "name": "ReliefWeb Jobs (global)",
        "type": "reliefweb_api",
        "url": "https://api.reliefweb.int/v1/jobs",
        "appname": "ngo-elearning-scanner",
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
        "type": "generic_html",
        "url": "https://www.devex.com/jobs/search?filter%5Bkeywords%5D=instructional+design",
        "css_selector": "a",
        "inherently_global": True,
    },
    {
        "name": "Idealist — Instructional Design (remote)",
        "type": "generic_html",
        "url": "https://www.idealist.org/en/jobs?q=instructional%20design&type=REMOTE",
        "css_selector": "a",
        "inherently_global": True,
        # JavaScript-rendered site — may return little via plain HTML fetch.
    },
    {
        "name": "DevelopmentAid — Jobs",
        "type": "generic_html",
        "url": "https://www.developmentaid.org/jobs?keywords=instructional+design",
        "css_selector": "a",
        "inherently_global": True,
        # Full listings require a paid membership; public page surfaces
        # titles/previews only.
    },
    {
        "name": "DevelopmentAid — Tenders (consultancies)",
        "type": "generic_html",
        "url": "https://www.developmentaid.org/tenders/search?keywords=instructional+design",
        "css_selector": "a",
        "inherently_global": True,
    },
    {
        "name": "DevelopmentAid — Grants",
        "type": "generic_html",
        "url": "https://www.developmentaid.org/grants/search?keywords=education",
        "css_selector": "a",
        "inherently_global": True,
    },

    # --- Kenya/local sources (disabled by default — uncomment to re-add) ---
    # {
    #     "name": "Corporate Staffing Kenya",
    #     "type": "generic_html",
    #     "url": "https://www.corporatestaffing.co.ke/?s=instructional+design",
    #     "css_selector": "a",
    # },
]

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
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 (compatible; OpportunityScanner/1.0; personal use)"
