"""
Stage 1+2+3 of the news pipeline spec, in one script: monitor X for the
curated account list, filter/summarize each new post with Claude, and
store everything in `news_items` (single source of truth for the news
feed, newsletter pool, and prediction updates).

Non-negotiables from the spec, enforced here:
- No auto-posting: every item lands with status='pending'. Nothing in this
  script ever sets status to 'approved' — that's a human decision (Twilio
  approval flow, not yet built; until then, approve/reject directly in
  Supabase).
- No silent edits: this script never writes to `predictions`. A post that
  plausibly affects a locked prediction gets a *draft* row in
  `prediction_updates` for review — the original prediction/reasoning is
  untouched.
- Idempotent: re-running (including overlapping poll windows) never
  duplicates a news item or a draft update, via source_tweet_id / news_item_id
  unique constraints and ignore-duplicates upserts.
"""
import argparse
import datetime
import os
import sys
import uuid

from anthropic_client import classify_tweet
from supabase_client import select, upsert
from x_client import search_recent

ACCOUNTS_FILE = os.path.join(os.path.dirname(__file__), "accounts.txt")


def load_accounts(path: str = ACCOUNTS_FILE) -> list:
    accounts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                accounts.append(line)
    return accounts


def fetch_locked_predictions() -> list:
    return select(
        "predictions",
        {"select": "id,game_or_player,prediction_type,call", "locked": "eq.true"},
    )


def already_seen_tweet_ids(tweet_ids: list) -> set:
    if not tweet_ids:
        return set()
    # PostgREST "in" filter: in.(id1,id2,...)
    in_list = ",".join(tweet_ids)
    rows = select(
        "news_items",
        {"select": "source_tweet_id", "source_tweet_id": f"in.({in_list})"},
    )
    return {r["source_tweet_id"] for r in rows if r.get("source_tweet_id")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lookback-minutes",
        type=int,
        default=35,
        help="How far back to search (should exceed the poll cron interval for overlap safety)",
    )
    parser.add_argument("--accounts-file", default=ACCOUNTS_FILE)
    args = parser.parse_args()

    accounts = load_accounts(args.accounts_file)
    if not accounts:
        print("No accounts configured in accounts.txt — nothing to monitor.", file=sys.stderr)
        sys.exit(1)
    print(f"Monitoring {len(accounts)} accounts: {', '.join(accounts)}")

    start_time = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=args.lookback_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Searching X for posts since {start_time}...")
    tweets = search_recent(accounts, start_time_iso=start_time)
    print(f"  got {len(tweets)} posts in the window")

    if not tweets:
        print("Done. Nothing new.")
        return

    seen = already_seen_tweet_ids([t["id"] for t in tweets if t.get("id")])
    new_tweets = [t for t in tweets if t.get("id") and t["id"] not in seen]
    print(f"  {len(new_tweets)} are new (skipping {len(tweets) - len(new_tweets)} already stored)")

    if not new_tweets:
        print("Done. Nothing new.")
        return

    locked_predictions = fetch_locked_predictions()
    print(f"  {len(locked_predictions)} locked predictions loaded for relevance matching")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    news_rows = []
    update_rows = []

    for t in new_tweets:
        source_account = t.get("author_username") or "unknown"
        classification = classify_tweet(t["text"], source_account, locked_predictions)

        news_id = str(uuid.uuid4())
        news_rows.append(
            {
                "id": news_id,
                "source_account": source_account,
                "source_tweet_id": t["id"],
                "raw_text": t["text"],
                "summary": classification["summary"] or None,
                "status": "pending",
                "published_to_feed": False,
                "newsletter_issue_id": None,
                "linked_prediction_id": classification["linked_prediction_id"],
                "created_at": now,
            }
        )

        if classification["is_relevant"] and classification["linked_prediction_id"] and classification["impact_note"]:
            update_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "prediction_id": classification["linked_prediction_id"],
                    "news_item_id": news_id,
                    "impact_note": classification["impact_note"],
                    "impact_graded": None,
                    "created_at": now,
                }
            )

    sent_news = upsert("news_items", news_rows, on_conflict="source_tweet_id", resolution="ignore-duplicates")
    print(f"Inserted {sent_news} new rows into news_items.")

    if update_rows:
        sent_updates = upsert(
            "prediction_updates", update_rows, on_conflict="news_item_id", resolution="ignore-duplicates"
        )
        print(f"Drafted {sent_updates} rows into prediction_updates for review.")
    else:
        print("No posts matched a locked prediction this run.")


if __name__ == "__main__":
    main()
