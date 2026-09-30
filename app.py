# -*- coding: utf-8 -*-
import os
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
        padding: 15px;
        border-radius: 8px;
        border: 1px solid #2e3440;
    }
    .bet-slip-container {
        background-color: #131722;
        padding: 20px;
        border-radius: 10px;
        border: 1px solid #2a2e39;
        margin-bottom: 20px;
    }
    </style>
""",
    unsafe_allow_html=True,
)

# ==========================================
# NEON DB CONNECTION & DATA LOADERS (AUTO-RECONNECT FIX)
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
    conn.close()  # Safely close connection
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
    conn.close()  # Safely close connection
    return df
  except Exception as e:
    st.error(f"Error loading race data: {e}")
    return pd.DataFrame()


# ==========================================
# DYNAMIC EV & KELLY CALCULATIONS
# ==========================================
def get_dynamic_ev_threshold(race_class):
  """Returns dynamic EV threshold based on market efficiency per race class.

  - Class 1 / 2 / Group: High efficiency -> Strict EV (>= +0.25)
  - Class 3: Standard efficiency -> Standard EV (>= +0.15)
  - Class 4 / 5 / Griffin / Maiden: High noise -> Looser EV (>= +0.10)
  """
  text = str(race_class).lower()
  if any(c in text for c in ["class 1", "class 2", "group", "g1", "g2", "g3"]):
    return 0.25
  elif "class 3" in text:
    return 0.15
  else:
    return 0.10


def calculate_kelly_and_ev(df, bankroll=10000.0, kelly_fraction=0.25):
  """Calculates EV Edge and fractional Kelly stakes for runners."""
  df = df.copy()

  df["model_pwin"] = pd.to_numeric(
      df["model_pwin"], errors="coerce"
  ).fillna(0.01)
  df["live_odds"] = pd.to_numeric(df["live_odds"], errors="coerce").fillna(10.0)

  # Expected Value Edge
  df["ev_edge"] = (df["model_pwin"] * df["live_odds"]) - 1.0

  # Fractional Kelly Stake Sizing
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

# Auto-detect race class or fallback to Class 3
race_class = (
    df_race["race_class"].iloc[0]
    if "race_class" in df_race.columns
    else "Class 3"
)
dynamic_ev_min = get_dynamic_ev_threshold(race_class)

# Apply Kelly & EV calculations
df_calc = calculate_kelly_and_ev(df_race, bankroll=10000, kelly_fraction=0.25)

# ==========================================
# 2-MINUTE EXECUTIVE BET SLIP
# ==========================================
st.markdown("---")
st.subheader(
    f"🚨 2-Minute Executive Bet Slip — {selected_date} | Race {selected_race_no}"
)
st.caption(
    f"Race Class: **{race_class}** | Dynamic EV Cutoff: **≥"
    f" +{dynamic_ev_min:.2f}**"
)

col_win, col_exotics = st.columns([1, 1])

# --- 1. TOP WIN VALUE BETS (PRIME OVERLAYS) ---
with col_win:
  st.markdown("### 🏆 Top WIN Value Bets (Max 3 Prime Overlays)")

  # Filter Prime Overlays with dynamic EV cutoff & EV cap (<= +2.50)
  df_prime = df_calc[
      (df_calc["model_pwin"] >= 0.08)
      & (df_calc["live_odds"] <= 35.0)
      & (df_calc["ev_edge"] >= dynamic_ev_min)
      & (df_calc["ev_edge"] <= 2.50)
  ].copy()

  if not df_prime.empty:
    df_prime = df_prime.sort_values(by="ev_edge", ascending=False).head(3)

    # Format table output
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

  # Top 2 by Model Probability + Top 2 Prime Overlays
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

  # Calculate best Pair (Q and PQ)
  if len(box_candidates) >= 2:
    p1 = box_candidates.iloc[0]
    p2 = box_candidates.iloc[1]

    prob_q = (p1["model_pwin"] * p2["model_pwin"]) / (
        1.0 - p1["model_pwin"] + 1e-6
    )
    prob_pq = prob_q * 2.2  # Approximate Place Quinella scaling factor

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
# MODEL VS MARKET MISPRICING ANALYSIS
# ==========================================
st.markdown("---")
st.subheader("📊 Model vs. Market Mispricing Analysis")

col_chart1, col_chart2 = st.columns(2)

df_calc["market_implied"] = (1.0 / df_calc["live_odds"]) * 100.0
df_calc["model_pwin_pct"] = df_calc["model_pwin"] * 100.0

with col_chart1:
  st.markdown("**Probability Comparison (Model PWIN % vs. Market Implied %)**")
  chart_data = df_calc[
      ["horse_no", "horse_name", "model_pwin_pct", "market_implied"]
  ].copy()
  chart_data["Horse"] = (
      "#"
      + chart_data["horse_no"].astype(str)
      + " "
      + chart_data["horse_name"]
  )
  chart_data = chart_data.set_index("Horse")[
      ["model_pwin_pct", "market_implied"]
  ]
  st.bar_chart(chart_data)

with col_chart2:
  st.markdown("**Expected Value (EV) Profile by Runner**")
  ev_chart_data = df_calc[["horse_no", "horse_name", "ev_edge"]].copy()
  ev_chart_data["Horse"] = (
      "#"
      + ev_chart_data["horse_no"].astype(str)
      + " "
      + ev_chart_data["horse_name"]
  )
  ev_chart_data = ev_chart_data.set_index("Horse")[["ev_edge"]]
  st.bar_chart(ev_chart_data)
