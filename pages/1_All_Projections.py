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
    return df


@st.cache_data
def load_static_raw():
    raw_df = pd.read_csv(RAW_FILE)
    if "ret_td" not in raw_df.columns:
        raw_df["ret_td"] = 0.0
    return raw_df


def load_raw():
    api_key = get_fantasypros_key()
    if api_key and HAVE_REQUESTS:
        try:
            return fetch_live_projections(api_key)
        except Exception:
            pass
    return load_static_raw()


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
    sort_by = st.selectbox("Sort by", ["VBD Value", "Projected Points"])

view = board.copy()
if pos_filter != "ALL":
    view = view[view["pos"] == pos_filter]
if search:
    view = view[view["name"].str.contains(search, case=False, na=False)]

sort_col = "vbd_value" if sort_by == "VBD Value" else "fpts"
view = view.sort_values(sort_col, ascending=False).reset_index(drop=True)
view.insert(0, "Rank", range(1, len(view) + 1))

display_df = view[["Rank", "name", "pos", "team", "bye", "fpts", "vbd_value", "adp_rank"]].copy()
display_df.columns = ["Rank", "Player", "Pos", "Team", "Bye", "Proj Pts", "VBD Value", "ADP"]
display_df["Bye"] = display_df["Bye"].apply(lambda b: f"Week {int(b)}" if pd.notna(b) else "N/A")
display_df["ADP"] = display_df["ADP"].apply(lambda a: int(a) if pd.notna(a) else "—")

st.dataframe(
    display_df, use_container_width=True, hide_index=True, height=800,
    column_config={
        "Proj Pts": st.column_config.NumberColumn(format="%.1f"),
        "VBD Value": st.column_config.NumberColumn(format="%.1f"),
    },
)
st.caption(f"Showing {len(display_df)} players.")
