# Third Down AI — CFB Data Pipeline

Ingests CollegeFootballData (CFBD) data into Supabase on a schedule via GitHub Actions.

## Tables

- `cfb_games` — schedule/results cache. Synced daily (idempotent upsert by CFBD game id).
- `cfb_roster` — depth-chart/biographical context. Synced monthly (roster barely changes game-to-game); trigger manually after a transfer or depth-chart shift.
- `cfb_player_season_stats` — lower priority; pivoted from CFBD's long-format player stats into one row per (season, player, category) with a `stats` jsonb blob. Synced weekly.

CFB predictions themselves stay qualitative/reasoning-based (see the CFB pipeline spec) — `cfb_games` and `cfb_roster` are the tables actually used for that reasoning; `cfb_player_season_stats` is a supplementary stat-callout source.

## Required GitHub Actions secrets

Set these under Settings → Secrets and variables → Actions → Secrets:

- `CFBD_API_KEY` — CollegeFootballData API key
- `SUPABASE_URL` — e.g. `https://fhduzzjwcijffgmsavih.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY` — Supabase service role key (bypasses RLS; the tables only allow public SELECT, so writes require this)

Optional repo **variable** (Settings → Secrets and variables → Actions → Variables):

- `CFB_SEASON_YEAR` — defaults to `2026` in the workflows if unset

## Running locally

```bash
pip install -r scripts/requirements.txt
export CFBD_API_KEY=...
export SUPABASE_URL=...
export SUPABASE_SERVICE_ROLE_KEY=...

python scripts/ingest_games.py --year 2026
python scripts/ingest_roster.py --year 2026
python scripts/ingest_player_season_stats.py --year 2026
```

## Notes / gotchas (from the Aug 29 2026 spec)

- CFBD has no separate "Week 0" — the season-opening slate is CFBD's `week=1`. Querying `week=0` silently returns the entire season. `ingest_games.py` avoids this by never passing `week` at all (omitting it returns every week for the year, which also lets one run refresh already-played weeks' scores).
- Player season stats come back long-format (one row per player/statType) — pivoted into `stats` jsonb during ingestion, not at read time.
- 2026 season stats will be thin for the first couple weeks; a zero/low row count from `ingest_player_season_stats.py` early in the season is expected, not a bug.
