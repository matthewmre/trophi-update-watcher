"""
Checks https://www.trophi.ai/updates for a new patch note.
Sends an email ONLY when the latest entry has changed since the last run.

The email now includes the actual contents of the patch note (pulled from the
individual update page), so you can read the changes without clicking through.

State (the last-seen patch note) is stored in last_update.json, which the
GitHub Actions workflow commits back to the repo after each run.
"""

import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

URL = "https://www.trophi.ai/updates"
STATE_FILE = Path("last_update.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (trophi-update-watcher)"}


def _slug_from_url(url):
    """The stable identifier for an update, e.g. '1-116-0' from
    https://www.trophi.ai/updates/1-116-0/ . Trailing slash is ignored."""
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1]


def fetch_latest_update():
    """Return a dict describing the newest update on the /updates page.

    The page lists updates newest-first as rows like:
        <a class="mxrow" href="1-116-0/">
            <b>1.116.0</b><span>Patch note</span><time>October 5, 2026</time>
        </a>
    """
    resp = requests.get(URL, timeout=30, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = soup.select("a.mxrow[href]")
    if not rows:
        raise RuntimeError(
            "Could not find any update rows (a.mxrow) on the page - the site "
            "layout may have changed again, so the script needs updating. "
            f"Checked: {URL}"
        )

    row = rows[0]  # newest first
    version_el = row.find("b")
    label_el = row.find("span")
    date_el = row.find("time")

    version = version_el.get_text(strip=True) if version_el else row.get_text(strip=True)
    label = label_el.get_text(strip=True) if label_el else "Update"
    date = date_el.get_text(strip=True) if date_el else ""

    full_url = urljoin(resp.url, row["href"])
    title = f"{version} — {label}" + (f" ({date})" if date else "")

    return {
        "slug": _slug_from_url(full_url),
        "version": version,
        "label": label,
        "date": date,
        "url": full_url,
        "title": title,
    }


def fetch_update_content(url):
    """Return (plain_text, inner_html) of the patch note body at `url`.

    The detail page holds the changes in <article class="mxart">. We keep the
    site's own HTML for the rich email, and build a readable plain-text version
    as a fallback.
    """
    resp = requests.get(url, timeout=30, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    article = soup.select_one("article.mxart") or soup.find("article")
    if article is None:
        return ("", "")

    # Plain-text rendering: headings and list items on their own lines.
    lines = []
    for el in article.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name == "li":
            lines.append(f"  - {text}")
        elif el.name.startswith("h"):
            lines.append(f"\n{text}")
        else:
            lines.append(text)
    plain = "\n".join(lines).strip()

    inner_html = article.decode_contents().strip()
    return (plain, inner_html)


def load_last_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_last_seen(update):
    STATE_FILE.write_text(json.dumps(update, indent=2))


def build_bodies(update, changes_text, changes_html):
    """Return (plain_text_body, html_body) for the notification email."""
    header = f"{update['title']}\n{update['url']}\n"
    if changes_text:
        text_body = f"A new patch note just went live:\n\n{header}\nWhat changed:\n\n{changes_text}\n"
    else:
        text_body = (
            f"A new patch note just went live:\n\n{header}\n"
            "(Couldn't extract the change details automatically - open the link above.)\n"
        )

    changes_block = changes_html or "<p>(Couldn't extract the change details automatically.)</p>"
    html_body = f"""\
<!DOCTYPE html>
<html>
  <body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.5;">
    <p style="margin:0 0 4px;">A new patch note just went live:</p>
    <h2 style="margin:0 0 2px;">{update['version']}</h2>
    <p style="margin:0 0 16px;color:#666;">{update['label']}{(' · ' + update['date']) if update['date'] else ''}</p>
    <div style="border-left:3px solid #e0e0e0;padding-left:16px;">
      {changes_block}
    </div>
    <p style="margin:20px 0 0;">
      <a href="{update['url']}" style="color:#0a66c2;">View this update on trophi.ai →</a>
    </p>
  </body>
</html>
"""
    return text_body, html_body


def send_email(subject, text_body, html_body):
    smtp_server = os.environ["SMTP_SERVER"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    # Supports one address or several comma-separated addresses in TO_EMAIL
    to_emails = [addr.strip() for addr in os.environ["TO_EMAIL"].split(",") if addr.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_emails)
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_emails, msg.as_string())


def main():
    update = fetch_latest_update()
    last = load_last_seen()

    # Identify an update by its slug (stable, and backward-compatible with the
    # old state file whose URL ended in the same slug).
    last_slug = _slug_from_url(last["url"]) if last and last.get("url") else None

    if last is None:
        print(f"First run. Recording current latest update: {update['title']}")
        save_last_seen(update)
        return

    if last_slug != update["slug"]:
        print(f"New update found: {update['title']}")
        changes_text, changes_html = fetch_update_content(update["url"])
        text_body, html_body = build_bodies(update, changes_text, changes_html)
        send_email(
            subject=f"New Trophi.ai update: {update['version']}"
            + (f" ({update['date']})" if update["date"] else ""),
            text_body=text_body,
            html_body=html_body,
        )
        save_last_seen(update)
    else:
        print("No new update.")


if __name__ == "__main__":
    main()
