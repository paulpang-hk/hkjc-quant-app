# -*- coding: utf-8 -*-
import os
import re
import numpy as np
import pandas as pd
import psycopg2
import streamlit as st

# ==========================================
# PAGE CONFIGURATION & STYLING
# ==========================================
st.set_page_config(
    page_title="HKJC Quant Strategy Engine",
    page_icon="🏇",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Dark Theme Custom Styling
st.markdown(
    """
    <style>
    .main { background-color: #0e1117; }
    .stTable { background-color: #1e222d; border-radius: 8px; }
    .metric-card {
        background-color: #1e222d;
        padding: 12px 18px;
        border-radius: 8px;
        border: 1px solid #2e3440;
    }
    .status-badge-green {
        background-color: #1b4332;
        color: #74c69d;
        padding: 6px 12px;
        border-radius: 6px;
        font-weight: bold;
        display: inline-block;
    }
    .status-badge-yellow {
        background-color: #4a3b00;
        color: #ffd166;
        padding: 6px 12px;
        border-radius: 6px;
        font-weight: bold;
        display: inline-block;
    }
    .status-badge-blue {
        background-color: #032b43;
        color: #63b3ed;
        padding: 6px 12px;
        border-radius: 6px;
        font-weight: bold;
        display: inline-block;
    }
    </style>
""",
    unsafe_allow_html=True,
)

# ==========================================
# NEON DB CONNECTION & DATA LOADERS
# ==========================================
NEON_DB_URL = os.environ.get(
    "NEON_DB_URL",
    "postgresql://neondb_owner:npg_D2YzinaM8grT@ep-snowy-fire-b59poqzm-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
  """Creates a fresh, un-cached connection to avoid Neon SSL idle timeouts."""
  return psycopg2.connect(NEON_DB_URL, connect_timeout=10)


def load_race_dates():
  try:
    conn = get_db_connection()
    query = (
        "SELECT DISTINCT race_date FROM model_pwin_results ORDER BY race_date"
        " DESC;"
    )
    df = pd.read_sql(query, conn)
    conn.close()
    return df["race_date"].tolist()
  except Exception as e:
    st.error(f"Error loading race dates from database: {e}")
    return []


def load_race_data(race_date, race_no):
  try:
    conn = get_db_connection()
    query = """
            SELECT * 
            FROM model_pwin_results 
            WHERE race_date = %s AND race_no = %s
            ORDER BY horse_no ASC;
        """
    df = pd.read_sql(query, conn, params=(str(race_date), int(race_no)))
    conn.close()
    return df
  except Exception as e:
    st.error(f"Error loading race data: {e}")
    return pd.DataFrame()


# ==========================================
# DYNAMIC EV & KELLY CALCULATIONS
# ==========================================
def get_dynamic_ev_threshold(race_class):
  text = str(race_class).lower()
  if any(c in text for c in ["class 1", "class 2", "group", "g1", "g2", "g3"]):
    return 0.25
  elif "class 3" in text:
    return 0.15
  else:
    return 0.10


def calculate_kelly_and_ev(df, bankroll=10000.0, kelly_fraction=0.25):
  df = df.copy()

  df["model_pwin"] = pd.to_numeric(
      df["model_pwin"], errors="coerce"
  ).fillna(0.01)
  df["live_odds"] = pd.to_numeric(df["live_odds"], errors="coerce").fillna(0.0)

  # Calculate Fair Odds if missing
  df["fair_odds"] = df["model_pwin"].apply(
      lambda p: round(1.0 / p, 2) if p > 0 else 999.0
  )

  # Expected Value Edge (only when live odds > 0)
  df["ev_edge"] = df.apply(
      lambda r: (r["model_pwin"] * r["live_odds"]) - 1.0
      if r["live_odds"] > 0
      else -1.0,
      axis=1,
  )

  def get_stake(row):
    p = row["model_pwin"]
    b = row["live_odds"] - 1.0
    if b <= 0 or p <= 0:
      return 0.0
    q = 1.0 - p
    f = (b * p - q) / b
    if f <= 0:
      return 0.0
    stake = f * kelly_fraction * bankroll
    return float(np.round(stake, 0))

  df["bet_stake"] = df.apply(get_stake, axis=1)
  return df


# ==========================================
# UI NAVIGATION & HEADER
# ==========================================
st.title("🏇 HKJC Quant Strategy Engine (Cloud Edition)")

race_dates = load_race_dates()

if not race_dates:
  st.warning(
      "No race data found in Neon DB. Please check database connection or run"
      " pipeline."
  )
  st.stop()

col_date, col_race = st.columns([2, 1])

with col_date:
  selected_date = st.selectbox("📅 Select Race Meeting Date", race_dates)

with col_race:
  selected_race_no = st.selectbox(
      "🏁 Select Race Number", list(range(1, 12)), index=0
  )

# Fetch Data
df_race = load_race_data(selected_date, selected_race_no)

if df_race.empty:
  st.info(
      f"No runners found for Date: {selected_date} | Race {selected_race_no}"
  )
  st.stop()

# ==========================================
# FAST MANUAL ODDS OVERRIDE (SIDEBAR)
# ==========================================
manual_odds_active = False

with st.sidebar:
  st.header("⚡ Fast Odds Override")
  st.caption("Input format: `1=4.5, 2=12, 3=2.1`")
  
  manual_odds_str = st.text_area(
      "Type Live Odds Here:", 
      height=120,
      placeholder="1=3.5\n2=4.0\n3=12"
  )
  
  if manual_odds_str.strip():
    # Use Regex to intelligently find all "number=number" patterns
    matches = re.findall(r"(\d+)\s*[=:]\s*(\d+(?:\.\d+)?)", manual_odds_str)
    
    if matches:
      for h_str, odds_str in matches:
        h_no = int(h_str)
        odds_val = float(odds_str)
        # Update the live odds in the dataframe memory directly
        df_race.loc[df_race["horse_no"] == h_no, "live_odds"] = odds_val
      
      manual_odds_active = True
      st.success(f"✅ Applied manual odds for {len(matches)} horses!")
    else:
      st.error("⚠️ Invalid format. Example: 1=4.5, 2=12")

# Auto-detect race class
race_class = (
    df_race["race_class"].iloc[0]
    if "race_class" in df_race.columns
    else "Class 3"
)
dynamic_ev_min = get_dynamic_ev_threshold(race_class)

# Apply Kelly & EV calculations using potentially overridden odds
df_calc = calculate_kelly_and_ev(df_race, bankroll=10000, kelly_fraction=0.25)

# ==========================================
# ODDS INGESTION STATUS INDICATOR BAR
# ==========================================
has_db_live_odds = (
    df_calc["live_odds"] > 0
).any() and not (df_calc["live_odds"] == 10.0).all()

col_m1, col_m2, col_m3 = st.columns(3)

with col_m1:
  if manual_odds_active:
    st.markdown(
        '<div class="status-badge-blue">⚡ Manual Odds Override Active</div>',
        unsafe_allow_html=True,
    )
  elif has_db_live_odds:
    st.markdown(
        '<div class="status-badge-green">🟢 DB Live Odds Active</div>',
        unsafe_allow_html=True,
    )
  else:
    st.markdown(
        '<div class="status-badge-yellow">🟡 Pending Live Odds</div>',
        unsafe_allow_html=True,
    )

with col_m2:
  st.markdown(f"**Total Field:** `{len(df_calc)} Runners`")

with col_m3:
  st.markdown(
      f"**Class Filter:** `{race_class}` | Min EV: `≥ +{dynamic_ev_min:.2f}`"
  )

st.markdown("<br>", unsafe_allow_html=True)

# ==========================================
# 2-MINUTE EXECUTIVE BET SLIP
# ==========================================
st.subheader(
    f"🚨 2-Minute Executive Bet Slip — {selected_date} | Race {selected_race_no}"
)

col_win, col_exotics = st.columns([1, 1])

# --- 1. TOP WIN VALUE BETS (PRIME OVERLAYS) ---
with col_win:
  st.markdown("### 🏆 Top WIN Value Bets (Max 3 Prime Overlays)")

  df_prime = df_calc[
      (df_calc["model_pwin"] >= 0.08)
      & (df_calc["live_odds"] <= 35.0)
      & (df_calc["live_odds"] > 0)
      & (df_calc["ev_edge"] >= dynamic_ev_min)
      & (df_calc["ev_edge"] <= 2.50)
  ].copy()

  if not df_prime.empty:
    df_prime = df_prime.sort_values(by="ev_edge", ascending=False).head(3)

    display_win = pd.DataFrame({
        "Horse": df_prime["horse_no"].astype(str)
        + " "
        + df_prime["horse_name"],
        "Live Odds": df_prime["live_odds"].map("{:.1f}".format),
        "EV Edge": df_prime["ev_edge"].map("+{:.2f}".format),
        "Bet Stake ($)": df_prime["bet_stake"].map("${:,.0f}".format),
    })

    st.table(display_win)
  else:
    st.info(
        f"⚠️ No qualified Prime Overlays (PWIN ≥ 8%, Odds ≤ 35, EV between"
        f" +{dynamic_ev_min:.2f} and +2.50) found. Skip WIN bets for this race."
    )

# --- 2. EXOTICS STRATEGY (BOX 4 COMBINATION) ---
with col_exotics:
  st.markdown("### 🎯 Exotics Strategy (Box 4 Combination)")

  top_pwin = df_calc.sort_values(by="model_pwin", ascending=False).head(2)

  if not df_prime.empty:
    top_overlays = df_prime.head(2)
    box_candidates = (
        pd.concat([top_pwin, top_overlays])
        .drop_duplicates(subset=["horse_no"])
        .head(4)
    )
  else:
    box_candidates = (
        df_calc.sort_values(by="model_pwin", ascending=False)
        .drop_duplicates(subset=["horse_no"])
        .head(4)
    )

  box_numbers = [f"#{int(h)}" for h in box_candidates["horse_no"].tolist()]
  st.success(
      f"**Box 4 Selections: ({', '.join(box_numbers)})** — Max 6 Combinations"
  )

  if len(box_candidates) >= 2:
    p1 = box_candidates.iloc[0]
    p2 = box_candidates.iloc[1]

    prob_q = (p1["model_pwin"] * p2["model_pwin"]) / (
        1.0 - p1["model_pwin"] + 1e-6
    )
    prob_pq = prob_q * 2.2

    fair_q_odds = 1.0 / prob_q if prob_q > 0 else 999.0
    fair_pq_odds = 1.0 / prob_pq if prob_pq > 0 else 999.0

    exotic_df = pd.DataFrame([
        {
            "Type": "Quinella (Q) Best Pair",
            "Pair": f"{int(p1['horse_no'])}-{int(p2['horse_no'])}",
            "Runners": (
                f"#{int(p1['horse_no'])} {p1['horse_name']} + "
                f"#{int(p2['horse_no'])} {p2['horse_name']}"
            ),
            "Fair Min Odds": f"${fair_q_odds:.2f}",
        },
        {
            "Type": "Place Quinella (PQ) Best",
            "Pair": f"{int(p1['horse_no'])}-{int(p2['horse_no'])}",
            "Runners": (
                f"#{int(p1['horse_no'])} {p1['horse_name']} + "
                f"#{int(p2['horse_no'])} {p2['horse_name']}"
            ),
            "Fair Min Odds": f"${fair_pq_odds:.2f}",
        },
    ])

    st.table(exotic_df)

# ==========================================
# FULL FIELD LIVE ODDS & VERIFICATION MATRIX
# ==========================================
st.markdown("---")
with st.expander("📋 Full Field Live Odds & Model Verification Table", expanded=True):
  df_display_all = df_calc.copy()

  def get_value_status(row):
    if row["live_odds"] <= 0 or row["live_odds"] == 10.0:
      return "🟡 Pending Odds"
    elif row["ev_edge"] >= dynamic_ev_min:
      return "🟢 Prime Overlay"
    elif row["ev_edge"] > 0:
      return "🔵 Slight Edge"
    else:
      return "🔴 Underlay"

  df_display_all["Status"] = df_display_all.apply(get_value_status, axis=1)

  df_table = pd.DataFrame({
      "No.": df_display_all["horse_no"].astype(int),
      "Horse": df_display_all["horse_name"],
      "Draw": df_display_all["draw"].astype(int),
      "Model PWIN %": (df_display_all["model_pwin"] * 100).map("{:.1f}%".format),
      "Fair Odds": df_display_all["fair_odds"].map("${:.2f}".format),
      "Live Odds": df_display_all["live_odds"].apply(
          lambda x: f"${x:.1f}" if x > 0 else "N/A"
      ),
      "EV Edge": df_display_all["ev_edge"].apply(
          lambda x: f"+{x:.2f}" if x > -1 else "N/A"
      ),
      "Status": df_display_all["Status"],
  })

  st.dataframe(df_table, use_container_width=True, hide_index=True)
