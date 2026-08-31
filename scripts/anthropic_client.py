"""
Filter & summarize step (stage 2 of the news pipeline spec) — reuses the
same Anthropic API key already set up for the draft app's AI reasoning
layer (ANTHROPIC_API_KEY), same model tier the draft app uses for
short/cheap explanations (Haiku).

For each raw post, asks Claude to determine: (a) is this fantasy/CFB
relevant at all, (b) a one-line summary, (c) does it plausibly affect any
currently-locked prediction, (d) if so, a directional read — flagged as a
draft suggestion only. Nothing here writes to `predictions` directly; the
directional read becomes a `prediction_updates` row for human review, per
the spec's non-negotiable no-silent-edits rule.
"""
import json
import os
import sys
import time

import anthropic

MODEL = "claude-haiku-4-5-20251001"

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY must be set", file=sys.stderr)
            sys.exit(1)
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _build_prompt(tweet_text: str, source_account: str, locked_predictions: list) -> str:
    preds_block = "\n".join(
        f"- id={p['id']} | {p.get('game_or_player', '')} | {p.get('prediction_type', '')} | "
        f"call: {p.get('call', '')}"
        for p in locked_predictions
    ) or "(none currently locked)"

    return f"""You are the news-triage step for Third Down AI, a fantasy football/CFB \
prediction site. You're given one raw X (Twitter) post from a known NFL/CFB \
beat writer or insider account, plus a list of this site's currently locked \
predictions. Decide whether the post is worth a human's attention.

Post from @{source_account}:
\"\"\"{tweet_text}\"\"\"

Currently locked predictions:
{preds_block}

Respond with ONLY a single JSON object (no markdown fences, no other text), \
with exactly these keys:
{{
  "is_relevant": true or false — is this fantasy/CFB relevant at all (injury, \
depth chart, suspension, trade, snap counts, etc.)? False for unrelated chatter.
  "summary": a single short sentence (the "why", kept genuinely short per site style),
  "linked_prediction_id": the id string of a locked prediction this plausibly \
affects, or null if none / not relevant,
  "impact_note": if linked_prediction_id is set, a short directional read like \
"increases likelihood of hit" or "decreases likelihood of hit" with a brief reason; \
otherwise null
}}

If is_relevant is false, linked_prediction_id and impact_note must both be null."""


def classify_tweet(tweet_text: str, source_account: str, locked_predictions: list, retries: int = 3) -> dict:
    client = _get_client()
    prompt = _build_prompt(tweet_text, source_account, locked_predictions)

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            # Defensive: strip accidental markdown fences if the model adds them.
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            parsed = json.loads(raw)
            return {
                "is_relevant": bool(parsed.get("is_relevant")),
                "summary": parsed.get("summary") or "",
                "linked_prediction_id": parsed.get("linked_prediction_id") or None,
                "impact_note": parsed.get("impact_note") or None,
            }
        except (anthropic.APIStatusError, anthropic.APIConnectionError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt < retries:
                wait = 2 ** attempt
                print(f"Retrying Claude classification (attempt {attempt}/{retries}) after: {exc} — sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)

    print(f"ERROR: Claude classification failed after {retries} attempts: {last_err}", file=sys.stderr)
    # Fail safe: treat as not relevant rather than crashing the whole run
    # over one bad tweet. The raw item is still stored in news_items either
    # way (see monitor_news.py), so nothing is silently dropped.
    return {"is_relevant": False, "summary": "", "linked_prediction_id": None, "impact_note": None}
