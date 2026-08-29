"""
Sync cfb_games from CFBD's /games endpoint.

Notes from the Aug 29 2026 spec:
- CFBD has no separate "Week 0" — the season-opening slate (e.g. the Aug 29
  UNC-TCU game) is just part of CFBD's week=1. Querying week=0 silently
  returns the *entire season* instead of filtering, so this script never
  passes week=0 to the API. We don't pass `week` at all here — omitting it
  returns every week for the given year/seasonType, which is simpler and
  also lets one run refresh scores for already-played weeks.
- Runs weekly (or after game days) via GitHub Actions. Upserts by CFBD's
  own game id, so re-running is always safe.
"""
import argparse
import datetime
import sys

from cfbd_client import get as cfbd_get
from supabase_client import upsert


def fetch_games(year: int, season_type: str) -> list:
    return cfbd_get("/games", {"year": year, "seasonType": season_type})


def to_row(g: dict) -> dict:
    return {
        "id": g.get("id"),
        "season": g.get("season"),
        "week": g.get("week"),
        "season_type": g.get("seasonType"),
        "start_date": g.get("startDate"),
        "home_team": g.get("homeTeam"),
        "away_team": g.get("awayTeam"),
        "home_points": g.get("homePoints"),
        "away_points": g.get("awayPoints"),
        "completed": bool(g.get("completed")),
        "venue": g.get("venue"),
        "neutral_site": bool(g.get("neutralSite")),
        "last_synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=datetime.datetime.now().year)
    parser.add_argument(
        "--season-types",
        nargs="+",
        default=["regular", "postseason"],
        help="CFBD seasonType values to sync",
    )
    args = parser.parse_args()

    total = 0
    for season_type in args.season_types:
        print(f"Fetching {args.year} {season_type} games from CFBD...")
        games = fetch_games(args.year, season_type)
        print(f"  got {len(games)} games")
        rows = [to_row(g) for g in games if g.get("id") is not None]
        sent = upsert("cfb_games", rows, on_conflict="id")
        print(f"  upserted {sent} rows into cfb_games")
        total += sent

    print(f"Done. Total rows upserted: {total}")
    if total == 0:
        print("WARNING: zero rows synced — check API key / year / network", file=sys.stderr)


if __name__ == "__main__":
    main()
