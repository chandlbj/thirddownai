"""Minimal CollegeFootballData (CFBD) API client."""
import os
import sys
import time
import requests

CFBD_BASE_URL = "https://api.collegefootballdata.com"
CFBD_API_KEY = os.environ.get("CFBD_API_KEY", "")


def get(path: str, params: dict = None, retries: int = 3) -> list:
    if not CFBD_API_KEY:
        print("ERROR: CFBD_API_KEY must be set", file=sys.stderr)
        sys.exit(1)
    url = f"{CFBD_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {CFBD_API_KEY}", "Accept": "application/json"}
    for attempt in range(1, retries + 1):
        resp = requests.get(url, headers=headers, params=params or {}, timeout=60)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429 and attempt < retries:
            wait = 5 * attempt
            print(f"Rate limited on {path}, sleeping {wait}s (attempt {attempt}/{retries})", file=sys.stderr)
            time.sleep(wait)
            continue
        print(f"ERROR calling {path}: {resp.status_code} {resp.text[:1000]}", file=sys.stderr)
        resp.raise_for_status()
    return []
