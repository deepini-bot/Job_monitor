"""Job monitor: checks company careers pages and emails you about new jobs.

Usage:
    python main.py              # run forever, check every N hours (config.yaml)
    python main.py --once       # check once and exit (good for cron / Task Scheduler)
    python main.py --test-email # send a test email and exit
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from monitor.discovery import discover_jobs
from monitor.notifier import send_email, send_new_jobs_email
from monitor.storage import Storage

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("job_monitor")


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("check_interval_hours", 3)
    cfg.setdefault("database_path", "jobs.db")
    cfg.setdefault("notify_on_first_run", False)
    cfg.setdefault("max_concurrent_companies", 5)
    cfg.setdefault("default_keywords", [])
    cfg.setdefault("companies", [])
    return cfg


def matches_keywords(job, keywords):
    if not keywords:
        return True
    title = job.title.lower()
    return any(k.lower() in title for k in keywords)


def fetch_company_jobs(company, cfg):
    name = company.get("name", "<unnamed company>")
    url = company.get("careers_url")
    if not url:
        log.error("[%s] missing careers_url; skipping company", name)
        return None

    try:
        return discover_jobs(url, render_js=company.get("render_js", False))
    except Exception as e:
        log.error("[%s] could not fetch jobs: %s", name, e)
        return None


def process_company_jobs(company, jobs, db, notify_on_first_run, cfg):
    name = company["name"]
    keywords = company.get("keywords") or cfg.get("default_keywords", [])
    jobs = [j for j in jobs if matches_keywords(j, keywords)]
    if not jobs:
        log.warning(
            "[%s] no jobs found. Check the URL, add keywords less strictly, "
            "or set render_js: true if the page needs JavaScript.", name
        )
        return

    first_run = not db.has_company(name)
    known = db.known_ids(name)
    new_jobs = [j for j in jobs if j.job_id not in known]

    if not new_jobs:
        log.info("[%s] %d jobs, nothing new", name, len(jobs))
        return

    if first_run and not notify_on_first_run:
        db.save_jobs(name, new_jobs)
        log.info("[%s] first run: saved %d jobs as baseline (no email)", name, len(new_jobs))
        return

    log.info("[%s] %d new job(s)", name, len(new_jobs))
    try:
        send_new_jobs_email(name, new_jobs)
    except Exception as e:
        # Not saved, so we retry the email on the next run.
        log.error("[%s] email failed, will retry next run: %s", name, e)
        return
    db.save_jobs(name, new_jobs)


def run_once(cfg):
    db = Storage(BASE_DIR / cfg["database_path"])
    try:
        companies = cfg["companies"]
        max_workers = int(cfg.get("max_concurrent_companies", 5))
        if max_workers < 1:
            raise ValueError("max_concurrent_companies must be at least 1")
        log.info(
            "Fetching %d companies with up to %d workers",
            len(companies), max_workers,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_company_jobs, company, cfg): company
                for company in companies
            }
            for future in as_completed(futures):
                company = futures[future]
                jobs = future.result()
                if jobs is not None:
                    process_company_jobs(
                        company, jobs, db, cfg["notify_on_first_run"], cfg
                    )
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--test-email", action="store_true", help="send a test email and exit")
    parser.add_argument("--config", default=str(BASE_DIR / "config.yaml"))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(BASE_DIR / "monitor.log", encoding="utf-8"),
        ],
    )
    load_dotenv(BASE_DIR / ".env")

    if args.test_email:
        send_email("Job monitor test", "Email setup works.")
        log.info("Test email sent.")
        return

    cfg = load_config(args.config)
    if not cfg["companies"]:
        log.error("No companies in config.yaml")
        return

    if args.once:
        run_once(cfg)
        log.info("One-time run completed.")
        return

    interval = float(cfg["check_interval_hours"]) * 3600
    log.info("Started. Checking every %s hour(s).", cfg["check_interval_hours"])
    while True:
        try:
            run_once(cfg)
            log.info("Run finished successfully at %s", time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            log.exception("Unexpected error in run")
        time.sleep(interval)


if __name__ == "__main__":
    main()
