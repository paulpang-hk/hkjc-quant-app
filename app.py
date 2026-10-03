# -*- coding: utf-8 -*-
import math
import os
import pandas as pd
import psycopg2
import streamlit as st

# ==============================================================================
# STREAMLIT PAGE CONFIGURATION
# ==============================================================================
st.set_page_config(
    page_title="HKJC Quant PWIN Dashboard",
    page_icon="🏇",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Dark Mode CSS Styling for Signal Badges
st.markdown(
    """
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #161b22; padding: 12px; border-radius: 8px; border: 1px solid #30363d; }
    .go-badge { background-color: #1e3d2f; padding: 12px; border-radius: 8px; border-left: 6px solid #2ea043; margin-bottom: 10px; }
    .skip-badge { background-color: #3d1e1e; padding: 12px; border-radius: 8px; border-left: 6px solid #da3633; margin-bottom: 10px; }
    .neutral-badge { background-color: #161b22; padding: 12px; border-radius: 8px; border-left: 6px solid #8b949e; margin-bottom: 10px; }
    </style>
""",
    unsafe_allow_html=True,
)

# ==============================================================================
# DATABASE CONNECTION MANAGER
# ==============================================================================
NEON_DB_URL_DEFAULT = "postgresql://neondb_owner:npg_D2YzinaM8grT@ep-snowy-fire-b59poqzm-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require"

NAS_DB_CONFIG = {
    "dbname": "hkracing",
    "user": "quant_user",
    "password": "YourStrongPassword123!",
    "host": "127.0.0.1",
    "port": 5434,
    "connect_timeout": 3,
}


@st.cache_resource(ttl=60)
def get_db_connection():
  """Attempts connection to Streamlit Secrets / Cloud DB first, falling back to local NAS DB."""
  neon_url = st.secrets.get(
      "NEON_DB_URL", os.environ.get("NEON_DB_URL", NEON_DB_URL_DEFAULT)
  )

  try:
    conn = psycopg2.connect(neon_url, connect_timeout=5)
    return conn, "Neon Cloud DB"
  except Exception:
    try:
      conn = psycopg2.connect(**NAS_DB_CONFIG)
      return conn, "Local NAS DB"
    except Exception as e:
      st.error(f"⚠️ Critical Database Connection Failure: {e}")
      st.stop()


@st.cache_data(ttl=15)
def load_race_dates():
  """Fetches all unique available race meeting dates from PostgreSQL."""
  conn, db_source = get_db_connection()
  cur = conn.cursor()
  cur.execute(
      "SELECT DISTINCT race_date FROM model_pwin_results ORDER BY race_date"
      " DESC;"
  )
  rows = cur.fetchall()
  cur.close()
  return [str(r[0]) for r in rows], db_source


@st.cache_data(ttl=10)
def load_meeting_data(selected_date):
  """Fetches race card details and model probabilities for the selected date."""
  conn, _ = get_db_connection()
  query = """
        SELECT race_no, horse_no, horse_name, rating, carried_weight, 
               draw, jt_win_pct, raw_score, model_pwin, fair_odds, live_odds
        FROM model_pwin_results
        WHERE race_date = %s
        ORDER BY race_no ASC, horse_no ASC;
    """
  df = pd.read_sql_query(query, conn, params=(selected_date,))
  return df


# ==============================================================================
# SIDEBAR CONTROL PANEL
# ==============================================================================
st.sidebar.title("🏇 HKJC Quant Control")

available_dates, db_source_name = load_race_dates()
st.sidebar.caption(f"Connected to: **{db_source_name}**")

if not available_dates:
  st.sidebar.warning("No race date records found in database.")
  st.stop()

selected_date = st.sidebar.selectbox(
    "📅 Select Race Meeting Date", available_dates, index=0
)

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Staking Parameters")
bankroll = st.sidebar.number_input(
    "Total Bankroll ($HKD)",
    min_value=1000,
    max_value=500000,
    value=5000,
    step=1000,
)
ev_threshold = st.sidebar.slider(
    "Min WIN EV Edge Threshold",
    min_value=0.05,
    max_value=0.50,
    value=0.20,
    step=0.05,
)
exotic_confidence_threshold = st.sidebar.slider(
    "Min Exotic Box 4 PWIN Sum",
    min_value=0.35,
    max_value=0.65,
    value=0.50,
    step=0.05,
)

if st.sidebar.button("🔄 Refresh Live Odds"):
  st.cache_data.clear()
  st.rerun()

# ==============================================================================
# MAIN DASHBOARD HEADING & EXECUTIVE SUMMARY
# ==============================================================================
st.title("🏇 HKJC Quant PWIN Super-Computer Dashboard v200.6")
st.caption(
    f"Active Race Meeting: **{selected_date}** | Automated Fractional Kelly &"
    " Dynamic Confidence Filters"
)

raw_df = load_meeting_data(selected_date)

if raw_df.empty:
  st.warning(f"No race data available for date: {selected_date}")
  st.stop()

# Group runners by Race Number
race_numbers = sorted(raw_df["race_no"].unique())

# Pre-calculate Global Executive Metrics
total_win_bets = 0
total_exotic_races = 0
total_recommended_stake = 0.0

for r_no in race_numbers:
  r_df = raw_df[raw_df["race_no"] == r_no].copy()
  pwins = r_df["model_pwin"].astype(float).tolist()
  harville_sum = sum(p / (1.0 - p) for p in pwins if p < 1.0)

  r_df["p_top2"] = r_df["model_pwin"].apply(
      lambda p: p * (1.0 + (harville_sum - (p / (1.0 - p)))) if p < 1.0 else p
  )
  sorted_top2 = r_df.sort_values(by="p_top2", ascending=False)
  top4_pwin_sum = sorted_top2.head(4)["model_pwin"].sum()

  if top4_pwin_sum >= exotic_confidence_threshold:
    total_exotic_races += 1

  for _, h in r_df.iterrows():
    pwin = float(h["model_pwin"] or 0.0)
    odds = float(h["live_odds"] or 0.0)
    if pwin >= 0.08 and 1.0 < odds <= 35.0:
      ev_edge = (pwin * odds) - 1.0
      if ev_threshold <= ev_edge <= 2.50:
        b = odds - 1.0
        f_kelly = max(0.0, (b * pwin - (1.0 - pwin)) / b)
        stake = round(f_kelly * bankroll * 0.25)
        if stake >= 50:
          total_win_bets += 1
          total_recommended_stake += stake

# Render Metric Bar
mcol1, mcol2, mcol3, mcol4 = st.columns(4)
mcol1.metric("Total Races Today", f"{len(race_numbers)} Races")
mcol2.metric("WIN Overlay Signals", f"{total_win_bets} Bets")
mcol3.metric("Exotics Box 4 Signals", f"{total_exotic_races} Races")
mcol4.metric(
    "Total Recommended Stake", f"${total_recommended_stake:,.0f} HKD"
)

st.markdown("---")

# ==============================================================================
# RACE TAB NAVIGATION & BET SLIP RENDERER
# ==============================================================================
tabs = st.tabs([f"Race {r_no}" for r_no in race_numbers])

for idx, r_no in enumerate(race_numbers):
  with tabs[idx]:
    r_df = raw_df[raw_df["race_no"] == r_no].copy()

    # 1. Calculate Harville Top-2 Finish Probabilities
    pwins = r_df["model_pwin"].astype(float).tolist()
    harville_sum = sum(p / (1.0 - p) for p in pwins if p < 1.0)

    r_df["p_top2"] = r_df["model_pwin"].apply(
        lambda p: (
            round(min(p * (1.0 + (harville_sum - (p / (1.0 - p)))), 0.999), 4)
            if p < 1.0
            else p
        )
    )

    # 2. Identify Qualified WIN Value Overlays
    win_overlays = []
    for _, h in r_df.iterrows():
      pwin = float(h["model_pwin"] or 0.0)
      odds = float(h["live_odds"] or 0.0)

      if pwin >= 0.08 and 1.0 < odds <= 35.0:
        ev_edge = (pwin * odds) - 1.0
        if ev_threshold <= ev_edge <= 2.50:
          b = odds - 1.0
          f_kelly = max(0.0, (b * pwin - (1.0 - pwin)) / b)
          stake = round(f_kelly * bankroll * 0.25)
          if stake >= 50:
            win_overlays.append({
                "horse_no": int(h["horse_no"]),
                "horse_name": h["horse_name"],
                "odds": odds,
                "pwin": pwin,
                "ev_edge": ev_edge,
                "stake": stake,
            })

    win_overlays = sorted(win_overlays, key=lambda x: x["ev_edge"], reverse=True)[
        :3
    ]

    # 3. Identify Top 4 Harville Exotic Candidates
    sorted_top2 = r_df.sort_values(by="p_top2", ascending=False)
    top4_pwin_sum = sorted_top2.head(4)["model_pwin"].sum()
    exotic_box = sorted_top2.head(4)["horse_no"].astype(int).tolist()

    # ----------------------------------------------------------------------
    # VISUAL UI BADGES (2-COLUMN RECOMMENDATION SLIP)
    # ----------------------------------------------------------------------
    st.subheader(f"⚡ Race {r_no} Live Recommendation Slip")
    col_win, col_exotic = st.columns(2)

    # --- WIN BET COLUMN ---
    with col_win:
      st.markdown("##### 🎯 WIN Bet Recommendation")
      if win_overlays:
        st.success(f"🟢 **WIN GO SIGNAL** ({len(win_overlays)} Overlay Found)")
        for bet in win_overlays:
          st.markdown(
              f"""
                        <div class="go-badge">
                            <span style="color: #ffffff; font-weight: bold; font-size: 17px;">
                                #{bet['horse_no']} {bet['horse_name']}
                            </span><br/>
                            <span style="color: #3fb950; font-weight: bold;">Live Odds: ${bet['odds']:.1f}</span> | 
                            <span style="color: #58a6ff;">EV Edge: +{bet['ev_edge']:.2f}</span> | 
                            <span style="color: #d2a8ff;">Model PWIN: {bet['pwin']:.1%}</span><br/>
                            <span style="color: #e6edf3; font-size: 15px;">Recommended Stake: <b>${bet['stake']} HKD</b></span>
                        </div>
                        """,
              unsafe_allow_html=True,
          )
      else:
        st.markdown(
            f"""
                    <div class="neutral-badge">
                        <span style="color: #8b949e; font-size: 15px;">⚪ <b>NO WIN BET</b></span><br/>
                        <span style="color: #c9d1d9; font-size: 13px;">No runners meet the strictly required EV edge threshold (EV &ge; +{ev_threshold:.2f}).</span>
                    </div>
                    """,
            unsafe_allow_html=True,
        )

    # --- EXOTICS BOX COLUMN ---
    with col_exotic:
      st.markdown("##### 🎲 Box 4 Exotics Recommendation")
      if top4_pwin_sum >= exotic_confidence_threshold:
        st.success(
            "🟢 **EXOTICS GO SIGNAL** (Top 4 Confidence:"
            f" {top4_pwin_sum:.1%})"
        )
        st.markdown(
            f"""
                    <div class="go-badge">
                        <span style="color: #8b949e; font-size: 13px;">Target Quinella / QP Box 4 Selections:</span><br/>
                        <span style="color: #3fb950; font-size: 24px; font-weight: bold;">
                            {exotic_box}
                        </span><br/>
                        <span style="color: #e6edf3; font-size: 13px;">Top 4 Combined Win Probability: <b>{top4_pwin_sum:.1%}</b> (&ge; {exotic_confidence_threshold:.0%})</span>
                    </div>
                    """,
            unsafe_allow_html=True,
        )
      else:
        st.markdown(
            f"""
                    <div class="skip-badge">
                        <span style="color: #f85149; font-weight: bold; font-size: 15px;">🔴 EXOTICS SKIP</span><br/>
                        <span style="color: #c9d1d9; font-size: 13px;">Low Confidence ({top4_pwin_sum:.1%} &lt; {exotic_confidence_threshold:.0%}). Win probability is spread thin across the field. Save exotic capital.</span><br/>
                        <span style="color: #8b949e; font-size: 12px;">Raw Top 4 Harville Rank: {exotic_box}</span>
                    </div>
                    """,
            unsafe_allow_html=True,
        )

    # ----------------------------------------------------------------------
    # DETAILED RACE FIELD MATRIX TABLE
    # ----------------------------------------------------------------------
    st.markdown("##### 📊 Full Field Probability & Edge Matrix")

    r_df["ev_edge"] = (r_df["model_pwin"] * r_df["live_odds"]) - 1.0
    r_df["kelly_stake"] = r_df.apply(
        lambda h: (
            round(
                max(
                    0.0,
                    (
                        (h["live_odds"] - 1.0) * h["model_pwin"]
                        - (1.0 - h["model_pwin"])
                    )
                    / (h["live_odds"] - 1.0),
                )
                * bankroll
                * 0.25
            )
            if (
                h["model_pwin"] >= 0.08
                and 1.0 < h["live_odds"] <= 35.0
                and h["ev_edge"] >= ev_threshold
            )
            else 0
        ),
        axis=1,
    )

    # Format Data for Clean Table Display
    display_df = pd.DataFrame({
        "No.": r_df["horse_no"].astype(int),
        "Horse Name": r_df["horse_name"],
        "Rating": r_df["rating"].astype(int),
        "Draw": r_df["draw"].astype(int),
        "Model PWIN": r_df["model_pwin"].map(lambda x: f"{x:.1%}"),
        "Harville Top-2": r_df["p_top2"].map(lambda x: f"{x:.1%}"),
        "Fair Odds": r_df["fair_odds"].map(lambda x: f"${x:.2f}"),
        "Live Odds": r_df["live_odds"].map(
            lambda x: f"${x:.1f}" if x > 0 else "N/A"
        ),
        "EV Edge": r_df["ev_edge"].map(
            lambda x: f"+{x:.2f}" if x > 0 else f"{x:.2f}"
        ),
        "Kelly Stake": r_df["kelly_stake"].map(
            lambda x: f"${x:.0f}" if x >= 50 else "-"
        ),
    })

    st.dataframe(
        display_df.sort_values(by="Model PWIN", ascending=False),
        use_container_width=True,
        hide_index=True,
    )
