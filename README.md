# Job Monitor

Checks company careers pages every 3 hours and emails you when a new job shows up.

## Structure

```
job_monitor/
├── main.py              # entry point + scheduler loop
├── config.yaml          # your companies / careers URLs
├── .env                 # your email settings (copy from .env.example)
├── requirements.txt
└── monitor/
    ├── __init__.py
    ├── discovery.py     # auto-detects jobs from a careers URL
    ├── storage.py       # SQLite, remembers jobs already seen
    └── notifier.py      # sends the email
```

## Setup (VS Code terminal)

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac / Linux
pip install -r requirements.txt
```

1. Copy `.env.example` to `.env` and fill in your Gmail + app password.
   (Google Account > Security > 2-Step Verification on > App passwords.)
2. Put your careers page URLs in `config.yaml`.
3. Test email: `python main.py --test-email`
4. Test one check: `python main.py --once`
5. Run it: `python main.py` (checks every 3 hours while it's running)

## How it works

- Auto-discovery tries, in order: known job boards (Greenhouse, Lever, Ashby,
  Workable, SmartRecruiters, also when your company page embeds one), then
  schema.org JobPosting data, then job-looking links on the page.
- First run for a company saves all current jobs as a baseline without
  emailing. After that, only new jobs trigger an email.
- If a fetch or email fails, nothing is marked as seen, so it retries next run.

## Notes

- If a page loads jobs with JavaScript and nothing is found, set
  `render_js: true` for that company and run
  `pip install playwright && playwright install chromium`.
- The script only runs while your laptop is on. For always-on use, run
  `python main.py --once` from Windows Task Scheduler / cron every 3 hours,
  or host it on a small server.
- The link-heuristic fallback is a best guess and varies by site. Known job
  boards are the most reliable.
