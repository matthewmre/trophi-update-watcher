"""
Checks https://www.trophi.ai/updates for a new patch note.
Sends an email ONLY when the latest entry has changed since the last run.
State (the last-seen patch note) is stored in last_update.json, which the
GitHub Actions workflow commits back to the repo after each run.
"""

import json
import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.trophi.ai/updates"
STATE_FILE = Path("last_update.json")


def fetch_latest_update():
    resp = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if "/updates/" in href and "Patch Note" in text:
            candidates.append((text, href))

    if not candidates:
        raise RuntimeError(
            "Could not find any patch notes on the page - the site layout "
            "may have changed, so the script needs updating."
        )

    # The page lists updates newest-first, so the first match is the latest.
    title, link = candidates[0]
    full_url = link if link.startswith("http") else "https://www.trophi.ai" + link
    return title, full_url


def load_last_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_last_seen(title, url):
    STATE_FILE.write_text(json.dumps({"title": title, "url": url}, indent=2))


def send_email(subject, body):
    smtp_server = os.environ["SMTP_SERVER"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    # Supports one address or several comma-separated addresses in TO_EMAIL
    to_emails = [addr.strip() for addr in os.environ["TO_EMAIL"].split(",") if addr.strip()]

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_emails)

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_emails, msg.as_string())


def main():
    title, url = fetch_latest_update()
    last = load_last_seen()

    if last is None:
        # First-ever run: just record the current latest entry, no email.
        print(f"First run. Recording current latest update: {title}")
        save_last_seen(title, url)
        return

    if last.get("title") != title:
        print(f"New update found: {title}")
        send_email(
            subject=f"New Trophi.ai update: {title}",
            body=f"A new patch note just went live:\n\n{title}\n{url}",
        )
        save_last_seen(title, url)
    else:
        print("No new update.")


if __name__ == "__main__":
    main()
