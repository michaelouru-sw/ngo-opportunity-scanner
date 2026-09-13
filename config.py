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
# Useful for filtering out roles you don't want (e.g. junior/unpaid/etc.)
EXCLUDE_KEYWORDS = [
    "unpaid",
    "volunteer",
]

# Words/phrases that suggest a listing is remote-friendly or open to
# candidates anywhere, not tied to one office location. Used only to LABEL
# results (see REQUIRE_REMOTE_SIGNAL below to also filter by this).
REMOTE_SIGNAL_KEYWORDS = [
    "remote",
    "home-based",
    "home based",
    "telecommute",
    "virtual",
    "worldwide",
    "any location",
    "work from anywhere",
    "global consultant",
]

# If True, generic_html results that show NEITHER a remote-signal keyword
# NOR come from an inherently international source (reliefweb, unjobs,
# devex, idealist) are dropped rather than just labeled. Turn this on once
# you've reviewed a few runs and confirmed you only want remote/global
# postings — it's off by default so you can see everything first and judge.
REQUIRE_REMOTE_SIGNAL = False

# ---------------------------------------------------------------------------
# EXPIRATION FILTERING
# Nothing is more annoying than a "new opportunity" that closed months ago.
# ReliefWeb's API gives us a real closing date, so those are filtered
# precisely. For scraped (generic_html) sources we don't get a reliable
# structured date from the listing page alone, so the scanner does a
# second pass: it opens each matching link and looks for a nearby deadline
# phrase ("deadline", "closing date", "apply by", etc.) and tries to parse
# a date from it. If that date is in the past, the listing is dropped
# instead of reported. If no date can be found at all, the listing is kept
# but flagged "(deadline not found — verify manually)" so you know to check.
# ---------------------------------------------------------------------------
CHECK_DEADLINES_ON_DETAIL_PAGES = True
MAX_DETAIL_PAGE_CHECKS_PER_RUN = 30  # caps total extra requests per run

# ---------------------------------------------------------------------------
# SOURCES
# Three kinds of sources are supported:
#
# 1. "reliefweb_api" — ReliefWeb's public jobs API. Global coverage, and the
#    only source with a genuinely reliable closing date, so expired listings
#    are excluded automatically and precisely.
#
# 2. "generic_html" — fetches a listings/search page and looks for links
#    whose visible text contains any KEYWORD. Works for server-rendered
#    pages (most WordPress sites, UNjobs). Won't see JavaScript-rendered
#    listings (a few modern job boards load results after the page loads —
#    if a source below consistently returns nothing, that's likely why).
#
# 3. Kenya/local job boards are commented out by default per your request
#    for worldwide/remote-first results — uncomment any you want back in.
#
# NOTE: HTML-scraped sources can break if a site redesigns its page. If a
# source stops returning results, check its URL still works in a browser.
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "name": "ReliefWeb Jobs (global)",
        "type": "reliefweb_api",
        "url": "https://api.reliefweb.int/v1/jobs",
        "appname": "ngo-elearning-scanner",  # required by ReliefWeb API, any string works
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
        # Idealist is a JavaScript-rendered site — this may return few or no
        # results via plain HTML fetching. Left in as a best-effort source;
        # if it consistently returns nothing, that's why (see README).
    },

    # --- Kenya/local sources (disabled by default — uncomment to re-add) ---
    # {
    #     "name": "Corporate Staffing Kenya",
    #     "type": "generic_html",
    #     "url": "https://www.corporatestaffing.co.ke/?s=instructional+design",
    #     "css_selector": "a",
    # },
    # {
    #     "name": "Corporate Staffing Kenya (LMS)",
    #     "type": "generic_html",
    #     "url": "https://www.corporatestaffing.co.ke/?s=learning+management+system",
    #     "css_selector": "a",
    # },
    # {
    #     "name": "Fuzu Kenya",
    #     "type": "generic_html",
    #     "url": "https://www.fuzu.com/kenya/search?q=instructional%20design",
    #     "css_selector": "a",
    # },
]

# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------
LOG_CSV_PATH = "opportunities_log.csv"     # every match ever found, timestamped
SEEN_STORE_PATH = "seen_opportunities.json"  # tracks what's already been reported

# ---------------------------------------------------------------------------
# EMAIL (optional)
# If EMAIL_ENABLED is False, the script just prints new matches to the
# terminal and appends them to the CSV — no email setup required.
# ---------------------------------------------------------------------------
EMAIL_ENABLED = True

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = "michaelouru2@gmail.com"
SMTP_PASSWORD_ENV_VAR = "OPPORTUNITY_SCANNER_SMTP_PASSWORD"  # env var name, not the password itself
EMAIL_FROM = "michaelouru2@gmail.com"
EMAIL_TO = "michaelouru2@gmail.com"
EMAIL_SUBJECT = "New eLearning/EdTech opportunities found"

# ---------------------------------------------------------------------------
# NETWORK
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 (compatible; OpportunityScanner/1.0; personal use)"