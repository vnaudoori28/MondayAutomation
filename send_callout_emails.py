#!/usr/bin/env python3
"""
monday.com Callout Email Notifier
Reads the exported CSV, finds all @mention callouts in the Updates column,
and sends one email per callout to sprint@authentica.us.monday.com via Zoho SMTP.

Deduplication: a sent_log.json file tracks hashes of already-sent callouts
so the same callout is never emailed twice across daily runs.
"""

import os
import csv
import re
import json
import hashlib
import smtplib
import glob
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── Configuration (set these as GitHub Actions secrets / env vars) ──────────
SMTP_HOST     = "smtp.zoho.com"
SMTP_PORT     = 587
SMTP_USER     = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
FROM_EMAIL    = os.environ["SMTP_USER"]
TO_EMAIL      = "sprint@authentica.us.monday.com"
OUTPUT_DIR    = os.environ.get("OUTPUT_DIR", "exports")
SENT_LOG      = os.environ.get("SENT_LOG", "exports/sent_log.json")
# ────────────────────────────────────────────────────────────────────────────

# Matches @Firstname Lastname (two capitalised words) then captures
# everything after as the task.
CALLOUT_PATTERN = re.compile(
    r'@([A-Z][^\s@][^\s]*(?:\s+[A-Z][^\s@][^\s]*)?)\s+([\s\S]+?)(?=\s*@[A-Z]|\s*$)'
)


# ── Deduplication helpers ────────────────────────────────────────────────────

def load_sent_log(path: str) -> set:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent_log(path: str, sent: set) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(sent), f, indent=2)


def callout_hash(item_name: str, date: str, author: str, mention: str, task: str) -> str:
    key = f"{item_name}|{date}|{author}|{mention}|{task.strip()}"
    return hashlib.sha256(key.encode()).hexdigest()


# ── CSV helpers ──────────────────────────────────────────────────────────────

def find_latest_csv(output_dir: str) -> str:
    files = glob.glob(os.path.join(output_dir, "*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV files found in '{output_dir}'")
    return max(files, key=os.path.getmtime)


# ── Callout extraction ───────────────────────────────────────────────────────

def extract_callouts(updates_text: str) -> list:
    """
    Parse the Updates cell and extract all @mention callouts.
    Each update entry looks like: [2026-03-01 Author Name] update body | [...]
    Returns list of {date, author, mention, task}
    """
    callouts = []
    entries = updates_text.split(" | ")

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        header_match = re.match(r'\[(\d{4}-\d{2}-\d{2})\s+(.+?)\]\s*([\s\S]*)', entry)
        if not header_match:
            continue

        date   = header_match.group(1)
        author = header_match.group(2).strip()
        body   = header_match.group(3).strip()

        # Normalise newlines to spaces for cleaner matching
        body_flat = re.sub(r'\s+', ' ', body)

        for m in CALLOUT_PATTERN.finditer(body_flat):
            mention = m.group(1).strip()
            task    = m.group(2).strip()
            if mention and task:
                callouts.append({
                    "date":    date,
                    "author":  author,
                    "mention": mention,
                    "task":    task,
                })

    return callouts


# ── Email sending ────────────────────────────────────────────────────────────

def send_email(item_name: str, group: str, callout: dict) -> None:
    """Send a single callout email via Zoho SMTP."""
    mention = callout["mention"]
    task    = callout["task"]
    author  = callout["author"]
    date    = callout["date"]

    # Subject: Item Name | @Mention task description
    subject = f"{item_name} | @{mention} {task}"

    body = f"""Hi,

A callout was posted in monday.com that requires attention.

  Program / Task : {item_name}
  Group          : {group}
  Posted by      : {author}
  Date           : {date}

  Callout        : @{mention} {task}

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


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.utcnow().isoformat()}] Starting callout email notifier...")

    csv_path = find_latest_csv(OUTPUT_DIR)
    print(f"  Reading  : {csv_path}")
    print(f"  Sent log : {SENT_LOG}")

    sent_hashes = load_sent_log(SENT_LOG)
    print(f"  Already sent: {len(sent_hashes)} callout(s) on record")

    total_callouts = 0
    total_sent     = 0
    total_skipped  = 0

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
            callouts  = extract_callouts(updates_text)
            total_callouts += len(callouts)

            for callout in callouts:
                chash = callout_hash(
                    item_name,
                    callout["date"],
                    callout["author"],
                    callout["mention"],
                    callout["task"],
                )

                if chash in sent_hashes:
                    total_skipped += 1
                    print(f"  Skipping : @{callout['mention']} on '{item_name}' (already sent)")
                    continue

                print(f"  Sending  : [{item_name}] @{callout['mention']} — {callout['task'][:50]}...")
                try:
                    send_email(item_name, group, callout)
                    sent_hashes.add(chash)
                    total_sent += 1
                    print(f"             ✓ Sent to {TO_EMAIL}")
                except Exception as e:
                    print(f"             ✗ Failed: {e}")

    save_sent_log(SENT_LOG, sent_hashes)

    print(f"\n  Callouts found : {total_callouts}")
    print(f"  Emails sent    : {total_sent}")
    print(f"  Skipped (dupes): {total_skipped}")
    print("Done.")


if __name__ == "__main__":
    main()
