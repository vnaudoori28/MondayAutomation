#!/usr/bin/env python3
"""
monday.com Board Exporter
Fetches all items (including update threads) from a board via GraphQL API
and saves them as a CSV file.
"""

import os
import csv
import requests
from datetime import datetime

# ── Configuration (set these as GitHub Actions secrets / env vars) ──────────
API_TOKEN  = os.environ["MONDAY_API_TOKEN"]   # monday.com API v2 token
BOARD_ID   = os.environ["MONDAY_BOARD_ID"]    # numeric board ID
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "exports")  # folder to save CSVs
# ────────────────────────────────────────────────────────────────────────────

API_URL = "https://api.monday.com/v2"
HEADERS = {
    "Authorization": API_TOKEN,
    "Content-Type": "application/json",
    "API-Version": "2024-01",
}


def fetch_board_items(board_id: str):
    """Fetch all items from a board, handling pagination."""
    all_items = []
    cursor = None
    board_name = ""

    while True:
        if cursor:
            query = """
            query ($cursor: String!) {
              next_items_page(limit: 500, cursor: $cursor) {
                cursor
                items {
                  id
                  name
                  state
                  created_at
                  updated_at
                  group { title }
                  column_values {
                    id
                    column { title }
                    text
                  }
                }
              }
            }
            """
            variables = {"cursor": cursor}
            resp_key = "next_items_page"
        else:
            query = """
            query ($board_id: ID!) {
              boards(ids: [$board_id]) {
                name
                items_page(limit: 500) {
                  cursor
                  items {
                    id
                    name
                    state
                    created_at
                    updated_at
                    group { title }
                    column_values {
                      id
                      column { title }
                      text
                    }
                  }
                }
              }
            }
            """
            variables = {"board_id": board_id}
            resp_key = None

        response = requests.post(
            API_URL,
            headers=HEADERS,
            json={"query": query, "variables": variables},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")

        if resp_key == "next_items_page":
            page = data["data"]["next_items_page"]
        else:
            board = data["data"]["boards"][0]
            board_name = board["name"]
            page = board["items_page"]

        all_items.extend(page["items"])
        cursor = page.get("cursor")
        print(f"  Fetched {len(all_items)} items so far...")

        if not cursor:
            break

    return board_name, all_items


def fetch_updates_for_items(item_ids: list) -> dict:
    """
    Fetch the update/comment threads for each item.
    Returns {item_id: "formatted updates string"}.
    Batches requests (50 items at a time) to avoid API limits.
    """
    updates_map = {}
    batch_size = 50

    for i in range(0, len(item_ids), batch_size):
        batch = item_ids[i : i + batch_size]
        query = """
        query ($ids: [ID!]!) {
          items(ids: $ids) {
            id
            updates(limit: 100) {
              text_body
              created_at
              creator { name }
            }
          }
        }
        """
        response = requests.post(
            API_URL,
            headers=HEADERS,
            json={"query": query, "variables": {"ids": batch}},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            print(f"  Warning: errors fetching updates: {data['errors']}")
            continue

        for item in data["data"]["items"]:
            parts = []
            for u in reversed(item.get("updates", [])):  # oldest first
                creator = (u.get("creator") or {}).get("name", "Unknown")
                ts = (u.get("created_at") or "")[:10]
                body = (u.get("text_body") or "").strip()
                if body:
                    parts.append(f"[{ts} {creator}] {body}")
            updates_map[item["id"]] = " | ".join(parts)

    return updates_map


def flatten_item(item: dict, updates_map: dict) -> dict:
    """Flatten a monday.com item into a plain dict for CSV writing."""
    row = {
        "Item ID":    item["id"],
        "Item Name":  item["name"],
        "Group":      item.get("group", {}).get("title", ""),
        "State":      item.get("state", ""),
        "Created At": item.get("created_at", ""),
        "Updated At": item.get("updated_at", ""),
    }
    for cv in item.get("column_values", []):
        col_title = cv["column"]["title"]
        row[col_title] = cv.get("text") or ""

    # Updates column: all comments joined with " | " separator
    row["Updates"] = updates_map.get(item["id"], "")

    return row


def export_to_csv(board_name: str, items: list, updates_map: dict, output_dir: str):
    """Write items to a timestamped CSV file."""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() else "_" for c in board_name)
    filename = f"{safe_name}_{timestamp}.csv"
    filepath = os.path.join(output_dir, filename)

    if not items:
        print("No items found on board. CSV will not be created.")
        return None

    # Collect all column headers preserving order
    all_keys = []
    seen = set()
    for item in items:
        for key in flatten_item(item, updates_map):
            if key not in seen:
                all_keys.append(key)
                seen.add(key)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow(flatten_item(item, updates_map))

    return filepath


def main():
    print(f"[{datetime.utcnow().isoformat()}] Starting monday.com board export...")
    print(f"  Board ID : {BOARD_ID}")

    board_name, items = fetch_board_items(BOARD_ID)
    print(f"  Board    : {board_name}")
    print(f"  Items    : {len(items)}")

    print("  Fetching updates/comments...")
    item_ids = [item["id"] for item in items]
    updates_map = fetch_updates_for_items(item_ids)
    print(f"  Updates fetched for {len(updates_map)} items.")

    filepath = export_to_csv(board_name, items, updates_map, OUTPUT_DIR)
    if filepath:
        print(f"  Saved to : {filepath}")
    print("Done.")


if __name__ == "__main__":
    main()
