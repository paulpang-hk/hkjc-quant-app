import re
import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch
import streamlit as st

DB_URL = "postgresql://neondb_owner:npg_D2YzinaM8grT@ep-snowy-fire-b59poqzm-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require"

st.set_page_config(page_title="HKJC 量化戰情室", layout="wide")
st.title("🏇 HKJC Quant Command Center")


@st.cache_data(ttl=15)
def load_data():
  with psycopg2.connect(DB_URL) as conn:
    query = """
            SELECT race_date, venue, race_no, horse_no, horse_name, draw, 
                   rating, raw_score, model_pwin, fair_odds, live_odds
            FROM model_pwin_results 
            WHERE race_date = (SELECT MAX(race_date) FROM model_pwin_results)
            ORDER BY race_no, horse_no;
        """
    return pd.read_sql(query, conn)


def update_manual_odds_batch(batch_updates, race_date, race_no):
  """Pushes a list of (odds, race_date, race_no, horse_no) tuples directly to Neon DB"""
  sql = """
        UPDATE model_pwin_results 
        SET live_odds = %s, updated_at = CURRENT_TIMESTAMP
        WHERE race_date = %s AND race_no = %s AND horse_no = %s;
    """
  with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
    execute_batch(cur, sql, batch_updates)


try:
  df = load_data()

  if df.empty:
    st.warning(
        "⚠️ Database is currently empty. Run the daily ingest script first."
    )
  else:
    target_date = df["race_date"].iloc[0]
    venue = df["venue"].iloc[0]

    # --- SIDEBAR: Bankroll & Global Controls ---
    st.sidebar.header("⚙️ Betting Controls")
    bankroll = st.sidebar.number_input(
        "Total Bankroll (HKD)", min_value=1000, value=50000, step=1000
    )
    kelly_fraction = st.sidebar.slider(
        "Kelly Fraction",
        min_value=0.10,
        max_value=0.50,
        value=0.25,
        step=0.05,
    )
    min_ev_threshold = st.sidebar.slider(
        "Min EV Threshold (+EV)",
        min_value=0.0,
        max_value=0.5,
        value=0.05,
        step=0.01,
    )

    # --- HEADER ---
    col1, col2 = st.columns(2)
    with col1:
      st.subheader(f"📅 Race Date: {target_date}")
    with col2:
      st.subheader(f"🏟️ Venue: {venue}")

    st.divider()

    # --- RACE SELECTOR ---
    races = sorted(df["race_no"].unique())
    selected_race = st.selectbox("📌 Select Race Number:", races)

    race_df = df[df["race_no"] == selected_race].copy()

    # --- CALCULATIONS ---
    def compute_metrics(row):
      p_win = row["model_pwin"] if pd.notna(row["model_pwin"]) else 0.0
      odds = row["live_odds"]

      if pd.notna(odds) and odds > 1.0 and p_win > 0:
        ev = (p_win * odds) - 1.0
        if ev > 0:
          k_pct = (ev / (odds - 1.0)) * kelly_fraction
          stake = bankroll * k_pct
          return pd.Series([ev, k_pct, stake])
      return pd.Series([None, 0.0, 0.0])

    race_df[["EV", "Kelly_Pct", "Recommended_Stake"]] = race_df.apply(
        compute_metrics, axis=1
    )

    # --- TOP VALUE BETS (ACTION CARDS) ---
    value_bets = race_df[race_df["EV"] >= min_ev_threshold].sort_values(
        by="EV", ascending=False
    )

    st.markdown("### ⭐ Actionable Opportunities")
    if not value_bets.empty:
      cols = st.columns(min(len(value_bets), 4))
      for idx, (_, bet) in enumerate(value_bets.iterrows()):
        if idx < 4:
          with cols[idx]:
            st.success(
                f"**Race {selected_race} - No. {bet['horse_no']}"
                f" {bet['horse_name']}**\n\n• Live Odds:"
                f" **{bet['live_odds']:.1f}** (Fair:"
                f" {bet['fair_odds']:.1f})\n\n• Expected Value:"
                f" **{bet['EV']:+.2f}**\n\n• Bet Stake: **HKD"
                f" ${bet['Recommended_Stake']:,.0f}**"
                f" ({bet['Kelly_Pct']*100:.1f}%)"
            )
    else:
      st.info("No value bets identified for this race based on current odds.")

    st.divider()

    # --- QUICK TEXT-INPUT ODDS OVERRIDE ---
    st.markdown("### ⚡ Quick Odds Entry")
    st.caption(
        "Format: `1=1.3, 2=7, 3=100` or `1=1.3 2=7 3=100` (Horse No = Odds)"
    )

    quick_odds_input = st.text_input(
        f"Enter Race {selected_race} Odds:",
        placeholder="e.g. 1=1.3, 2=7, 3=100, 4=15.5",
    )

    if st.button("🚀 Apply Quick Odds Update"):
      if quick_odds_input.strip():
        matches = re.findall(
            r"(\d+)\s*=\s*(\d+(?:\.\d+)?)", quick_odds_input
        )
        if matches:
          batch_updates = []
          for h_no_str, odds_str in matches:
            h_no = int(h_no_str)
            odds_val = float(odds_str)
            batch_updates.append((odds_val, target_date, selected_race, h_no))

          try:
            update_manual_odds_batch(
                batch_updates, target_date, selected_race
            )
            st.success(
                f"✅ Updated {len(batch_updates)} horses for Race"
                f" {selected_race}!"
            )
            st.cache_data.clear()
            st.rerun()
          except Exception as ex:
            st.error(f"Failed to update odds: {ex}")
        else:
          st.warning(
              "⚠️ Invalid format. Example format: `1=1.3, 2=7, 3=100`"
          )
      else:
        st.warning("⚠️ Please type some odds first.")

    st.divider()

    # --- TABLE VIEW ---
    st.markdown("### 📊 Racecard Overview")

    display_df = race_df[[
        "horse_no",
        "horse_name",
        "draw",
        "rating",
        "model_pwin",
        "fair_odds",
        "live_odds",
        "EV",
        "Recommended_Stake",
    ]].copy()

    # Format model_pwin into readable percentage strings (e.g., 3.2%)
    display_df["model_pwin"] = (display_df["model_pwin"] * 100).apply(
        lambda x: f"{x:.1f}%" if pd.notna(x) else "-"
    )

    st.dataframe(
        display_df,
        column_config={
            "horse_no": st.column_config.NumberColumn("No."),
            "horse_name": st.column_config.TextColumn("Horse Name"),
            "draw": st.column_config.NumberColumn("Draw"),
            "rating": st.column_config.NumberColumn("Rtg"),
            "model_pwin": st.column_config.TextColumn("P(win)"),
            "fair_odds": st.column_config.NumberColumn(
                "Fair Odds", format="%.1f"
            ),
            "live_odds": st.column_config.NumberColumn(
                "Live Odds", format="%.1f"
            ),
            "EV": st.column_config.NumberColumn("EV", format="%+.2f"),
            "Recommended_Stake": st.column_config.NumberColumn(
                "Stake (HKD)", format="$%d"
            ),
        },
        hide_index=True,
        use_container_width=True,
    )

except Exception as e:
  st.error(f"Error loading dashboard: {e}")
