"""
Minimal X (Twitter) API v2 client — App-Only Bearer token auth.

Uses the recent-search endpoint (GET /2/tweets/search/recent) with a
`from:handle1 OR from:handle2 OR ...` query built from the curated account
list, rather than one call per account — much cheaper per the ~$0.005/read
2026 pricing the spec priced this out against.
"""
import os
import sys
import time
import requests

X_API_BASE = "https://api.x.com/2"
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")


def _headers() -> dict:
    if not X_BEARER_TOKEN:
        print("ERROR: X_BEARER_TOKEN must be set", file=sys.stderr)
        sys.exit(1)
    return {"Authorization": f"Bearer {X_BEARER_TOKEN}"}


def build_query(handles: list) -> str:
    or_clause = " OR ".join(f"from:{h}" for h in handles)
    return f"({or_clause}) -is:retweet -is:reply"


def search_recent(handles: list, start_time_iso: str = None, max_pages: int = 3, retries: int = 3) -> list:
    """
    Returns a flat list of tweets: [{id, text, author_id, author_username,
    created_at}, ...], newest first per X's default ordering.
    """
    if not handles:
        return []

    query = build_query(handles)
    params = {
        "query": query,
        "max_results": 100,
        "tweet.fields": "created_at,author_id",
        "expansions": "author_id",
        "user.fields": "username",
    }
    if start_time_iso:
        params["start_time"] = start_time_iso

    tweets = []
    users_by_id = {}
    next_token = None
    pages = 0

    while pages < max_pages:
        pages += 1
        req_params = dict(params)
        if next_token:
            req_params["next_token"] = next_token

        data = None
        for attempt in range(1, retries + 1):
            resp = requests.get(
                f"{X_API_BASE}/tweets/search/recent",
                headers=_headers(),
                params=req_params,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                break
            if resp.status_code == 429 and attempt < retries:
                wait = 15 * attempt
                print(f"X API rate limited, sleeping {wait}s (attempt {attempt}/{retries})", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code in (401, 402, 403):
                # 402/403 is what a $0-credit developer account should expect
                # until credits are loaded — surface that plainly rather than
                # retrying a request that will never succeed.
                print(
                    f"ERROR: X API returned {resp.status_code} — check X_BEARER_TOKEN and "
                    f"that the developer account has credits loaded. Body: {resp.text[:500]}",
                    file=sys.stderr,
                )
                resp.raise_for_status()
            print(f"ERROR calling X API: {resp.status_code} {resp.text[:500]}", file=sys.stderr)
            resp.raise_for_status()

        if data is None:
            break

        for u in data.get("includes", {}).get("users", []):
            users_by_id[u["id"]] = u.get("username")

        for t in data.get("data", []):
            tweets.append(
                {
                    "id": t.get("id"),
                    "text": t.get("text"),
                    "author_id": t.get("author_id"),
                    "author_username": users_by_id.get(t.get("author_id")),
                    "created_at": t.get("created_at"),
                }
            )

        next_token = data.get("meta", {}).get("next_token")
        if not next_token:
            break

    return tweets
