"""
Sync cfb_rankings from CFBD's /rankings endpoint.

New dependency surfaced by the Aug 29 2026 Confidence Picks Board spec:
the CFB Top 25 board's upset flag needs AP Top 25 (and other poll) data
("flag as an upset when picking an unranked team over a ranked one, or a
lower-ranked team over a higher-ranked one"). Reuses the existing
CFBD_API_KEY and Supabase project — no new vendor.

CFBD's /rankings response is nested: a list of {season, week, seasonType,
polls: [{poll, ranks: [{rank, school, points, firstPlaceVotes}, ...]}]}.
This script flattens that into one row per (season, week, poll, team).
Stores every poll CFBD returns (AP Top 25, Coaches Poll, Playoff
Committee selections, etc.) — the schema's `poll` column already
discriminates between them, so the Confidence Picks board can filter to
AP Top 25 specifically without this script needing to hardcode that
choice.
"""
import argparse
import datetime
import sys

from cfbd_client import get as cfbd_get
from supabase_client import upsert


def fetch_rankings(year: int, season_type: str) -> list:
    return cfbd_get("/rankings", {"year": year, "seasonType": season_type})


def flatten(weeks: list) -> list:
    rows = []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for w in weeks:
        season = w.get("season")
        week = w.get("week")
        for poll in w.get("polls", []) or []:
            poll_name = poll.get("poll")
            if not poll_name:
                continue
            for entry in poll.get("ranks", []) or []:
                team = entry.get("school")
                rank = entry.get("rank")
                if team is None or rank is None:
                    continue
                rows.append(
                    {
                        "season": season,
                        "week": week,
                        "poll": poll_name,
                        "team": team,
                        "rank": rank,
                        "points": entry.get("points"),
                        "first_place_votes": entry.get("firstPlaceVotes"),
                        "last_synced_at": now,
                    }
                )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=datetime.datetime.now().year)
    parser.add_argument("--season-type", default="regular")
    args = parser.parse_args()

    print(f"Fetching {args.year} {args.season_type} rankings from CFBD...")
    weeks = fetch_rankings(args.year, args.season_type)
    print(f"  got {len(weeks)} week entries")

    rows = flatten(weeks)
    polls_seen = sorted({r["poll"] for r in rows})
    print(f"  flattened into {len(rows)} (season, week, poll, team) rows across polls: {polls_seen}")

    sent = upsert("cfb_rankings", rows, on_conflict="season,week,poll,team")
    print(f"Done. Upserted {sent} rows into cfb_rankings.")
    if sent == 0:
        print(
            "WARNING: zero rows synced — expected before the AP Top 25 releases "
            "its first poll of the season, but double-check if it persists.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
