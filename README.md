# Third Down AI — Data Pipelines

Ingests CollegeFootballData (CFBD) and X (Twitter) beat-writer news into Supabase on a schedule via GitHub Actions.

## CFB tables

- `cfb_games` — schedule/results cache. Synced daily (idempotent upsert by CFBD game id).
- `cfb_roster` — depth-chart/biographical context. Synced monthly (roster barely changes game-to-game); trigger manually after a transfer or depth-chart shift.
- `cfb_player_season_stats` — lower priority; pivoted from CFBD's long-format player stats into one row per (season, player, category) with a `stats` jsonb blob. Synced weekly.
- `cfb_rankings` — AP Top 25 (and every other poll CFBD returns: Coaches Poll, Playoff Committee, etc.) flattened into one row per (season, week, poll, team). Synced weekly. Added for the Confidence Picks Board's CFB upset-flag logic ("unranked team beats ranked team" / "lower-ranked beats higher-ranked").
- `confidence_picks` — structure only, created for the Confidence Picks Board spec (Aug 29 2026). Stays empty from the pipeline build; populated weekly with hand-written picks, not auto-generated.

CFB predictions themselves stay qualitative/reasoning-based (see the CFB pipeline spec) — `cfb_games` and `cfb_roster` are the tables actually used for that reasoning; `cfb_player_season_stats` and `cfb_rankings` are supplementary (stat callouts, upset-flag basis).

## News pipeline tables (Aug 23 2026 spec)

- `news_items` — single source of truth feeding the future `/news` page, the newsletter pool, and prediction updates. Populated by `monitor_news.py` with `status='pending'`; nothing here is ever auto-published. Has one addition beyond the original spec table: `source_tweet_id` (unique), needed so a re-poll of an overlapping time window can't create duplicate pending items.
- `prediction_updates` — append-only draft directional reads ("increases/decreases likelihood of hit") when a news item plausibly affects a currently-locked prediction. Never touches `predictions` itself. `news_item_id` is unique as a second idempotency layer.

**Not yet built**: the Twilio approve/deny SMS flow and its Supabase Edge Function webhook — Twilio isn't set up yet. Until that exists, approve/reject items by hand: `update news_items set status = 'approved' where id = '...'` in the Supabase SQL editor (or `'rejected'`). Nothing auto-publishes regardless.

## Required GitHub Actions secrets

Set these under Settings → Secrets and variables → Actions → Secrets:

- `CFBD_API_KEY` — CollegeFootballData API key
- `SUPABASE_URL` — e.g. `https://fhduzzjwcijffgmsavih.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY` — Supabase service role key (bypasses RLS; the tables only allow public SELECT, so writes require this)
- `X_BEARER_TOKEN` — X API v2 App-Only Bearer token. **Requires credits loaded on the X developer account** — a $0-credit account will get a 402/403 from the news monitor workflow; that's expected until credits are added, not a bug in the script.
- `ANTHROPIC_API_KEY` — reuses the same key the draft app uses, for the news filter/summarize step (Haiku model)

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
python scripts/ingest_rankings.py --year 2026

# News pipeline — also needs X_BEARER_TOKEN and ANTHROPIC_API_KEY set:
python scripts/monitor_news.py
```

## Notes / gotchas (from the Aug 29 2026 CFB spec)

- CFBD has no separate "Week 0" — the season-opening slate is CFBD's `week=1`. Querying `week=0` silently returns the entire season. `ingest_games.py` avoids this by never passing `week` at all (omitting it returns every week for the year, which also lets one run refresh already-played weeks' scores).
- Player season stats come back long-format (one row per player/statType) — pivoted into `stats` jsonb during ingestion, not at read time.
- 2026 season stats will be thin for the first couple weeks; a zero/low row count from `ingest_player_season_stats.py` early in the season is expected, not a bug.

## Notes / gotchas (from the Aug 23 2026 news pipeline spec)

- `accounts.txt` (in `scripts/`) is the curated X account list — one handle per line, `#` comments allowed. Seeded with a small national-insider starter list per the spec's open decision; edit freely, the script rereads it every run.
- The monitor script never re-classifies or re-inserts a tweet it's already stored (checked by `source_tweet_id` before calling the Anthropic API at all), so overlapping poll windows cost nothing extra.
- `classify_tweet()` fails safe: if Claude's response can't be parsed as JSON after retries, the item is still stored in `news_items` (so nothing is silently dropped) but marked not relevant/unlinked rather than crashing the whole run over one bad response.
- Twilio setup (not yet done): a Twilio account, a phone number (~$1/month + ~$0.0079/text), and a Supabase Edge Function to receive the incoming-SMS webhook. That's a separate build once the account exists.
