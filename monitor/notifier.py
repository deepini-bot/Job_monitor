import os
import smtplib
from email.message import EmailMessage
from html import escape

MAX_LISTED = 50  # max jobs shown in one email


def _settings():
    cfg = {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("EMAIL_USER"),
        "password": os.getenv("EMAIL_PASSWORD"),
        "to": os.getenv("EMAIL_TO"),
    }
    missing = [k for k in ("user", "password", "to") if not cfg[k]]
    if missing:
        raise RuntimeError(
            "Missing email settings in .env: " + ", ".join(missing)
        )
    return cfg


def send_email(subject, text_body, html_body=None):
    cfg = _settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
        server.starttls()
        server.login(cfg["user"], cfg["password"])
        server.send_message(msg)


def send_new_jobs_email(company, jobs):
    subject = f"{len(jobs)} new job(s) at {company}"

    text_lines = [f"New jobs at {company}:", ""]
    html_items = []
    for j in jobs[:MAX_LISTED]:
        loc = f" ({j.location})" if j.location else ""
        text_lines.append(f"- {j.title}{loc}\n  {j.url}")
        html_items.append(
            f'<li><a href="{escape(j.url)}">{escape(j.title)}</a>{escape(loc)}</li>'
        )

    if len(jobs) > MAX_LISTED:
        extra = len(jobs) - MAX_LISTED
        text_lines.append(f"...and {extra} more")
        html_items.append(f"<li>...and {extra} more</li>")

    html = (
        f"<p>New jobs at <b>{escape(company)}</b>:</p>"
        f"<ul>{''.join(html_items)}</ul>"
    )
    send_email(subject, "\n".join(text_lines), html)
