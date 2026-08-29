"""
Sync cfb_roster from CFBD's /roster endpoint.

Per spec: refreshed far less often than games/stats (roster composition
doesn't change game-to-game) — run once per season, or on-demand via
workflow_dispatch if something changes (a transfer, a depth-chart shift).

CFBD's /roster endpoint returns every FBS team's roster when called with
just `year`. As a safety net, if that comes back suspiciously small (fewer
than a normal single team's roster would suggest full coverage failed), we
fall back to iterating team-by-team using the FBS team list.
"""
import argparse
import datetime
import sys
import time

from cfbd_client import get as cfbd_get
from supabase_client import upsert

MIN_EXPECTED_PLAYERS = 3000  # ~130 FBS teams * ~85 roster spots, generous floor


def fetch_full_roster(year: int) -> list:
    return cfbd_get("/roster", {"year": year})


def fetch_fbs_teams(year: int) -> list:
    teams = cfbd_get("/teams/fbs", {"year": year})
    return [t.get("school") for t in teams if t.get("school")]


def fetch_roster_by_team(year: int, team: str) -> list:
    return cfbd_get("/roster", {"year": year, "team": team})


def to_row(p: dict, season: int) -> dict:
    player_id = p.get("id")
    if player_id is None:
        return None
    return {
        "id": str(player_id),
        "team": p.get("team"),
        "season": season,
        "first_name": p.get("firstName"),
        "last_name": p.get("lastName"),
        "position": p.get("position"),
        "class_year": p.get("year"),
        "home_city": p.get("homeCity"),
        "home_state": p.get("homeState"),
        "last_synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=datetime.datetime.now().year)
    args = parser.parse_args()
    year = args.year

    print(f"Fetching {year} full roster from CFBD...")
    players = fetch_full_roster(year)
    print(f"  got {len(players)} players from bulk /roster?year=")

    if len(players) < MIN_EXPECTED_PLAYERS:
        print(
            f"  bulk roster looks incomplete (<{MIN_EXPECTED_PLAYERS}), "
            "falling back to per-team fetch...",
            file=sys.stderr,
        )
        teams = fetch_fbs_teams(year)
        print(f"  found {len(teams)} FBS teams for {year}")
        players = []
        for i, team in enumerate(teams, 1):
            team_players = fetch_roster_by_team(year, team)
            players.extend(team_players)
            if i % 20 == 0:
                print(f"    ...{i}/{len(teams)} teams fetched, {len(players)} players so far")
            time.sleep(0.2)  # be polite to the API
        print(f"  per-team fallback got {len(players)} total players")

    rows = [r for r in (to_row(p, year) for p in players) if r is not None]
    sent = upsert("cfb_roster", rows, on_conflict="id,season")
    print(f"Done. Upserted {sent} rows into cfb_roster for season {year}.")
    if sent == 0:
        print("WARNING: zero rows synced — check API key / year / network", file=sys.stderr)


if __name__ == "__main__":
    main()
