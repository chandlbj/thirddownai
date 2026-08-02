"""
Third Down AI - All Projections (separate page)

Streamlit auto-discovers any file in a pages/ folder next to the main
draft_app_v14.py and adds it as a separate page with its own nav entry --
no changes needed to the main app for this to show up.

This page shows the full ranked projection list (not just who's still
undrafted, and not filtered to a specific team's recommendation) with a
position filter -- a browsable reference, separate from the live draft
board.

Note: this duplicates the data-loading and scoring logic from the main app
rather than importing it, since draft_app_v14.py isn't structured as an
importable module. Kept deliberately simple and clearly commented so it's
easy to keep in sync if the main scoring engine changes.
"""

import streamlit as st
import pandas as pd
import os
import math
from datetime import datetime

try:
    import requests
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False

st.set_page_config(page_title="Third Down AI - All Projections", page_icon="assets/icon.png", layout="wide")

RAW_FILE = "raw_projections_2026.csv"
ADP_FILE = "adp_data_2026.csv"
FANTASYPROS_BASE = "https://api.fantasypros.com/public/v2/json"

TEAM_BYE_WEEKS = {
    'ARI': 14, 'ATL': 11, 'BAL': 13, 'BUF': 7, 'CAR': 5, 'CHI': 10, 'CIN': 6,
    'CLE': 11, 'DAL': 14, 'DEN': 10, 'DET': 6, 'GB': 11, 'HOU': 8, 'IND': 13,
    'JAX': 7, 'KC': 5, 'LAC': 7, 'LAR': 11, 'LV': 13, 'MIA': 6, 'MIN': 6,
    'NE': 11, 'NO': 8, 'NYG': 8, 'NYJ': 13, 'PHI': 10, 'PIT': 9, 'SEA': 11,
    'SF': 8, 'TB': 10, 'TEN': 9, 'WAS': 7,
}

# Sane defaults in case this page is opened before the main draft board page
# in this session (session_state is shared across pages, but only once the
# main page has actually run and set these).
_scoring_defaults = {
    "sc_pass_yds_per_pt": 25.0, "sc_pass_td": 4.0, "sc_pass_int": -1.0, "sc_pass_2pt": 2.0,
    "sc_rush_yds_per_pt": 10.0, "sc_rush_td": 6.0, "sc_rush_2pt": 2.0,
    "sc_rec": 1.0, "sc_rec_yds_per_pt": 10.0, "sc_rec_td": 6.0, "sc_rec_2pt": 2.0,
    "sc_fum_lost": -2.0, "sc_return_td": 6.0,
    "req_qb": 1, "req_rb": 2, "req_wr": 3, "req_te": 1, "req_flex": 1,
    "num_teams": 10,
}
for k, v in _scoring_defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def get_fantasypros_key():
    try:
        key = st.secrets.get("FANTASYPROS_API_KEY", "")
    except Exception:
        key = ""
    if not key:
        key = os.environ.get("FANTASYPROS_API_KEY", "")
    return key


@st.cache_data(ttl=12 * 3600, show_spinner="Fetching live projections from FantasyPros...")
def fetch_live_projections(api_key, season=2026, scoring="PPR"):
    headers = {"x-api-key": api_key}
    rows = []
    for pos in ["QB", "RB", "WR", "TE"]:
        resp = requests.get(
            f"{FANTASYPROS_BASE}/nfl/{season}/projections",
            headers=headers, params={"position": pos, "scoring": scoring, "week": "0"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        for p in data.get("players", []):
            s = p.get("stats", {})
            rows.append({
                "name": p.get("name", ""), "pos": pos, "team": p.get("team_id", ""),
                "pass_yds": s.get("pass_yds", 0.0) or 0.0, "pass_td": s.get("pass_tds", 0.0) or 0.0,
                "pass_int": s.get("pass_ints", 0.0) or 0.0, "pass_2pt": 0.0,
                "rush_yds": s.get("rush_yds", 0.0) or 0.0, "rush_td": s.get("rush_tds", 0.0) or 0.0,
                "rush_2pt": s.get("2pt_tds", 0.0) or 0.0,
                "rec": s.get("rec_rec", 0.0) or 0.0, "rec_yds": s.get("rec_yds", 0.0) or 0.0,
                "rec_td": s.get("rec_tds", 0.0) or 0.0, "rec_2pt": 0.0,
                "fum_lost": s.get("fumbles", 0.0) or 0.0, "ret_td": s.get("ret_tds", 0.0) or 0.0,
            })
    df = pd.DataFrame(rows)
    df["bye"] = df["team"].map(TEAM_BYE_WEEKS)
    return df, datetime.now()


@st.cache_data
def load_static_raw():
    raw_df = pd.read_csv(RAW_FILE)
    if "ret_td" not in raw_df.columns:
        raw_df["ret_td"] = 0.0
    return raw_df


def merge_live_with_static(live_df, static_df):
    """FantasyPros' free tier truncates responses to ~10 players per
    position -- merge live data in for whichever players it covers,
    keeping the full static file for the rest so the list stays complete."""
    merged = static_df.copy()
    live_indexed = live_df.set_index("name")
    overlap_cols = [c for c in
                    ["team", "bye", "pass_yds", "pass_td", "pass_int", "pass_2pt",
                     "rush_yds", "rush_td", "rush_2pt", "rec", "rec_yds", "rec_td",
                     "rec_2pt", "fum_lost", "ret_td"]
                    if c in live_indexed.columns]
    for col in overlap_cols:
        live_values = merged["name"].map(live_indexed[col])
        merged[col] = live_values.combine_first(merged[col])
    new_players = live_df[~live_df["name"].isin(merged["name"])]
    if len(new_players) > 0:
        merged = pd.concat([merged, new_players], ignore_index=True)
    return merged


def load_raw():
    static_df = load_static_raw()
    api_key = get_fantasypros_key()
    if api_key and HAVE_REQUESTS:
        try:
            live_df, fetched_at = fetch_live_projections(api_key)
            st.session_state["_projections_fetch_signature"] = fetched_at
            return merge_live_with_static(live_df, static_df)
        except Exception:
            pass
    st.session_state["_projections_fetch_signature"] = None
    return static_df


raw = load_raw()
try:
    adp_df = pd.read_csv(ADP_FILE)[["name", "adp_rank"]]
    raw = raw.merge(adp_df, on="name", how="left")
except FileNotFoundError:
    raw["adp_rank"] = None


def compute_points_and_vbd(df, s):
    df = df.copy()
    df["fpts"] = (
        df["pass_yds"] / s["sc_pass_yds_per_pt"] + df["pass_td"] * s["sc_pass_td"]
        + df["pass_int"] * s["sc_pass_int"] + df["pass_2pt"] * s["sc_pass_2pt"]
        + df["rush_yds"] / s["sc_rush_yds_per_pt"] + df["rush_td"] * s["sc_rush_td"]
        + df["rush_2pt"] * s["sc_rush_2pt"] + df["rec"] * s["sc_rec"]
        + df["rec_yds"] / s["sc_rec_yds_per_pt"] + df["rec_td"] * s["sc_rec_td"]
        + df["rec_2pt"] * s["sc_rec_2pt"] + df["fum_lost"] * s["sc_fum_lost"]
        + df.get("ret_td", 0) * s["sc_return_td"]
    ).round(2)

    required = {"QB": s["req_qb"], "RB": s["req_rb"], "WR": s["req_wr"], "TE": s["req_te"]}
    num_teams = s["num_teams"]
    flex_eligible = {"RB", "WR", "TE"}
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

    leftover = [by_pos[p].iloc[req_counts[p]:] for p in required if p in flex_eligible]
    leftover_df = pd.concat(leftover).sort_values("fpts", ascending=False) if leftover else pd.DataFrame(columns=df.columns)
    flex_pool = leftover_df.head(flex_slots) if flex_slots > 0 else leftover_df.head(0)
    flex_positions_present = set(flex_pool["pos"].unique())
    flex_baseline = flex_pool["fpts"].min() if len(flex_pool) > 0 else None

    for p in required:
        if p in flex_eligible and p in flex_positions_present and flex_baseline is not None:
            baselines[p] = min(baselines[p], flex_baseline)

    df["vbd_value"] = df.apply(lambda r: round(r["fpts"] - baselines.get(r["pos"], 0), 1), axis=1)
    return df


board = compute_points_and_vbd(raw, st.session_state)

# Stat-change tracking ("we show our work"): compare this fetch against the
# previous one and remember what changed, so the table can show a ▲/▼ and
# the delta next to any stat that moved -- including overall projected
# points, which drives the "biggest change" sort option. Only recompute
# when a genuinely NEW fetch happened (tracked via the fetch timestamp
# signature) -- a trivial rerun (typing in search, changing the position
# filter) reuses the same cached data and shouldn't reset the comparison.
DELTA_STAT_COLS = ["pass_yds", "pass_td", "pass_int", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td"]
SNAPSHOT_COLS = DELTA_STAT_COLS + ["fpts"]
current_signature = st.session_state.get("_projections_fetch_signature")
last_seen_signature = st.session_state.get("_projections_last_signature")

if current_signature is not None and current_signature != last_seen_signature:
    prev_snapshot = st.session_state.get("_projections_prev_snapshot")
    if prev_snapshot is not None:
        deltas = {}
        pts_deltas = {}
        for _, row in board.iterrows():
            prev_row = prev_snapshot.get(row["name"])
            if prev_row is None:
                continue
            row_deltas = {}
            for col in DELTA_STAT_COLS:
                change = round(row[col] - prev_row.get(col, row[col]), 1)
                if abs(change) >= 0.5:  # ignore trivial noise-level rounding shifts
                    row_deltas[col] = change
            if row_deltas:
                deltas[row["name"]] = row_deltas
            pts_change = round(row["fpts"] - prev_row.get("fpts", row["fpts"]), 1)
            if abs(pts_change) >= 0.5:
                pts_deltas[row["name"]] = pts_change
        st.session_state["_projections_deltas"] = deltas
        st.session_state["_projections_pts_deltas"] = pts_deltas
    st.session_state["_projections_prev_snapshot"] = {
        r["name"]: {c: r[c] for c in SNAPSHOT_COLS} for _, r in board.iterrows()
    }
    st.session_state["_projections_last_signature"] = current_signature

stat_deltas = st.session_state.get("_projections_deltas", {})
pts_deltas = st.session_state.get("_projections_pts_deltas", {})
board["_pts_delta"] = board["name"].map(pts_deltas).fillna(0.0)

st.image("assets/banner_cropped.png", use_container_width=True)
st.title("All Projections")
st.caption(
    "Full ranked list of every player's projected value under your league's scoring -- "
    "browsable reference, independent of the live draft board."
)

col1, col2, col3 = st.columns([1, 2, 1])
with col1:
    pos_filter = st.selectbox("Position", ["ALL", "QB", "RB", "WR", "TE"])
with col2:
    search = st.text_input("Search player name")
with col3:
    sort_by = st.selectbox(
        "Sort by", ["VBD Value", "Projected Points", "Biggest Increase", "Biggest Decrease"]
    )

view = board.copy()
if pos_filter != "ALL":
    view = view[view["pos"] == pos_filter]
if search:
    view = view[view["name"].str.contains(search, case=False, na=False)]

if sort_by == "Biggest Increase":
    view = view[view["_pts_delta"] > 0]
    view = view.sort_values("_pts_delta", ascending=False).reset_index(drop=True)
elif sort_by == "Biggest Decrease":
    view = view[view["_pts_delta"] < 0]
    view = view.sort_values("_pts_delta", ascending=True).reset_index(drop=True)
else:
    sort_col = "vbd_value" if sort_by == "VBD Value" else "fpts"
    view = view.sort_values(sort_col, ascending=False).reset_index(drop=True)
view.insert(0, "Rank", range(1, len(view) + 1))

if sort_by in ("Biggest Increase", "Biggest Decrease") and len(view) == 0:
    st.info(
        "No tracked point changes yet -- this compares against the previous live refresh, "
        "so it needs at least one refresh with actual data changes to show anything here."
    )

# Position-tailored stat columns -- a QB's relevant detail (passing) is
# totally different from a RB/WR/TE's (rushing/receiving), so show the
# stats that actually matter for whichever position is selected. When
# viewing ALL positions together, fall back to a compact universal set.
base_cols = ["Rank", "name", "pos", "team", "bye"]
base_labels = ["Rank", "Player", "Pos", "Team", "Bye"]

if pos_filter == "QB":
    stat_cols = ["pass_yds", "pass_td", "pass_int", "rush_yds", "rush_td", "fum_lost"]
    stat_labels = ["Pass Yds", "Pass TD", "INT", "Rush Yds", "Rush TD", "Fum Lost"]
elif pos_filter == "RB":
    stat_cols = ["rush_yds", "rush_td", "rec", "rec_yds", "rec_td", "fum_lost"]
    stat_labels = ["Rush Yds", "Rush TD", "Rec", "Rec Yds", "Rec TD", "Fum Lost"]
elif pos_filter in ("WR", "TE"):
    stat_cols = ["rec", "rec_yds", "rec_td", "rush_yds", "rush_td", "fum_lost"]
    stat_labels = ["Rec", "Rec Yds", "Rec TD", "Rush Yds", "Rush TD", "Fum Lost"]
else:
    # ALL positions mixed together -- a compact set that's meaningful across
    # every position rather than a huge sparse table.
    stat_cols = ["pass_yds", "rush_yds", "rec_yds", "rush_td", "rec_td"]
    stat_labels = ["Pass Yds", "Rush Yds", "Rec Yds", "Rush TD", "Rec TD"]

end_cols = ["fpts", "vbd_value", "adp_rank"]
end_labels = ["Proj Pts", "VBD Value", "ADP"]
if sort_by in ("Biggest Increase", "Biggest Decrease"):
    end_cols = ["fpts", "_pts_delta", "vbd_value", "adp_rank"]
    end_labels = ["Proj Pts", "Pts Δ", "VBD Value", "ADP"]

display_df = view[base_cols + stat_cols + end_cols].copy()

# Annotate any changed stat with a ▲/▼ and the delta amount, comparing
# against the previous live refresh -- "we show our work" applied to the
# projections themselves, not just draft reasoning.
def format_stat_cell(player_name, col, value):
    delta = stat_deltas.get(player_name, {}).get(col)
    val_str = f"{value:.0f}"
    if delta is None or abs(delta) < 0.5:
        return val_str
    arrow = "▲" if delta > 0 else "▼"
    return f"{val_str} {arrow}{abs(delta):.0f}"


for col in stat_cols:
    display_df[col] = [
        format_stat_cell(name, col, val) for name, val in zip(view["name"], view[col])
    ]

display_df.columns = base_labels + stat_labels + end_labels
display_df["Bye"] = display_df["Bye"].apply(lambda b: f"Week {int(b)}" if pd.notna(b) else "N/A")
display_df["ADP"] = display_df["ADP"].apply(lambda a: int(a) if pd.notna(a) else "—")

number_col_config = {"Proj Pts": st.column_config.NumberColumn(format="%.1f"),
                      "VBD Value": st.column_config.NumberColumn(format="%.1f")}
if "Pts Δ" in display_df.columns:
    number_col_config["Pts Δ"] = st.column_config.NumberColumn(format="%+.1f")

st.dataframe(
    display_df, use_container_width=True, hide_index=True, height=800,
    column_config=number_col_config,
)
if any(stat_deltas.values()):
    st.caption("▲/▼ shows the change in that stat since the last live refresh.")
if pos_filter == "ALL":
    st.caption(f"Showing {len(display_df)} players. Filter to a specific position above for its full stat breakdown (e.g. completions/INTs for QB, receptions for WR/TE).")
else:
    st.caption(f"Showing {len(display_df)} players.")
