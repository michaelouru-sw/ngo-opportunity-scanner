# eLearning / EdTech Opportunity Scanner

A small script that checks a list of job/consultancy/procurement pages
for listings matching your keywords (instructional design, e-learning,
LMS, Moodle, etc.), and shows you only the ones it hasn't shown you
before. Runs on your own machine — nothing here depends on Claude or
any Anthropic service after you download it.

## What it does and doesn't do

- ✅ Checks ReliefWeb's official jobs API directly — global coverage, and the
  only source with a real closing date, so expired listings are excluded precisely
- ✅ Scans UNjobs, Devex, and Idealist (worldwide, not location-limited) plus
  any career/procurement pages you add, for keyword matches
- ✅ For scraped sources, opens each match's own page and looks for a
  deadline phrase — drops it if that date has passed, flags it if no date
  could be found at all, so you're not chasing closed roles
- ✅ Labels results as remote/global vs. "location not confirmed remote" —
  see `REQUIRE_REMOTE_SIGNAL` in config.py to hard-filter on this once you've
  reviewed a run or two
- ✅ Remembers what it's already shown you, so re-running only surfaces new items
- ✅ Logs everything it ever finds to a CSV you can open in Excel
- ✅ Optionally emails you a digest when there's something new
- ❌ Does **not** run by itself — you schedule it (instructions below) on your
  own computer or via GitHub Actions (already set up — see below)
- ❌ Can't see JavaScript-rendered listings on sites that load jobs dynamically
  without a plain HTML fallback (Idealist is the main risk here — it's included
  as best-effort but may return little). If a source stops finding matches,
  check troubleshooting below
- ❌ Deadline-detection on scraped sources is best-effort text matching, not
  guaranteed — always treat "deadline not found, verify manually" as exactly that

## 1. Install (one-time)

Requires Python 3.8+.

```bash
cd ngo-opportunity-scanner
pip install -r requirements.txt
```

## 2. Run it once manually

```bash
python3 scanner.py
```

First run will report everything it finds (nothing is "seen" yet).
After that, only genuinely new listings show up. Check `opportunities_log.csv`
in the same folder — that's your full running history.

## 3. Customize it

Open `config.py`:

- **KEYWORDS** — add/remove terms. Keep it specific enough to avoid noise.
- **EXCLUDE_KEYWORDS** — anything with these words gets filtered out even
  if it matches a keyword (e.g. "volunteer", "unpaid").
- **SOURCES** — the list of pages it checks. By default this is worldwide
  sources only (ReliefWeb, UNjobs, Devex, Idealist). Kenya-specific boards
  (Corporate Staffing, Fuzu) are included but commented out — remove the `#`
  in front of a block if you want local roles back in alongside the global ones.
  Add more sources by copying an existing `generic_html` entry.
- **REQUIRE_REMOTE_SIGNAL** — set to `True` to drop any scraped listing that
  doesn't explicitly mention "remote," "worldwide," etc. and isn't from an
  inherently global source. Leave `False` at first so you can see everything
  and judge for yourself — some remote roles never say the word "remote."
- **CHECK_DEADLINES_ON_DETAIL_PAGES / MAX_DETAIL_PAGE_CHECKS_PER_RUN** —
  controls the expired-listing filter for scraped sources. Raise the max if
  you add a lot more sources and want deeper checking per run (each check is
  one extra page fetch, so very high numbers slow the run down).

## 4. Schedule it to run daily

### Mac / Linux (cron)

```bash
crontab -e
```

Add this line (runs every day at 7:00 AM — adjust the time as you like):

```
0 7 * * * cd /full/path/to/ngo-opportunity-scanner && /usr/bin/python3 scanner.py >> cron.log 2>&1
```

Save and exit. Use `crontab -l` to confirm it's there.

### Windows (Task Scheduler)

1. Open **Task Scheduler** → **Create Basic Task**
2. Name it "Opportunity Scanner", set trigger to **Daily** at a time you like
3. Action: **Start a program**
   - Program/script: `python` (or the full path to `python.exe`)
   - Add arguments: `scanner.py`
   - Start in: the full path to the `ngo-opportunity-scanner` folder
4. Finish — it'll now run daily even if you're not logged in (depending on
   your power settings)

### Cloud option: GitHub Actions (no computer needed, free)

This folder already includes `.github/workflows/daily-scan.yml`, which
runs the scanner automatically every day in the cloud — your laptop can
be off, asleep, anywhere. GitHub gives every account free minutes for
this (a run takes well under a minute, so you won't come close to any
limit even on a private repo).

**Setup (one-time, ~10 minutes):**

1. **Create a repo.** On github.com, click "New repository". Name it
   anything (e.g. `opportunity-scanner`). Private is recommended since
   `config.py` will contain your email address.

2. **Push these files to it.** From inside this folder:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
   git push -u origin main
   ```
   (Replace the URL with the one GitHub shows you after creating the repo.)

3. **Add your email password as a secret** (never put it directly in
   `config.py`): repo page → **Settings** → **Secrets and variables** →
   **Actions** → **New repository secret**. Name it exactly:
   ```
   OPPORTUNITY_SCANNER_SMTP_PASSWORD
   ```
   and paste your Gmail App Password as the value.

4. **Turn on email in `config.py`:** set `EMAIL_ENABLED = True`, fill in
   `SMTP_USERNAME`, `EMAIL_FROM`, `EMAIL_TO` with your real address, then
   commit and push that change:
   ```bash
   git add config.py
   git commit -m "Enable email digest"
   git push
   ```

5. **Check it's scheduled.** Go to the repo's **Actions** tab — you'll see
   "Daily Opportunity Scan" listed. It runs automatically at 9:00 AM
   Nairobi time daily. To test it immediately rather than waiting, click
   into the workflow and press **Run workflow**.

6. **Where results show up:** if email is enabled, in your inbox. Either
   way, every run also commits the updated `opportunities_log.csv` back to
   the repo, so you can always open the repo on GitHub and see the full
   history in the file browser, no email required.

**To change the schedule:** edit the `cron:` line in
`.github/workflows/daily-scan.yml` (times are UTC; Nairobi is UTC+3, so
subtract 3 hours from your desired local time), commit, and push.

### Alternative always-on option

If you'd rather not use GitHub, the same script works unchanged on a
small always-on machine — a Raspberry Pi or a $5/month VPS (DigitalOcean,
Linode) with the cron setup above. Ask if you'd like help with that route
instead.

## 5. Optional: email digest

By default the script just prints results and logs them to CSV. To get
an email when something new turns up:

1. In `config.py`, set `EMAIL_ENABLED = True` and fill in `SMTP_USERNAME`,
   `EMAIL_FROM`, `EMAIL_TO`.
2. **Don't put your password in the file.** Instead, set it as an
   environment variable before running:

   Mac/Linux:
   ```bash
   export OPPORTUNITY_SCANNER_SMTP_PASSWORD="your-app-password"
   ```

   Windows (PowerShell):
   ```powershell
   $env:OPPORTUNITY_SCANNER_SMTP_PASSWORD="your-app-password"
   ```

   For cron/Task Scheduler to see this variable, add it to the scheduled
   task itself (cron: put the `export` line in the crontab command, or in
   a `.env` file you source; Task Scheduler: set it as a system
   environment variable in Windows settings so it's always available).

3. If using Gmail, generate an **App Password** (not your regular Gmail
   password): https://myaccount.google.com/apppasswords — this requires
   2-Step Verification to be turned on.

## Troubleshooting

**A source suddenly returns 0 matches when you know new jobs were posted:**
The site likely changed its page structure, or the jobs are loaded via
JavaScript after the page loads (which plain HTML fetching can't see).
Open the URL in a browser, view page source (Ctrl+U / Cmd+Option+U), and
check whether the job titles appear in the raw HTML. If not, that source
needs a different approach (browser automation) — let me know and I can
adapt it.

**Getting blocked / no results at all from a source:**
Some sites rate-limit or block automated requests. Increase the delay
between runs, or check if the site has an official API or RSS feed
instead (more reliable long-term than scraping).

**Duplicate entries in the CSV:**
The CSV logs everything found on *first* detection — it won't re-log the
same URL twice, but if a site changes a job's URL slightly, it may look
"new" again. This is a minor cosmetic issue, not a functional bug.

## Files in this folder

| File | Purpose |
|---|---|
| `scanner.py` | Main script — run this |
| `config.py` | Your settings — keywords, sources, email |
| `requirements.txt` | Python packages needed |
| `opportunities_log.csv` | Created after first run — full history |
| `seen_opportunities.json` | Created after first run — dedup memory |
