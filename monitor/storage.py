import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    company    TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    title      TEXT NOT NULL,
    url        TEXT,
    location   TEXT,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (company, job_id)
);
"""


class Storage:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def has_company(self, company):
        row = self.conn.execute(
            "SELECT 1 FROM jobs WHERE company = ? LIMIT 1", (company,)
        ).fetchone()
        return row is not None

    def known_ids(self, company):
        rows = self.conn.execute(
            "SELECT job_id FROM jobs WHERE company = ?", (company,)
        ).fetchall()
        return {r[0] for r in rows}

    def save_jobs(self, company, jobs):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.executemany(
            "INSERT OR IGNORE INTO jobs (company, job_id, title, url, location, first_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(company, j.job_id, j.title, j.url, j.location, now) for j in jobs],
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
