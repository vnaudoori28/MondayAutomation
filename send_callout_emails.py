#!/usr/bin/env python3
"""
monday.com Callout Email Notifier
Reads the exported CSV, finds all @mention callouts in the Updates column,
and sends one email per callout to sprint@authentica.us.monday.com via Zoho SMTP.
"""

import os
import csv
import re
import smtplib
import glob
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── Configuration (set these as GitHub Actions secrets / env vars) ──────────
SMTP_HOST     = "smtp.zoho.com"
SMTP_PORT     = 587
SMTP_USER     = os.environ["SMTP_USER"]       # your Zoho email address
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]   # your Zoho email password / app password
FROM_EMAIL    = os.environ["SMTP_USER"]
TO_EMAIL      = "sprint@authentica.us.monday.com"
OUTPUT_DIR    = os.environ.get("OUTPUT_DIR", "exports")
# ────────────────────────────────────────────────────────────────────────────

# Matches @Name (one or two words) followed by the rest of the callout text
# e.g. "@Vedanth Maheshwari Send revised contract..."
CALLOUT_PATTERN = re.compile(r'@([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+([^|@]+)')


def find_latest_csv(output_dir: str) -> str:
    """Return the most recently created CSV in the exports folder."""
    files = glob.glob(os.path.join(output_dir, "*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV files found in '{output_dir}'")
    return max(files, key=os.path.getctime)


def extract_callouts(updates_text: str) -> list[dict]:
    """
    Parse the Updates cell and extract all @mention callouts.
    Each update entry looks like: [2026-03-01 Author Name] update text | [...]
    Returns list of {date, author, mention, task}
    """
    callouts = []

    # Split individual update entries on the " | " separator
    entries = updates_text.split(" | ")

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        # Extract timestamp and author from the [date author] prefix
        header_match = re.match(r'\[(\d{4}-\d{2}-\d{2})\s+(.+?)\]\s*(.*)', entry)
        if not header_match:
            continue

        date     = header_match.group(1)
        author   = header_match.group(2).strip()
        body     = header_match.group(3).strip()

        # Find all @mentions in the body of this update
        for match in CALLOUT_PATTERN.finditer(body):
            mention = match.group(1) + (
                (" " + match.group(0).split()[1])
                if len(match.group(0).split()) > 1 and match.group(1).count(" ") == 0
                else ""
            )
            # Re-extract cleanly: full @Name + task text
            full_match = re.search(
                r'@(' + re.escape(match.group(1)) + r'(?:\s[A-Z][a-z]+)?)\s+([^|@]+)',
                body
            )
            if full_match:
                callouts.append({
                    "date":    date,
                    "author":  author,
                    "mention": full_match.group(1).strip(),
                    "task":    full_match.group(2).strip(),
                })

    return callouts


def send_email(item_name: str, group: str, callout: dict) -> None:
    """Send a single callout email via Zoho SMTP."""
    mention  = callout["mention"]
    task     = callout["task"]
    author   = callout["author"]
    date     = callout["date"]

    subject = f"Action Required: @{mention} — {task[:60]}{'...' if len(task) > 60 else ''}"

    body = f"""Hi,

A callout was posted in monday.com that requires attention:

  Board Item : {item_name}
  Group      : {group}
  Posted by  : {author}
  Date       : {date}

  Callout    : @{mention} {task}

Please action this at your earliest convenience.

---
This email was sent automatically from the monday.com daily board export.
"""

    msg = MIMEMultipart()
    msg["From"]    = FROM_EMAIL
    msg["To"]      = TO_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())


def main():
    print(f"[{datetime.utcnow().isoformat()}] Starting callout email notifier...")

    csv_path = find_latest_csv(OUTPUT_DIR)
    print(f"  Reading  : {csv_path}")

    total_callouts = 0
    total_sent     = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        if "Updates" not in (reader.fieldnames or []):
            print("  No 'Updates' column found in CSV. Nothing to do.")
            return

        for row in reader:
            updates_text = row.get("Updates", "").strip()
            if not updates_text:
                continue

            item_name = row.get("Item Name", "Unknown Item")
            group     = row.get("Group", "")

            callouts = extract_callouts(updates_text)
            total_callouts += len(callouts)

            for callout in callouts:
                print(f"  Sending  : @{callout['mention']} — {callout['task'][:50]}...")
                try:
                    send_email(item_name, group, callout)
                    total_sent += 1
                    print(f"             ✓ Sent to {TO_EMAIL}")
                except Exception as e:
                    print(f"             ✗ Failed: {e}")

    print(f"\n  Callouts found : {total_callouts}")
    print(f"  Emails sent    : {total_sent}")
    print("Done.")


if __name__ == "__main__":
    main()
