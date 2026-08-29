"""
Sync cfb_player_season_stats from CFBD's /stats/player/season endpoint.

Lower priority per the Aug 29 2026 decision to keep CFB predictions
qualitative rather than building a full projection engine — this table is
useful for occasional stat callouts in reasoning text, not a core input.

CFBD returns this endpoint in LONG format: one row per (player, statType),
e.g. a QB's ATT, COMPLETIONS, YDS, TD are four separate rows. This script
pivots into one row per (season, player_id, category), with a `stats`
jsonb blob like {"ATT": 87, "COMPLETIONS": 54, "YDS": 612, "TD": 5}.
"""
import argparse
import datetime
import sys

from cfbd_client import get as cfbd_get
from supabase_client import upsert


def fetch_player_season_stats(year: int, season_type: str) -> list:
    return cfbd_get("/stats/player/season", {"year": year, "seasonType": season_type})


def coerce_number(value):
    try:
        if isinstance(value, str) and "." in value:
            return float(value)
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value


def pivot(records: list, season: int) -> list:
    groups = {}
    for r in records:
        player_id = r.get("playerId")
        category = r.get("category")
        if player_id is None or category is None:
            continue
        key = (player_id, category)
        if key not in groups:
            groups[key] = {
                "season": season,
                "player_id": str(player_id),
                "player_name": r.get("player"),
                "team": r.get("team"),
                "position": None,
                "category": category,
                "stats": {},
                "last_synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        stat_type = r.get("statType")
        if stat_type:
            groups[key]["stats"][stat_type] = coerce_number(r.get("stat"))
    return list(groups.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=datetime.datetime.now().year)
    parser.add_argument("--season-type", default="regular")
    args = parser.parse_args()

    print(f"Fetching {args.year} {args.season_type} player season stats from CFBD...")
    records = fetch_player_season_stats(args.year, args.season_type)
    print(f"  got {len(records)} long-format rows")

    rows = pivot(records, args.year)
    print(f"  pivoted into {len(rows)} (player, category) rows")

    sent = upsert("cfb_player_season_stats", rows, on_conflict="season,player_id,category")
    print(f"Done. Upserted {sent} rows into cfb_player_season_stats.")
    if sent == 0:
        print(
            "WARNING: zero rows synced — this is expected very early in the "
            "season (2026 stats are thin as of Aug 29) but double-check if "
            "it persists.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
