"""Minimal Supabase REST client for upserts, no external SDK dependency."""
import os
import sys
import time
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _headers(prefer: str) -> dict:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        sys.exit(1)
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def upsert(table: str, rows: list, on_conflict: str, batch_size: int = 500, retries: int = 3) -> int:
    """Upsert rows into a Supabase table via PostgREST. Returns count of rows sent."""
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    headers = _headers("resolution=merge-duplicates,return=minimal")
    sent = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        for attempt in range(1, retries + 1):
            resp = requests.post(url, json=batch, headers=headers, timeout=60)
            if resp.status_code in (200, 201, 204):
                sent += len(batch)
                break
            if attempt == retries:
                print(f"ERROR upserting into {table}: {resp.status_code} {resp.text[:1000]}", file=sys.stderr)
                resp.raise_for_status()
            else:
                wait = 2 ** attempt
                print(f"Retrying {table} batch after error {resp.status_code} (attempt {attempt}/{retries}), sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
    return sent


def count_rows(table: str, filter_qs: str = "") -> int:
    """Return the row count for a table (optionally filtered) via PostgREST count header."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?select=id"
    if filter_qs:
        url += f"&{filter_qs}"
    headers = _headers("count=exact")
    headers["Range"] = "0-0"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    content_range = resp.headers.get("content-range", "0/0")
    total = content_range.split("/")[-1]
    return int(total) if total.isdigit() else 0
