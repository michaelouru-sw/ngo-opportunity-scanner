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

# ---------------------------------------------------------------------------
# SOURCES
# Two kinds of sources are supported:
#
# 1. "reliefweb_api" — uses ReliefWeb's public jobs API directly (most
#    reliable, no scraping fragility).
#
# 2. "generic_html" — fetches a listings/search page and looks for links
#    whose visible text contains any KEYWORD. Works for most WordPress-style
#    job boards and simple career pages. Add as many as you like.
#
# NOTE: HTML-scraped sources can break if a site redesigns its page. If a
# source stops returning results, check its URL still works in a browser
# and adjust CSS_SELECTOR if needed (see README.md).
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "name": "ReliefWeb Jobs",
        "type": "reliefweb_api",
        "url": "https://api.reliefweb.int/v1/jobs",
        "appname": "ngo-elearning-scanner",  # required by ReliefWeb API, any string works
    },
    {
        "name": "Corporate Staffing Kenya",
        "type": "generic_html",
        "url": "https://www.corporatestaffing.co.ke/?s=instructional+design",
        "css_selector": "a",  # scans all links on the search-results page
    },
    {
        "name": "Corporate Staffing Kenya (LMS)",
        "type": "generic_html",
        "url": "https://www.corporatestaffing.co.ke/?s=learning+management+system",
        "css_selector": "a",
    },
    {
        "name": "APHRC Careers",
        "type": "generic_html",
        "url": "https://aphrc.org/careers/",
        "css_selector": "a",
    },
    {
        "name": "ALSF Procurement",
        "type": "generic_html",
        "url": "https://www.alsf.int/en/procurement-notices",
        "css_selector": "a",
    },
    {
        "name": "UNTalent (UN system consultancies)",
        "type": "generic_html",
        "url": "https://untalent.org/jobs?search=instructional+design",
        "css_selector": "a",
    },
    {
        "name": "Fuzu Kenya",
        "type": "generic_html",
        "url": "https://www.fuzu.com/kenya/search?q=instructional%20design",
        "css_selector": "a",
    },
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
#
# To enable email, set EMAIL_ENABLED = True and fill in the SMTP details.
# For Gmail: use an "App Password" (not your normal password) —
# https://myaccount.google.com/apppasswords
# It's recommended to set SMTP_PASSWORD via an environment variable
# instead of typing it here — see README.md.
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
