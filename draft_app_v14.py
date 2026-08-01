"""
Third Down AI - Draft Assistant (v14)
Run with: python -m streamlit run draft_app_v14.py

Requires: pip install streamlit-keyup   (for live, as-you-type search)
Requires: pip install anthropic          (for the AI reasoning layer)
Requires: adp_data_2026.csv in the same folder (ADP data, merged in automatically)
Requires: an Anthropic API key -- checked in this order:
          1. Streamlit Cloud secrets (st.secrets["ANTHROPIC_API_KEY"]) -- for
             the hosted/deployed version, set under the app's Secrets settings
          2. OS environment variable ANTHROPIC_API_KEY -- for local use
          3. Manual sidebar input (session only, never saved) -- fallback

v14 adds on top of v13:
- API key lookup now also checks Streamlit Cloud's st.secrets, so the exact
  same file works unchanged whether run locally or deployed to Streamlit
  Community Cloud for sharing with the league -- only where the key lives
  changes, not the code.
- Added requirements.txt (streamlit, pandas, streamlit-keyup, anthropic),
  required for Streamlit Cloud to install dependencies on deploy.
- Third Down AI branding: uses the REAL banner/icon image assets (assets/banner.png,
  assets/banner_cropped.png, assets/icon.png -- must be committed to the repo
  alongside this file) and the actual brand tagline "We show our work. We keep
  score." instead of invented copy. Filename kept as draft_app_v14.py so the
  already-deployed Streamlit app picks up the change on redeploy without
  needing its settings updated.
- UI cleanup: "Value model tuning" is now a collapsible sidebar expander,
  consistent with the other config sections. The "AI Reasoning Layer" sidebar
  section is now fully hidden once a key is resolved from Streamlit secrets
  or the environment (the normal deployed/tester experience) -- it only
  appears at all when someone genuinely needs to type a key in manually.

v13 recap: AI Reasoning Layer ("Why this pick?" + ask-about-any-player).
v12 recap: absolute (not curved) draft grading.
v11 recap: structured Starters/Bench roster tables; grading summary table.
v9 recap: end-of-draft grading engine (surplus value vs ADP expectation).
v8 recap: reliable, repeatable "Undo last pick"; bye-week display fixes.
v7 recap: ADP-based live Steal Alerts.
v6 recap: live Team Power Rankings leaderboard.
v5 recap: merged color-coded board with TOP PICK callout.
v4 recap: live search, fully configurable scoring + roster settings.
v10 recap: sidebar auto-draft ("Others" / "All").

Still not included (future work):
- Position-run detector, positional heatmap, shareable recap card,
  live web-search-backed injury/news context for the AI layer
"""

import streamlit as st
import pandas as pd
import random
import os
import math

try:
    import anthropic
    HAVE_ANTHROPIC = True
except ImportError:
    HAVE_ANTHROPIC = False

try:
    from st_keyup import st_keyup
    HAVE_KEYUP = True
except ImportError:
    HAVE_KEYUP = False

RAW_FILE = "raw_projections_2026.csv"
ADP_FILE = "adp_data_2026.csv"

st.set_page_config(page_title="Third Down AI - Draft Assistant", page_icon="assets/icon.png", layout="wide")


@st.cache_data
def load_raw():
    raw_df = pd.read_csv(RAW_FILE)
    try:
        adp_df = pd.read_csv(ADP_FILE)[["name", "adp_rank"]]
        raw_df = raw_df.merge(adp_df, on="name", how="left")
    except FileNotFoundError:
        raw_df["adp_rank"] = None
    return raw_df


raw = load_raw()

# ---- Session state defaults ----
defaults = {
    "num_teams": 10,
    "team_names": [f"Team {i+1}" for i in range(10)],
    "my_team": "Team 1",
    "snake": True,
    "draft_started": False,
    "pick_number": 1,
    "drafted": {},
    "draft_log": [],       # ordered list of {pick_number, name, team} -- source of truth for undo
    "team_rosters": {},
    "flex_filled": {},
    # Scoring defaults (Yahoo-style full PPR, matching Brad's league -- editable)
    "sc_pass_yds_per_pt": 25.0,
    "sc_pass_td": 4.0,
    "sc_pass_int": -1.0,
    "sc_pass_2pt": 2.0,
    "sc_rush_yds_per_pt": 10.0,
    "sc_rush_td": 6.0,
    "sc_rush_2pt": 2.0,
    "sc_rec": 1.0,
    "sc_rec_yds_per_pt": 10.0,
    "sc_rec_td": 6.0,
    "sc_rec_2pt": 2.0,
    "sc_fum_lost": -2.0,
    # Roster defaults
    "req_qb": 1,
    "req_rb": 2,
    "req_wr": 3,
    "req_te": 1,
    "req_flex": 1,
    "req_bench": 8,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

FLEX_ELIGIBLE = {"RB", "WR", "TE"}
POSITION_LABELS = {"QB": "quarterback", "RB": "running back", "WR": "wide receiver", "TE": "tight end"}
FLEX_FILL_PENALTY = 3.0  # points subtracted when a pick only fills FLEX, not a genuinely required starter slot


# ---- Point / VBD computation (recomputed live from current settings) ----
def compute_board():
    df = raw.copy()
    s = st.session_state
    df["fpts"] = (
        df["pass_yds"] / s["sc_pass_yds_per_pt"]
        + df["pass_td"] * s["sc_pass_td"]
        + df["pass_int"] * s["sc_pass_int"]
        + df["pass_2pt"] * s["sc_pass_2pt"]
        + df["rush_yds"] / s["sc_rush_yds_per_pt"]
        + df["rush_td"] * s["sc_rush_td"]
        + df["rush_2pt"] * s["sc_rush_2pt"]
        + df["rec"] * s["sc_rec"]
        + df["rec_yds"] / s["sc_rec_yds_per_pt"]
        + df["rec_td"] * s["sc_rec_td"]
        + df["rec_2pt"] * s["sc_rec_2pt"]
        + df["fum_lost"] * s["sc_fum_lost"]
    ).round(2)

    required = {"QB": s["req_qb"], "RB": s["req_rb"], "WR": s["req_wr"], "TE": s["req_te"]}
    num_teams = s["num_teams"]
    flex_slots = s["req_flex"] * num_teams

    by_pos = {p: df[df["pos"] == p].sort_values("fpts", ascending=False) for p in required}

    req_counts = {p: required[p] * num_teams for p in required}
    baselines = {}
    for p in required:
        n = req_counts[p]
        if len(by_pos[p]) >= n and n > 0:
            baselines[p] = by_pos[p].iloc[n - 1]["fpts"]
        elif len(by_pos[p]) > 0:
            baselines[p] = by_pos[p].iloc[-1]["fpts"]
        else:
            baselines[p] = 0.0

    leftover = []
    for p in required:
        if p not in FLEX_ELIGIBLE:
            continue
        n = req_counts[p]
        leftover.append(by_pos[p].iloc[n:])
    leftover_df = pd.concat(leftover).sort_values("fpts", ascending=False) if leftover else pd.DataFrame(columns=df.columns)
    flex_pool = leftover_df.head(flex_slots) if flex_slots > 0 else leftover_df.head(0)
    flex_positions_present = set(flex_pool["pos"].unique())
    flex_baseline = flex_pool["fpts"].min() if len(flex_pool) > 0 else None

    for p in required:
        if p in FLEX_ELIGIBLE and p in flex_positions_present and flex_baseline is not None:
            baselines[p] = min(baselines[p], flex_baseline)

    df["vbd_value"] = df.apply(lambda r: round(r["fpts"] - baselines.get(r["pos"], 0), 1), axis=1)
    return df, required, baselines


board, REQUIRED_STARTERS, position_baselines = compute_board()


def get_team_on_clock(pick_number, team_names, snake):
    n = len(team_names)
    round_num = (pick_number - 1) // n
    idx_in_round = (pick_number - 1) % n
    if snake and round_num % 2 == 1:
        idx_in_round = n - 1 - idx_in_round
    return team_names[idx_in_round], round_num + 1


def mark_drafted(name, team_name):
    row = board.loc[board["name"] == name].iloc[0]
    pos = row["pos"]
    bye_raw = row["bye"]
    bye_val = int(bye_raw) if pd.notna(bye_raw) else None
    st.session_state.drafted[name] = team_name
    st.session_state.draft_log.append({
        "pick_number": st.session_state.pick_number, "name": name, "team": team_name
    })
    roster = st.session_state.team_rosters.setdefault(team_name, [])
    count_before = sum(1 for p in roster if p["pos"] == pos)
    if count_before >= REQUIRED_STARTERS.get(pos, 0) and pos in FLEX_ELIGIBLE and \
       st.session_state.flex_filled.get(team_name) is None:
        st.session_state.flex_filled[team_name] = pos
    roster.append({"name": name, "pos": pos, "team": row["team"], "bye": bye_val})
    st.session_state.pick_number += 1


def undo_last_pick():
    if not st.session_state.draft_log:
        return
    last = st.session_state.draft_log.pop()
    name, team_name = last["name"], last["team"]
    st.session_state.drafted.pop(name, None)
    roster = st.session_state.team_rosters.get(team_name, [])
    st.session_state.team_rosters[team_name] = [p for p in roster if p["name"] != name]
    # Recompute this team's flex_filled from scratch to stay consistent
    st.session_state.flex_filled[team_name] = None
    counts = {}
    for p in st.session_state.team_rosters[team_name]:
        counts[p["pos"]] = counts.get(p["pos"], 0) + 1
        req = REQUIRED_STARTERS.get(p["pos"], 0)
        if counts[p["pos"]] > req and p["pos"] in FLEX_ELIGIBLE and \
           st.session_state.flex_filled[team_name] is None:
            st.session_state.flex_filled[team_name] = p["pos"]
    # Restore the pick counter to exactly what it was before that pick --
    # correct regardless of how many picks have happened since, because we're
    # always popping the true most-recent entry off an ordered log.
    st.session_state.pick_number = last["pick_number"]


def roster_counts(team_name):
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    for p in st.session_state.team_rosters.get(team_name, []):
        counts[p["pos"]] += 1
    return counts


def get_roster_with_fpts(team_name):
    roster = st.session_state.team_rosters.get(team_name, [])
    result = []
    for p in roster:
        match = board.loc[board["name"] == p["name"]]
        fpts = float(match.iloc[0]["fpts"]) if not match.empty else 0.0
        result.append({**p, "fpts": fpts})
    return result


def compute_team_projection(team_name):
    """
    Returns (starters_points, total_points, starters_detail) for a team,
    using the optimal starting lineup (best players at each required
    position + best remaining flex-eligible players for FLEX slots) out
    of whatever they've drafted so far.
    """
    roster_fpts = get_roster_with_fpts(team_name)
    if not roster_fpts:
        return 0.0, 0.0, []

    total_points = sum(r["fpts"] for r in roster_fpts)

    by_pos = {}
    for r in roster_fpts:
        by_pos.setdefault(r["pos"], []).append(r)
    for p in by_pos:
        by_pos[p].sort(key=lambda r: -r["fpts"])

    starters = []
    for pos, req in REQUIRED_STARTERS.items():
        starters.extend(by_pos.get(pos, [])[:req])

    used_names = {r["name"] for r in starters}
    flex_candidates = []
    for pos in FLEX_ELIGIBLE:
        flex_candidates.extend(
            [r for r in by_pos.get(pos, []) if r["name"] not in used_names]
        )
    flex_candidates.sort(key=lambda r: -r["fpts"])
    starters.extend(flex_candidates[: st.session_state.req_flex])

    starters_points = sum(r["fpts"] for r in starters)
    return round(starters_points, 1), round(total_points, 1), starters


def need_multiplier(team_name, pos, bench_allowance, decay_rate):
    counts = roster_counts(team_name)
    count = counts[pos]
    req = REQUIRED_STARTERS.get(pos, 0)
    pos_label = POSITION_LABELS.get(pos, pos)

    if count < req:
        return 1.0, f"You still need a starting {pos_label} ({count}/{req} filled) — this fills that spot.", 0.0

    if pos in FLEX_ELIGIBLE and st.session_state.flex_filled.get(team_name) is None:
        # FLEX is a "best of what's left" slot, not a specific structural need
        # the way an unfilled required starter is. A multiplicative discount
        # doesn't reliably work here since scarcity bonus is additive and not
        # scaled by it -- when VBD is near zero late in the draft, a <1x
        # multiplier barely moves anything. Use a real additive penalty
        # instead so a genuine roster hole elsewhere reliably wins close
        # calls against a flex-only fill.
        return 1.0, f"This would fill your open FLEX spot (not as urgent as a still-empty required starter elsewhere).", -FLEX_FILL_PENALTY

    already_bench = count - req
    if pos in FLEX_ELIGIBLE and st.session_state.flex_filled.get(team_name) == pos:
        already_bench -= 1
    already_bench = max(already_bench, 0)

    if already_bench < bench_allowance:
        mult = 0.85 * (0.9 ** already_bench)
        return round(mult, 3), (
            f"Your starters at {pos_label} are set, but this is still useful bench depth."
        ), 0.0
    else:
        excess = already_bench - bench_allowance + 1
        mult = 0.85 * (0.9 ** bench_allowance) * (decay_rate ** excess)
        return round(mult, 3), (
            f"You already have plenty of {pos_label}s — this one won't likely see the field."
        ), 0.0


def bye_collision_multiplier(team_name, pos, bye):
    if bye is None or pd.isna(bye):
        return 1.0, None
    bye_int = int(bye)
    roster = st.session_state.team_rosters.get(team_name, [])
    same = [p for p in roster if p["pos"] == pos and p["bye"] == bye_int]
    if not same:
        return 1.0, None
    names = ", ".join(p["name"] for p in same)
    mult = 0.9 ** len(same)
    return round(mult, 3), (
        f"Heads up — he's on a Week {bye_int} bye, same as your {POSITION_LABELS.get(pos, pos)} "
        f"{names}, so you'd be short a starter that week."
    )


# ============ SIDEBAR: League Configuration ============
st.sidebar.header("League Configuration")

with st.sidebar.expander("Teams & draft order", expanded=not st.session_state.draft_started):
    num_teams = st.number_input("Number of teams", min_value=2, max_value=20,
                                 value=st.session_state.num_teams)

    if "my_team_idx" not in st.session_state:
        prior_idx = (st.session_state.team_names.index(st.session_state.my_team)
                     if st.session_state.my_team in st.session_state.team_names else 0)
        st.session_state.my_team_idx = prior_idx

    # Detect a newly-clicked checkbox (any checkbox True that isn't the
    # currently stored selection) BEFORE rendering this run's checkboxes,
    # then force every other checkbox's state to False so exactly one stays
    # checked -- this must happen before the widgets below are created.
    for i in range(num_teams):
        key = f"is_my_team_{i}"
        if st.session_state.get(key) and i != st.session_state.my_team_idx:
            st.session_state.my_team_idx = i
            for j in range(num_teams):
                if j != i:
                    st.session_state[f"is_my_team_{j}"] = False
            break

    st.caption("Team names — check the box next to yours")
    live_team_list = []
    for i in range(num_teams):
        c1, c2 = st.columns([1, 5])
        with c1:
            st.checkbox("", key=f"is_my_team_{i}", value=(i == st.session_state.my_team_idx),
                        label_visibility="collapsed")
        with c2:
            default_val = (st.session_state.team_names[i]
                            if i < len(st.session_state.team_names) else f"Team {i+1}")
            name_val = st.text_input(
                f"team_name_{i}", value=default_val, key=f"team_name_input_{i}",
                label_visibility="collapsed"
            )
        live_team_list.append(name_val.strip() or f"Team {i+1}")

    st.session_state.my_team = live_team_list[st.session_state.my_team_idx]

    snake = st.checkbox("Snake draft (order reverses each round)", value=st.session_state.snake)

    if st.button("Apply teams & order"):
        team_list = live_team_list
        st.session_state.num_teams = num_teams
        st.session_state.team_names = team_list
        st.session_state.snake = snake
        if st.session_state.my_team not in team_list:
            st.session_state.my_team = team_list[0]
        st.session_state.draft_started = True
        st.rerun()

with st.sidebar.expander("Scoring system"):
    st.caption("Points per unit unless noted. Yards fields are 'yards per point' (e.g. 25 = 1 pt per 25 yds).")
    st.session_state.sc_pass_yds_per_pt = st.number_input("Passing yards per point", value=st.session_state.sc_pass_yds_per_pt)
    st.session_state.sc_pass_td = st.number_input("Passing TD", value=st.session_state.sc_pass_td)
    st.session_state.sc_pass_int = st.number_input("Interception", value=st.session_state.sc_pass_int)
    st.session_state.sc_pass_2pt = st.number_input("2-pt conversion (pass)", value=st.session_state.sc_pass_2pt)
    st.session_state.sc_rush_yds_per_pt = st.number_input("Rushing yards per point", value=st.session_state.sc_rush_yds_per_pt)
    st.session_state.sc_rush_td = st.number_input("Rushing TD", value=st.session_state.sc_rush_td)
    st.session_state.sc_rush_2pt = st.number_input("2-pt conversion (rush)", value=st.session_state.sc_rush_2pt)
    st.session_state.sc_rec = st.number_input("Points per reception (PPR)", value=st.session_state.sc_rec)
    st.session_state.sc_rec_yds_per_pt = st.number_input("Receiving yards per point", value=st.session_state.sc_rec_yds_per_pt)
    st.session_state.sc_rec_td = st.number_input("Receiving TD", value=st.session_state.sc_rec_td)
    st.session_state.sc_rec_2pt = st.number_input("2-pt conversion (rec)", value=st.session_state.sc_rec_2pt)
    st.session_state.sc_fum_lost = st.number_input("Fumble lost", value=st.session_state.sc_fum_lost)

with st.sidebar.expander("Roster requirements"):
    st.session_state.req_qb = st.number_input("QB starters", min_value=0, max_value=3, value=st.session_state.req_qb)
    st.session_state.req_rb = st.number_input("RB starters", min_value=0, max_value=5, value=st.session_state.req_rb)
    st.session_state.req_wr = st.number_input("WR starters", min_value=0, max_value=5, value=st.session_state.req_wr)
    st.session_state.req_te = st.number_input("TE starters", min_value=0, max_value=3, value=st.session_state.req_te)
    st.session_state.req_flex = st.number_input("FLEX slots (RB/WR/TE)", min_value=0, max_value=3, value=st.session_state.req_flex)
    st.session_state.req_bench = st.number_input("Bench spots", min_value=0, max_value=15, value=st.session_state.req_bench)

    total_rounds = (st.session_state.req_qb + st.session_state.req_rb + st.session_state.req_wr +
                    st.session_state.req_te + st.session_state.req_flex + st.session_state.req_bench)
    st.caption(f"Rounds implied by roster size: **{total_rounds}** (starters + flex + bench, excludes IR)")

st.sidebar.markdown("---")
with st.sidebar.expander("Value model tuning"):
    bench_allowance = st.slider("Bench allowance per position (value model)", 0, 4, 2,
                                 help="How many picks beyond starters/flex still get solid value before steep decay. Separate from the roster bench-spot count above.")
    decay_rate = st.slider("Excess-depth decay rate", 0.2, 0.8, 0.55, 0.05)
    steal_threshold = st.slider(
        "Steal alert threshold (picks past ADP)", 5, 30, 15,
        help="Flag a player as a live steal once they've fallen this many picks past their expected (ADP) draft position."
    )

    st.markdown("---")
    scarcity_toggle = st.checkbox(
        "Factor positional scarcity into rankings (recommended)", value=True,
        help=(
            "ON: when the best remaining player at a position has a big point gap over the "
            "next-best option there, that gap adds real value to their ranking — reflecting "
            "the cost of waiting and likely missing that whole tier. This is why an elite TE "
            "or a top RB can rank above a slightly-higher-raw-value player at a deeper position. "
            "OFF: rankings are pure value (VBD) only, with no scarcity adjustment -- the scarcity "
            "insight still appears in the explanation text either way, but it won't move anyone's "
            "rank when this is off."
        )
    )
    scarcity_weight = 0.5
    if scarcity_toggle:
        scarcity_weight = st.slider(
            "Scarcity adjustment strength", 0.0, 1.0, 0.5, 0.1,
            help="How much of the positional point-gap gets added as bonus value. 0 = no effect (same as OFF). 1 = the full gap counts."
        )


def auto_complete_draft(stop_before_team=None):
    """
    Auto-drafts remaining picks using each team's own need/bye-adjusted
    recommendation (same math as the main board), stopping before a given
    team's turn if stop_before_team is set (used for "auto-draft others").
    """
    total_rounds_needed = (
        st.session_state.req_qb + st.session_state.req_rb + st.session_state.req_wr +
        st.session_state.req_te + st.session_state.req_flex + st.session_state.req_bench
    )
    total_needed = st.session_state.num_teams * total_rounds_needed
    safety = 0
    while len(st.session_state.draft_log) < total_needed and safety < 5000:
        safety += 1
        current_team, _ = get_team_on_clock(
            st.session_state.pick_number, st.session_state.team_names, st.session_state.snake
        )
        if stop_before_team is not None and current_team == stop_before_team:
            break
        avail = board[~board["name"].isin(st.session_state.drafted.keys())].copy()
        if avail.empty:
            break
        adj_vals = []
        for _, row in avail.iterrows():
            nm, _, need_penalty = need_multiplier(current_team, row["pos"], bench_allowance, decay_rate)
            bm, _ = bye_collision_multiplier(current_team, row["pos"], row["bye"])
            adj_vals.append(row["vbd_value"] * nm * bm + need_penalty)
        avail["_adj"] = adj_vals
        pick_row = avail.sort_values("_adj", ascending=False).iloc[0]
        mark_drafted(pick_row["name"], current_team)


st.sidebar.markdown("---")
st.sidebar.subheader("Auto-draft")
st.sidebar.caption("Fills picks using each team's own need/bye-adjusted best available. May take a few seconds.")
auto_col1, auto_col2 = st.sidebar.columns(2)
if auto_col1.button("🤖 Others"):
    auto_complete_draft(stop_before_team=st.session_state.my_team)
    st.rerun()
if auto_col2.button("⏩ All"):
    auto_complete_draft(stop_before_team=None)
    st.rerun()

# Resolve the API key silently in the background. Only show sidebar UI for
# this at all when a manual key entry is genuinely needed (local use with no
# secrets/env configured) -- testers on the deployed app never see this,
# since Streamlit Cloud secrets resolve it before any UI would render.
secrets_key = ""
try:
    secrets_key = st.secrets.get("ANTHROPIC_API_KEY", "")
except Exception:
    pass  # no secrets.toml / no Streamlit Cloud secrets configured -- fine locally

env_key = os.environ.get("ANTHROPIC_API_KEY", "")

if secrets_key:
    st.session_state["anthropic_api_key"] = secrets_key
elif env_key:
    st.session_state["anthropic_api_key"] = env_key
elif HAVE_ANTHROPIC:
    st.sidebar.markdown("---")
    st.sidebar.subheader("AI Reasoning Layer")
    key_input = st.sidebar.text_input(
        "Anthropic API key (session only, never saved to disk)",
        type="password", value=st.session_state.get("anthropic_api_key", "")
    )
    st.session_state["anthropic_api_key"] = key_input
    st.sidebar.caption(
        "Uses your own Anthropic account -- each explanation is a real, small API call "
        "against your usage. Nothing is sent unless you click an explain button."
    )


def get_ai_take(player_row, team_name, reason_text):
    """
    Calls Claude (Haiku, for speed/cost) to narrate a pick in plain language,
    using ONLY the analytical context we already computed -- explicitly
    instructed not to invent specific recent news it can't actually know.
    """
    api_key = st.session_state.get("anthropic_api_key", "")
    if not api_key:
        return "No API key set -- add one in the sidebar under 'AI Reasoning Layer' to use this."

    try:
        client = anthropic.Anthropic(api_key=api_key)
        system_prompt = (
            "You are a sharp, concise fantasy football draft analyst. You're given a "
            "specific player and quantitative context (a VBD-style value score, why they "
            "fit or don't fit the team's current roster needs, and any bye-week or ADP "
            "notes). Write 3-4 sentences explaining the pick in plain language for a "
            "casual fantasy player. You may draw on general, durable knowledge you have "
            "about the player (career track record, typical role, playing style, general "
            "injury history pattern) but you must NOT invent specific recent news, camp "
            "reports, or current-week updates -- you have no way to know those. If you're "
            "not confident about something recent, don't claim it. Be direct, not hypey."
        )
        user_prompt = (
            f"Player: {player_row['name']} ({player_row['pos']}, {player_row['team']})\n"
            f"Team considering them: {team_name}\n"
            f"Raw value score (VBD): {player_row['vbd_value']:.1f}\n"
            f"Value adjusted for this team's roster: {player_row['adjusted_value']:.1f}\n"
            f"Why this pick fits (or doesn't) right now: {reason_text}\n\n"
            f"Explain this pick."
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
    except Exception as e:
        return f"AI explanation failed: {e}"


st.sidebar.markdown("---")
if st.sidebar.button("Reset entire draft"):
    st.session_state.drafted = {}
    st.session_state.draft_log = []
    st.session_state.team_rosters = {}
    st.session_state.flex_filled = {}
    st.session_state.pick_number = 1
    st.rerun()


# ============ MAIN PANEL ============
st.image("assets/banner_cropped.png", use_container_width=True)
st.markdown(
    "<div style='color:#2FB35E; font-size:16px; font-weight:600; margin-top:-8px; margin-bottom:12px;'>"
    "Draft Assistant</div>",
    unsafe_allow_html=True,
)

total_rounds = (st.session_state.req_qb + st.session_state.req_rb + st.session_state.req_wr +
                st.session_state.req_te + st.session_state.req_flex + st.session_state.req_bench)
on_clock_team, round_num = get_team_on_clock(
    st.session_state.pick_number, st.session_state.team_names, st.session_state.snake
)
st.markdown(
    f"### On the clock: **{on_clock_team}**  |  Round {round_num} of {total_rounds}, "
    f"Pick {st.session_state.pick_number}"
)

with st.expander(f"📋 {st.session_state.my_team}'s Roster So Far", expanded=True):
    my_starters_pts, _, my_starters_detail = compute_team_projection(st.session_state.my_team)
    my_full_roster = get_roster_with_fpts(st.session_state.my_team)
    if my_full_roster:
        starter_names_compact = {s["name"] for s in my_starters_detail}
        bench_count = len([p for p in my_full_roster if p["name"] not in starter_names_compact])

        by_pos_compact = {p: [] for p in REQUIRED_STARTERS}
        for p in my_full_roster:
            by_pos_compact.setdefault(p["pos"], []).append(p["name"])

        cols = st.columns(len(REQUIRED_STARTERS))
        for col, pos in zip(cols, REQUIRED_STARTERS):
            names = by_pos_compact.get(pos, [])
            names_html = "<br>".join(names) if names else "<span style='color:#999'>—</span>"
            col.markdown(f"**{pos}**  \n{names_html}", unsafe_allow_html=True)

        st.caption(f"Projected starters: {my_starters_pts:.1f} pts  •  Bench: {bench_count} players")
    else:
        st.caption("No picks yet.")

col1, col2, col3 = st.columns([1, 2, 2])
with col1:
    pos_filter = st.selectbox("Position filter", ["ALL", "QB", "RB", "WR", "TE"])
with col2:
    if HAVE_KEYUP:
        search = st_keyup("Search player name (live)", key="search_live")
    else:
        search = st.text_input("Search player name (press Enter — install streamlit-keyup for live search)")
with col3:
    view_team = st.selectbox(
        "View recommendation for", st.session_state.team_names,
        index=st.session_state.team_names.index(on_clock_team)
    )

if st.session_state.get("last_seen_pick") != st.session_state.pick_number:
    st.session_state.assign_team = on_clock_team
    st.session_state.last_seen_pick = st.session_state.pick_number

assign_team = st.selectbox(
    "Drafting for (defaults to team on the clock — override if needed)",
    st.session_state.team_names, key="assign_team"
)

available_all = board[~board["name"].isin(st.session_state.drafted.keys())].copy()

# Steal detection: how many picks past this player's ADP-expected slot are we,
# given the CURRENT overall pick number? Only meaningful for players with a
# known ADP; players with no ADP data are left out of steal detection entirely
# rather than guessed at. Computed on the FULL pool so it's never hidden by
# an active position filter or search.
available_all["steal_gap"] = available_all["adp_rank"].apply(
    lambda adp: (st.session_state.pick_number - adp) if pd.notna(adp) else None
)

steals = available_all[available_all["steal_gap"].fillna(0) >= steal_threshold].copy()
steals = steals.sort_values("steal_gap", ascending=False)

if len(steals) > 0:
    st.markdown("### 🔥 Steal Alerts")
    st.caption(
        f"Still on the board {steal_threshold}+ picks past their expected (ADP) draft slot."
    )
    for _, row in steals.head(5).iterrows():
        st.markdown(
            f"🔥 **{row['name']}** ({row['pos']}, {row['team']}) — "
            f"expected around pick {int(row['adp_rank'])}, still available at pick "
            f"{st.session_state.pick_number} (**{int(row['steal_gap'])} picks late**)"
        )
    st.markdown("---")

# Positional scarcity: for each player, how do they compare to the next
# (or top) available player at their SAME position? This is the actual
# "why this position right now" argument -- a big gap to the next-best
# option at that position is a real cliff (grab it now); a small gap means
# the position stays deep and you could reasonably wait.
pos_groups = {}
for pos in REQUIRED_STARTERS:
    pos_groups[pos] = (
        available_all[available_all["pos"] == pos]
        .sort_values("vbd_value", ascending=False)
        .reset_index(drop=True)
    )


def gap_tier_phrase(gap):
    if gap >= 20:
        return "a huge edge — this position falls off a cliff after him"
    elif gap >= 10:
        return "a real, meaningful edge"
    elif gap >= 5:
        return "a modest edge"
    else:
        return "barely ahead of the next option, so this position should stay deep for a while"


def scarcity_info(row):
    """Returns (explanation_text, scarcity_gap). scarcity_gap is only
    non-zero for the current best-remaining player at their position --
    that's the one actually facing a "grab now or miss this tier" decision."""
    pos_df = pos_groups.get(row["pos"])
    pos_label = POSITION_LABELS.get(row["pos"], row["pos"])
    if pos_df is None or len(pos_df) == 0:
        return "", 0.0
    matches = pos_df.index[pos_df["name"] == row["name"]]
    if len(matches) == 0:
        return "", 0.0
    rank = matches[0]  # 0-based: 0 = best remaining at this position
    if rank == 0:
        if len(pos_df) > 1:
            gap = row["vbd_value"] - pos_df.iloc[1]["vbd_value"]
            next_name = pos_df.iloc[1]["name"]
            tier = gap_tier_phrase(gap)
            text = (
                f"He's the best {pos_label} left on the board, {gap:.0f} points ahead of the "
                f"next-best one ({next_name}) — {tier}."
            )
            return text, gap
        return f"He's the only {pos_label} left with real value.", 0.0
    else:
        top_row = pos_df.iloc[0]
        gap_to_top = top_row["vbd_value"] - row["vbd_value"]
        text = (
            f"A solid {pos_label} option, though {gap_to_top:.0f} points behind the top "
            f"{pos_label} still on the board ({top_row['name']})."
        )
        return text, 0.0


adj_values, reasons = [], []
for _, row in available_all.iterrows():
    need_mult, need_reason, need_penalty = need_multiplier(view_team, row["pos"], bench_allowance, decay_rate)
    bye_mult, bye_reason = bye_collision_multiplier(view_team, row["pos"], row["bye"])
    total_mult = need_mult * bye_mult

    scarcity_text, scarcity_gap = scarcity_info(row)
    scarcity_bonus = (scarcity_gap * scarcity_weight) if (scarcity_toggle and scarcity_gap > 0) else 0.0
    adj_values.append(round(row["vbd_value"] * total_mult + scarcity_bonus + need_penalty, 1))

    parts = [scarcity_text]
    if scarcity_bonus > 0:
        parts.append(f"(+{scarcity_bonus:.0f} added to his ranking for that scarcity)")
    parts.append(need_reason)
    if bye_reason:
        parts.append(bye_reason)
    if pd.notna(row["steal_gap"]) and row["steal_gap"] >= steal_threshold:
        parts.append(
            f"He's also fallen {int(row['steal_gap'])} picks past where he's normally taken — "
            f"good value if he's still here."
        )
    reasons.append(" ".join(p for p in parts if p))

available_all["adjusted_value"] = adj_values
available_all["reason"] = reasons

best_available_all = available_all.sort_values("vbd_value", ascending=False)
recommended_all = available_all.sort_values("adjusted_value", ascending=False).reset_index(drop=True)

# Now that we know our own ranking, compare it to each player's ADP (industry
# consensus draft position) and explain any meaningful disagreement -- e.g.
# "we have him ranked well ahead of his typical ADP" points straight back to
# whatever drove that (scarcity bonus, need, etc.), so it's not just a
# floating number with no explanation.
#
# Important: published ADP is almost always sourced from standard 10-12 team
# industry drafts, NOT necessarily this league's actual team count. Our own
# ranking already correctly accounts for st.session_state.num_teams (it's
# baked into the VBD baseline calculation). Comparing raw rank numbers
# directly would silently compare two different scales -- so instead we
# convert both into ROUNDS, each using its own team-count context, and
# compare THAT. This is what correctly answers "does my league size mean
# I might land him later than his ADP suggests" instead of just assuming yes.
# (our own round is still computed via league-size-aware projection above;
# the ADP side of the comparison below is kept as a simple pick-number
# reference rather than converting it into rounds)

rank_lookup = {name: i + 1 for i, name in enumerate(recommended_all["name"])}
current_round = math.ceil(st.session_state.pick_number / st.session_state.num_teams)
final_reasons = []
for i, row in enumerate(available_all.itertuples()):
    base_reason = reasons[i]
    our_rank = rank_lookup.get(row.name)
    adp_rank = row.adp_rank
    note = ""
    if our_rank is not None:
        # our_rank is a rank among REMAINING players, not an absolute pick
        # number -- project it forward from the current pick, since
        # (our_rank - 1) other remaining players are expected to go before
        # him starting from right now.
        expected_overall_pick = st.session_state.pick_number + our_rank - 1
        our_round = math.ceil(expected_overall_pick / st.session_state.num_teams)

        # Urgency: compare to the CURRENT round, not to ADP. This answers
        # "should I wait on him" -- if our own model expects him gone at or
        # before the round we're already in, waiting is not supported by our
        # model, regardless of what public ADP says.
        if our_round <= current_round:
            note += " Our model expects him gone by about now — don't expect him to last another round."
        else:
            rounds_of_room = our_round - current_round
            note += (
                f" Our model doesn't expect him gone for about {rounds_of_room} more "
                f"round{'s' if rounds_of_room != 1 else ''} — you could reasonably wait on him if you want."
            )

        # ADP context: purely informational, simple pick-vs-pick comparison --
        # the round/team-size math is useful internally but just adds noise
        # to the sentence itself.
        if pd.notna(adp_rank):
            adp_rank_int = int(adp_rank)
            current_pick = st.session_state.pick_number
            if adp_rank_int < current_pick:
                note += f" For reference, his typical ADP is pick {adp_rank_int} — you're already past that."
            elif adp_rank_int > current_pick:
                note += (
                    f" For reference, his typical ADP is pick {adp_rank_int} "
                    f"({adp_rank_int - current_pick} picks from now)."
                )
            else:
                note += f" For reference, his typical ADP is pick {adp_rank_int} — right about now."
    final_reasons.append(base_reason + note)

available_all["reason"] = final_reasons

# Top-3 sets and the TRUE top pick are always computed off the full pool --
# never off whatever a position filter or search happens to narrow the view
# to. This is what fixes the bug where searching made an unrelated player
# falsely appear as "TOP PICK": that label now only ever attaches to the
# actual best pick, whether or not it's currently visible in a filtered view.
top3_vbd_names = set(best_available_all.head(3)["name"])
top3_rec_names = set(recommended_all.head(3)["name"])
true_top_pick_name = recommended_all.iloc[0]["name"] if len(recommended_all) else None

# NOW apply position/search filters -- for DISPLAY only, using the columns
# already computed above rather than recomputing anything.
available = available_all.copy()
if pos_filter != "ALL":
    available = available[available["pos"] == pos_filter]
if search:
    available = available[available["name"].str.contains(search, case=False, na=False)]

best_available = available.sort_values("vbd_value", ascending=False)
recommended = available.sort_values("adjusted_value", ascending=False)


def draft_button(row, key_prefix):
    if st.button(f"Draft to {assign_team}", key=f"{key_prefix}_btn_{row['name']}"):
        mark_drafted(row["name"], assign_team)
        st.rerun()


st.caption(
    "🏆 Top pick for the selected team   🟡 Also top 3 overall (raw VBD)   "
    "🟢 Also top 3 recommended"
)

if HAVE_ANTHROPIC:
    with st.expander("🤖 Ask AI about any player"):
        ask_col1, ask_col2 = st.columns([3, 1])
        with ask_col1:
            ask_name = st.text_input(
                "Player name", key="ai_ask_name", label_visibility="collapsed",
                placeholder="e.g. Puka Nacua"
            )
        with ask_col2:
            ask_clicked = st.button("Ask", key="ai_ask_button")
        if ask_clicked and ask_name:
            match = board[board["name"].str.contains(ask_name, case=False, na=False)]
            if match.empty:
                st.warning(f"No player found matching '{ask_name}'.")
            else:
                prow = match.iloc[0]
                nm, nreason, need_penalty = need_multiplier(view_team, prow["pos"], bench_allowance, decay_rate)
                bm, breason = bye_collision_multiplier(view_team, prow["pos"], prow["bye"])
                parts = [nreason]
                if breason:
                    parts.append(breason)
                reason_text = "; ".join(parts)
                adj_val = round(prow["vbd_value"] * nm * bm + need_penalty, 1)
                prow_dict = {
                    "name": prow["name"], "pos": prow["pos"], "team": prow["team"],
                    "vbd_value": prow["vbd_value"], "adjusted_value": adj_val,
                }
                with st.spinner("Asking Claude..."):
                    explanation = get_ai_take(prow_dict, view_team, reason_text)
                st.info(explanation)

st.subheader(f"Draft Board — Recommended for {view_team}")

recommended_list = list(recommended.head(30).iterrows())
for i, (_, row) in enumerate(recommended_list):
    is_yellow = row["name"] in top3_vbd_names
    is_green = row["name"] in top3_rec_names
    is_top_pick = (row["name"] == true_top_pick_name)

    if is_top_pick:
        bg = "background-color:#fff3b0;border:3px solid #b8860b;border-radius:6px;color:#1a1a2e;"
        label = "🏆 TOP PICK — "
    elif is_yellow and is_green:
        bg = "background-color:#fff3b0;border-left:6px solid #2e7d32;color:#1a1a2e;"
        label = ""
    elif is_green:
        bg = "background-color:#d9f2d9;color:#1a1a2e;"
        label = ""
    elif is_yellow:
        bg = "background-color:#fff3b0;color:#1a1a2e;"
        label = ""
    else:
        bg = ""
        label = ""

    c1, c2, c3, c4, c5 = st.columns([2.5, 1, 1.8, 2, 2.2])
    with c1:
        weight = "font-size:1.1em;" if is_top_pick else ""
        st.markdown(
            f"<div style='padding:6px 10px;{bg}{weight}'><b>{label}{row['name']}</b></div>",
            unsafe_allow_html=True,
        )
    c2.write(row["pos"])
    c3.write(f"VBD {row['vbd_value']:.1f} → **Adj {row['adjusted_value']:.1f}**")
    c4.caption(row["reason"])
    with c5:
        draft_button(row, "board")

    if is_top_pick and HAVE_ANTHROPIC:
        if st.button("🤖 Why this pick?", key="ai_why_top"):
            with st.spinner("Asking Claude..."):
                st.session_state["ai_explanation_top"] = get_ai_take(row, view_team, row["reason"])
                st.session_state["ai_explanation_top_player"] = row["name"]
        if st.session_state.get("ai_explanation_top_player") == row["name"]:
            st.info(st.session_state.get("ai_explanation_top", ""))

st.markdown("---")
st.subheader("🏆 Team Power Rankings (live)")
st.caption(
    "Starters = projected points from each team's best possible starting lineup "
    "given what they've drafted so far. Total = full roster depth. Early in the "
    "draft this is noisy (mostly reflects who picked first) -- it gets meaningful "
    "as rosters fill in."
)

rank_rows = []
for t in st.session_state.team_names:
    starters_pts, total_pts, _ = compute_team_projection(t)
    picks_made = len(st.session_state.team_rosters.get(t, []))
    rank_rows.append({
        "Team": t, "Starters Pts": starters_pts, "Total Roster Pts": total_pts,
        "Picks Made": picks_made,
    })

rank_df = pd.DataFrame(rank_rows).sort_values("Starters Pts", ascending=False).reset_index(drop=True)
rank_df.insert(0, "Rank", range(1, len(rank_df) + 1))
st.dataframe(
    rank_df, use_container_width=True, hide_index=True,
    column_config={
        "Starters Pts": st.column_config.NumberColumn(format="%.1f"),
        "Total Roster Pts": st.column_config.NumberColumn(format="%.1f"),
    },
)

st.markdown("---")
st.subheader(f"{st.session_state.my_team}'s Roster")

starters_pts, total_pts, starters_detail = compute_team_projection(st.session_state.my_team)
full_roster = get_roster_with_fpts(st.session_state.my_team)

if full_roster:
    # Label each starter slot (position starters, then FLEX for whatever's left over)
    running = {p: 0 for p in REQUIRED_STARTERS}
    slot_rows = []
    for s in starters_detail:
        pos = s["pos"]
        if running[pos] < REQUIRED_STARTERS[pos]:
            running[pos] += 1
            label = f"{pos}{running[pos]}" if REQUIRED_STARTERS[pos] > 1 else pos
        else:
            label = "FLEX"
        slot_rows.append({"Slot": label, "Player": s["name"], "Team": s["team"],
                           "Bye": f"Week {s['bye']}" if s["bye"] is not None else "N/A",
                           "Proj Pts": f"{s['fpts']:.1f}"})

    st.write(f"**Starters** — projected {starters_pts:.1f} pts")
    st.table(pd.DataFrame(slot_rows))

    starter_names = {s["name"] for s in starters_detail}
    bench = [p for p in full_roster if p["name"] not in starter_names]
    if bench:
        bench_rows = [{
            "Player": p["name"], "Pos": p["pos"], "Team": p["team"],
            "Bye": f"Week {p['bye']}" if p["bye"] is not None else "N/A",
            "Proj Pts": f"{p['fpts']:.1f}",
        } for p in bench]
        st.write(f"**Bench** ({len(bench)})")
        st.table(pd.DataFrame(bench_rows))
else:
    st.write("_None yet_")

st.subheader("Full Draft Log")
if st.session_state.draft_log:
    log_df = pd.DataFrame(st.session_state.draft_log)[["pick_number", "name", "team"]]
    log_df.columns = ["Pick #", "Player", "Team"]
    st.dataframe(log_df.sort_values("Pick #", ascending=False), use_container_width=True, hide_index=True)

    last_pick = st.session_state.draft_log[-1]
    st.warning(
        f"Next undo removes: **Pick {last_pick['pick_number']} — "
        f"{last_pick['name']} ({last_pick['team']})**"
    )
    if st.button("⏪ Undo last pick"):
        undo_last_pick()
        st.rerun()
    st.caption(
        "Click repeatedly to walk backwards through several picks in order "
        "if you need to correct something from a few picks back."
    )
else:
    st.write("_No picks logged yet._")


# ============ DRAFT GRADES ============
BEST_PICK_LINES = [
    "stole {name} at pick {pick} like it was on clearance ({surplus:+.1f} pts over expected)",
    "landed {name} in round {round_num}, which should be studied in draft-strategy textbooks ({surplus:+.1f} pts of pure theft)",
    "got {name} for pennies on the dollar — {surplus:+.1f} points better than the market said to expect",
    "somehow talked the room out of {name} until pick {pick} ({surplus:+.1f} pts of free value)",
    "walked away with {name} at a price that should require a note from their agent ({surplus:+.1f} pts)",
    "found {name} sitting on the shelf and quietly checked out with {surplus:+.1f} points of value",
    "turned pick {pick} into highway robbery, coming away with {name} ({surplus:+.1f} pts)",
    "let {name} fall right into their lap in round {round_num} ({surplus:+.1f} pts of value nobody else wanted)",
    "got {name} at a discount so steep it should be illegal ({surplus:+.1f} pts)",
    "snuck away with {name} while everyone else was looking elsewhere ({surplus:+.1f} pts of value)",
    "picked {name} at exactly the right moment and nobody else noticed ({surplus:+.1f} pts)",
    "turned {name} into the steal of the draft at pick {pick} ({surplus:+.1f} pts)",
]
WORST_PICK_LINES = [
    "reached hard for {name} at pick {pick}, paying {surplus_abs:.1f} points above sticker price",
    "took {name} well before the market was ready to let go ({surplus_abs:.1f} pts of overpayment)",
    "panicked and grabbed {name} — the room let out an audible 'why' ({surplus_abs:.1f} pts of reach)",
    "fell in love with {name} about two rounds too early ({surplus_abs:.1f} pts overpaid)",
    "spent draft capital on {name} like it was going out of style ({surplus_abs:.1f} pts of reach)",
    "took {name} at pick {pick} for reasons only they understand ({surplus_abs:.1f} pts overpaid)",
    "jumped the line for {name} and paid full retail plus tip ({surplus_abs:.1f} pts of reach)",
    "drafted {name} on vibes alone ({surplus_abs:.1f} pts above where the market had them)",
    "got a little too excited about {name} and it cost them ({surplus_abs:.1f} pts of reach)",
    "reached for {name} so early even their own bench looked confused ({surplus_abs:.1f} pts overpaid)",
    "bought {name} at the top of the market ({surplus_abs:.1f} pts of overpayment)",
    "took {name} like it was the last one on the shelf ({surplus_abs:.1f} pts of reach)",
]


def build_expected_value_curve(board_df, total_picks):
    """
    Expected VBD at each pick number, based on ADP consensus. Beyond where
    real ADP data reaches, falls back to overall VBD rank as the expectation
    (i.e. 'what's the Nth-best value player available' with no market
    inefficiency assumed).
    """
    adp_ranked = board_df.dropna(subset=["adp_rank"]).sort_values("adp_rank")
    adp_lookup = dict(zip(adp_ranked["adp_rank"].astype(int), adp_ranked["vbd_value"]))
    vbd_sorted = board_df.sort_values("vbd_value", ascending=False).reset_index(drop=True)

    curve = {}
    for pick in range(1, total_picks + 1):
        if pick in adp_lookup:
            curve[pick] = adp_lookup[pick]
        elif pick - 1 < len(vbd_sorted):
            curve[pick] = vbd_sorted.iloc[pick - 1]["vbd_value"]
        else:
            curve[pick] = 0.0
    return curve


def compute_draft_grades():
    total_picks_needed = max(len(st.session_state.draft_log), st.session_state.num_teams)
    curve = build_expected_value_curve(board, total_picks_needed)

    per_pick = []
    for entry in st.session_state.draft_log:
        pick_num = entry["pick_number"]
        name = entry["name"]
        team = entry["team"]
        match = board.loc[board["name"] == name]
        actual_vbd = float(match.iloc[0]["vbd_value"]) if not match.empty else 0.0
        expected_vbd = curve.get(pick_num, 0.0)
        surplus = round(actual_vbd - expected_vbd, 1)
        per_pick.append({
            "pick_number": pick_num, "name": name, "team": team,
            "actual_vbd": round(actual_vbd, 1), "expected_vbd": round(expected_vbd, 1),
            "surplus": surplus,
        })

    team_summary = {}
    for t in st.session_state.team_names:
        picks = [p for p in per_pick if p["team"] == t]
        if not picks:
            continue
        total_surplus = round(sum(p["surplus"] for p in picks), 1)
        avg_surplus = round(total_surplus / len(picks), 2)
        best = max(picks, key=lambda p: p["surplus"])
        worst = min(picks, key=lambda p: p["surplus"])
        team_summary[t] = {
            "total_surplus": total_surplus, "avg_surplus": avg_surplus,
            "best": best, "worst": worst, "picks": picks,
        }
    return team_summary


def assign_grades(team_summary):
    """
    Absolute grading: each team's grade reflects how much surplus value
    THEY generated per pick, not how they compare to the rest of the
    field. Thresholds are a calibrated heuristic (avg VBD surplus per
    pick), not a scientific scale -- tune ABSOLUTE_GRADE_BANDS if a
    league's typical surplus range runs noticeably hot or cold.
    """
    ranked = sorted(team_summary.items(), key=lambda kv: -kv[1]["total_surplus"])
    grades = {}
    for team, summary in ranked:
        avg = summary["avg_surplus"]
        grade = ABSOLUTE_GRADE_BANDS[-1][1]  # default to worst band (F)
        for threshold, band_grade in ABSOLUTE_GRADE_BANDS:
            if avg >= threshold:
                grade = band_grade
                break
        grades[team] = grade
    return grades, ranked


ABSOLUTE_GRADE_BANDS = [
    (8.0, "A+"), (5.0, "A"), (3.0, "A-"),
    (1.5, "B+"), (0.5, "B"), (-0.5, "B-"),
    (-1.5, "C+"), (-3.0, "C"), (-5.0, "C-"),
    (-8.0, "D"), (float("-inf"), "F"),
]


def assign_unique_templates(templates, n, seed):
    """
    Assigns n templates with no repeats until the pool is exhausted (only
    then does it reshuffle and potentially repeat) -- guarantees variety
    across teams in a single grading run instead of each team picking
    independently, which could (and did) produce duplicate lines.
    """
    rng = random.Random(seed)
    assigned = []
    pool = []
    while len(assigned) < n:
        if not pool:
            pool = templates.copy()
            rng.shuffle(pool)
            if assigned and pool[0] == assigned[-1] and len(pool) > 1:
                pool[0], pool[1] = pool[1], pool[0]
        assigned.append(pool.pop())
    return assigned


def witty_lines_for_team(summary, best_template, worst_template):
    best, worst = summary["best"], summary["worst"]
    best_round = ((best["pick_number"] - 1) // st.session_state.num_teams) + 1
    best_line = best_template.format(
        name=best["name"], pick=best["pick_number"], surplus=best["surplus"], round_num=best_round
    )
    worst_line = worst_template.format(
        name=worst["name"], pick=worst["pick_number"], surplus_abs=abs(worst["surplus"])
    )
    return best_line, worst_line


st.markdown("---")
st.subheader("🎓 Draft Grades")
st.caption(
    "Surplus value = actual VBD delivered minus the VBD expected at that pick, based on "
    "ADP consensus (or overall VBD rank where ADP data doesn't reach). Grades are absolute -- "
    "based on each team's own average surplus per pick -- not curved against how the other "
    "teams in this draft did. The commentary is templated for now, not AI-generated; "
    "that's a planned upgrade."
)

total_picks_completed = len(st.session_state.draft_log)
total_picks_needed = st.session_state.num_teams * total_rounds
draft_complete = total_picks_completed >= total_picks_needed

show_grades = draft_complete
if not draft_complete and total_picks_completed > 0:
    show_grades = st.checkbox("Preview grades now (draft isn't finished yet)")

if total_picks_completed == 0:
    st.write("_No picks yet._")
elif show_grades:
    team_summary = compute_draft_grades()
    grades, ranked = assign_grades(team_summary)

    n = len(ranked)
    best_templates = assign_unique_templates(BEST_PICK_LINES, n, seed=42)
    worst_templates = assign_unique_templates(WORST_PICK_LINES, n, seed=99)

    # League-wide summary table first
    surpluses = [s["total_surplus"] for _, s in ranked]
    avg_surplus = round(sum(surpluses) / len(surpluses), 1) if surpluses else 0.0
    top_team, top_summary = ranked[0]
    bottom_team, bottom_summary = ranked[-1]

    st.markdown(
        f"**League summary:** {top_team} drafted the best value this league has seen "
        f"({grades[top_team]}, {top_summary['total_surplus']:+.1f} pts / "
        f"{top_summary['avg_surplus']:+.1f} per pick), while {bottom_team} had the roughest "
        f"draft by comparison ({grades[bottom_team]}, {bottom_summary['total_surplus']:+.1f} pts / "
        f"{bottom_summary['avg_surplus']:+.1f} per pick). Grades below are absolute -- tied to "
        f"each team's own performance, not a curve against this room. "
        f"League-average surplus: {avg_surplus:+.1f} pts total."
    )
    summary_table = pd.DataFrame([
        {"Rank": i + 1, "Team": team, "Grade": grades[team], "Total Surplus": f"{s['total_surplus']:+.1f}",
         "Avg / Pick": f"{s['avg_surplus']:+.2f}"}
        for i, (team, s) in enumerate(ranked)
    ])
    st.table(summary_table)

    st.markdown("#### Team-by-team breakdown")
    for i, (team, summary) in enumerate(ranked):
        grade = grades[team]
        best_line, worst_line = witty_lines_for_team(summary, best_templates[i], worst_templates[i])
        st.markdown(f"**{team} — Grade: {grade}**  (surplus {summary['total_surplus']:+.1f} pts)")
        st.write(f"✅ Best pick: {best_line}")
        st.write(f"❌ Worst pick: {worst_line}")
else:
    st.write(
        f"_Draft in progress ({total_picks_completed}/{total_picks_needed} picks made) -- "
        "grades available once complete, or check the preview box above."
    )

st.markdown(
    """
    <div style="
        margin-top: 40px;
        padding-top: 14px;
        border-top: 1px solid #1a2540;
        text-align: center;
        color: #2FB35E;
        font-size: 12px;
    ">
        <b>THIRD DOWN AI</b> — We show our work. We keep score.
    </div>
    """,
    unsafe_allow_html=True,
)
