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


def upsert(
    table: str,
    rows: list,
    on_conflict: str,
    batch_size: int = 200,
    retries: int = 4,
    timeout: int = 120,
    resolution: str = "merge-duplicates",
) -> int:
    """Upsert rows into a Supabase table via PostgREST. Returns count of rows sent.

    Retries on both bad HTTP status codes AND network-level exceptions
    (timeouts, connection resets) — a request that never got a response
    still needs the same retry/backoff treatment as a 5xx.

    resolution: "merge-duplicates" (default — overwrite on conflict, used by
    the CFB pipeline's refreshable cache tables) or "ignore-duplicates" (skip
    silently on conflict — used by the news pipeline so a re-poll of an
    overlapping time window can't clobber a human's approve/reject decision
    on an already-stored news item).
    """
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    headers = _headers(f"resolution={resolution},return=minimal")
    sent = 0
    total_batches = (len(rows) + batch_size - 1) // batch_size
    for batch_num, i in enumerate(range(0, len(rows), batch_size), 1):
        batch = rows[i : i + batch_size]
        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(url, json=batch, headers=headers, timeout=timeout)
            except requests.exceptions.RequestException as exc:
                if attempt == retries:
                    print(
                        f"ERROR upserting into {table} (batch {batch_num}/{total_batches}): "
                        f"network error after {retries} attempts: {exc}",
                        file=sys.stderr,
                    )
                    raise
                wait = 2 ** attempt
                print(
                    f"Retrying {table} batch {batch_num}/{total_batches} after network error "
                    f"({attempt}/{retries}): {exc} — sleeping {wait}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue

            if resp.status_code in (200, 201, 204):
                sent += len(batch)
                if batch_num % 10 == 0 or batch_num == total_batches:
                    print(f"  {table}: upserted batch {batch_num}/{total_batches} ({sent} rows so far)")
                break
            if attempt == retries:
                print(f"ERROR upserting into {table}: {resp.status_code} {resp.text[:1000]}", file=sys.stderr)
                resp.raise_for_status()
            else:
                wait = 2 ** attempt
                print(f"Retrying {table} batch {batch_num}/{total_batches} after error {resp.status_code} (attempt {attempt}/{retries}), sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
    return sent


def select(table: str, params: dict, timeout: int = 30, retries: int = 3) -> list:
    """
    Simple read via PostgREST. `params` is passed straight through as query
    params (e.g. {"select": "id,status", "status": "eq.pending"}).
    """
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = _headers("return=representation")
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            if attempt == retries:
                print(f"ERROR reading {table}: network error after {retries} attempts: {exc}", file=sys.stderr)
                raise
            wait = 2 ** attempt
            print(f"Retrying read of {table} ({attempt}/{retries}) after: {exc} — sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if resp.status_code == 200:
            return resp.json()
        if attempt == retries:
            print(f"ERROR reading {table}: {resp.status_code} {resp.text[:500]}", file=sys.stderr)
            resp.raise_for_status()
        wait = 2 ** attempt
        time.sleep(wait)
    return []


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
