# -*- coding: utf-8 -*-
import math
import os
import itertools
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
    .audit-badge { background-color: #1f293d; padding: 12px; border-radius: 8px; border-left: 6px solid #388bfd; margin-bottom: 10px; }
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

HISTORICAL_OFFICIAL_RESULTS = {
    "2026-10-01": {
        1: {"win": (7, 124.50), "q": ((2, 7), 554.50), "qp": [((2, 7), 162.00), ((1, 7), 133.00), ((1, 2), 121.00)]},
        2: {"win": (9, 46.50), "q": ((9, 10), 225.00), "qp": [((9, 10), 79.50), ((7, 9), 94.00), ((7, 10), 193.00)]},
        3: {"win": (4, 22.00), "q": ((4, 7), 52.50), "qp": [((4, 7), 23.00), ((4, 6), 32.00), ((6, 7), 44.50)]},
        4: {"win": (7, 19.00), "q": ((6, 7), 77.00), "qp": [((6, 7), 30.00), ((3, 7), 99.00), ((3, 6), 294.50)]},
        5: {"win": (3, 47.00), "q": ((1, 3), 46.50), "qp": [((1, 3), 23.00), ((3, 6), 59.00), ((1, 6), 41.50)]},
        6: {"win": (3, 50.00), "q": ((1, 3), 88.50), "qp": [((1, 3), 38.50), ((3, 8), 108.50), ((1, 8), 77.50)]},
        7: {"win": (7, 380.00), "q": ((7, 10), 758.50), "qp": [((7, 10), 232.00), ((7, 11), 645.00), ((10, 11), 75.00)]},
        8: {"win": (9, 500.50), "q": ((4, 9), 658.00), "qp": [((4, 9), 198.00), ((9, 11), 373.00), ((4, 11), 36.50)]},
        9: {"win": (1, 81.50), "q": ((1, 2), 140.50), "qp": [((1, 2), 55.50), ((1, 11), 86.00), ((2, 11), 50.50)]},
        10: {"win": (3, 56.00), "q": ((1, 3), 352.00), "qp": [((1, 3), 113.00), ((3, 7), 43.00), ((1, 7), 113.00)]},
        11: {"win": (6, 55.50), "q": ((6, 11), 240.50), "qp": [((6, 11), 96.00), ((6, 13), 742.00), ((11, 13), 765.00)]},
    }
}


@st.cache_resource(ttl=60)
def get_db_connection():
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
  conn, db_source = get_db_connection()
  cur = conn.cursor()
  cur.execute("SELECT DISTINCT race_date FROM model_pwin_results ORDER BY race_date DESC;")
  rows = cur.fetchall()
  cur.close()
  return [str(r[0]) for r in rows], db_source


@st.cache_data(ttl=10)
def load_meeting_data(selected_date):
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


def update_single_race_odds(target_date, race_no, odds_map):
  """Saves manual odds input directly to PostgreSQL DB and syncs to Cloud."""
  update_payload = [(float(odds), target_date, race_no, int(h_no)) for h_no, odds in odds_map.items()]
  update_query = """
        UPDATE model_pwin_results
        SET live_odds = %s
        WHERE race_date = %s AND race_no = %s AND horse_no = %s;
    """
  
  # 1. Update primary connection (Cloud DB or Local NAS)
  try:
    conn, db_source = get_db_connection()
    cur = conn.cursor()
    cur.executemany(update_query, update_payload)
    conn.commit()
    cur.close()
    conn.close()
  except Exception as e:
    st.error(f"Error saving odds to primary database: {e}")

  # 2. Mirror to Neon Cloud DB if connected to local
  neon_url = st.secrets.get("NEON_DB_URL", os.environ.get("NEON_DB_URL", NEON_DB_URL_DEFAULT))
  try:
    cloud_conn = psycopg2.connect(neon_url, connect_timeout=3)
    cloud_cur = cloud_conn.cursor()
    cloud_cur.executemany(update_query, update_payload)
    cloud_conn.commit()
    cloud_cur.close()
    cloud_conn.close()
  except Exception:
    pass


# ==============================================================================
# SIDEBAR CONTROL PANEL
# ==============================================================================
st.sidebar.title("🏇 HKJC Quant Control")

available_dates, db_source_name = load_race_dates()
st.sidebar.caption(f"Connected to: **{db_source_name}**")

if not available_dates:
  st.sidebar.warning("No race date records found in database.")
  st.stop()

selected_date = st.sidebar.selectbox("📅 Select Race Meeting Date", available_dates, index=0)

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Staking Parameters")
bankroll = st.sidebar.number_input("Total Bankroll ($HKD)", min_value=1000, max_value=500000, value=5000, step=1000)
ev_threshold = st.sidebar.slider("Min WIN EV Edge Threshold", min_value=0.05, max_value=0.50, value=0.20, step=0.05)
exotic_confidence_threshold = st.sidebar.slider("Min Exotic Box 4 PWIN Sum", min_value=0.35, max_value=0.65, value=0.50, step=0.05)

if st.sidebar.button("🔄 Refresh Live Odds"):
  st.cache_data.clear()
  st.rerun()

# ==============================================================================
# MAIN PAGE NAVIGATION TABS
# ==============================================================================
st.title("🏇 HKJC Quant PWIN Super-Computer Dashboard v200.6")

main_nav1, main_nav2 = st.tabs(["⚡ Live Racecard & Bet Signals", "📜 Previous Meeting Performance Audit"])

# ------------------------------------------------------------------------------
# TAB 1: LIVE RACECARD & SIGNALS
# ------------------------------------------------------------------------------
with main_nav1:
  st.caption(f"Active Race Meeting: **{selected_date}** | Automated Fractional Kelly & Dynamic Confidence Filters")
  raw_df = load_meeting_data(selected_date)

  if raw_df.empty:
    st.warning(f"No race data available for date: {selected_date}")
  else:
    race_numbers = sorted(raw_df["race_no"].unique())

    # Pre-calculate Global Metrics
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

    mcol1, mcol2, mcol3, mcol4 = st.columns(4)
    mcol1.metric("Total Races Today", f"{len(race_numbers)} Races")
    mcol2.metric("WIN Overlay Signals", f"{total_win_bets} Bets")
    mcol3.metric("Exotics Box 4 Signals", f"{total_exotic_races} Races")
    mcol4.metric("Total Recommended Stake", f"${total_recommended_stake:,.0f} HKD")

    st.markdown("---")

    tabs = st.tabs([f"Race {r_no}" for r_no in race_numbers])

    for idx, r_no in enumerate(race_numbers):
      with tabs[idx]:
        r_df = raw_df[raw_df["race_no"] == r_no].copy()

        pwins = r_df["model_pwin"].astype(float).tolist()
        harville_sum = sum(p / (1.0 - p) for p in pwins if p < 1.0)

        r_df["p_top2"] = r_df["model_pwin"].apply(
            lambda p: round(min(p * (1.0 + (harville_sum - (p / (1.0 - p)))), 0.999), 4) if p < 1.0 else p
        )

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

        win_overlays = sorted(win_overlays, key=lambda x: x["ev_edge"], reverse=True)[:3]

        sorted_top2 = r_df.sort_values(by="p_top2", ascending=False)
        top4_pwin_sum = sorted_top2.head(4)["model_pwin"].sum()
        exotic_box = sorted_top2.head(4)["horse_no"].astype(int).tolist()

        st.subheader(f"⚡ Race {r_no} Live Recommendation Slip")
        col_win, col_exotic = st.columns(2)

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

        with col_exotic:
          st.markdown("##### 🎲 Box 4 Exotics Recommendation")
          if top4_pwin_sum >= exotic_confidence_threshold:
            st.success(f"🟢 **EXOTICS GO SIGNAL** (Top 4 Confidence: {top4_pwin_sum:.1%})")
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
        # NEW: MANUAL ODDS OVERRIDE EXPANDER
        # ----------------------------------------------------------------------
        with st.expander(f"✏️ Manual Odds Override for Race {r_no}"):
          st.caption("Input or edit live tote odds below. Click 'Save Odds' to update calculations instantly.")
          
          edit_df = r_df[["horse_no", "horse_name", "fair_odds", "live_odds"]].copy()
          edit_df.columns = ["No.", "Horse Name", "Fair Odds", "Live Odds"]
          
          edited_data = st.data_editor(
              edit_df,
              column_config={
                  "No.": st.column_config.NumberColumn(disabled=True),
                  "Horse Name": st.column_config.TextColumn(disabled=True),
                  "Fair Odds": st.column_config.NumberColumn(disabled=True, format="$%.2f"),
                  "Live Odds": st.column_config.NumberColumn("Live Odds ($)", min_value=1.0, max_value=999.0, step=0.1, format="$%.1f"),
              },
              hide_index=True,
              key=f"editor_race_{r_no}"
          )
          
          if st.button(f"💾 Save Race {r_no} Odds & Recalculate Stakes", key=f"btn_race_{r_no}"):
            odds_map = dict(zip(edited_data["No."], edited_data["Live Odds"]))
            update_single_race_odds(selected_date, r_no, odds_map)
            st.cache_data.clear()
            st.success(f"Successfully updated odds for Race {r_no}!")
            st.rerun()

        # ----------------------------------------------------------------------
        # DETAILED RACE FIELD MATRIX TABLE
        # ----------------------------------------------------------------------
        st.markdown("##### 📊 Full Field Probability & Edge Matrix")

        r_df["ev_edge"] = (r_df["model_pwin"] * r_df["live_odds"]) - 1.0
        r_df["kelly_stake"] = r_df.apply(
            lambda h: (
                round(
                    max(0.0, ((h["live_odds"] - 1.0) * h["model_pwin"] - (1.0 - h["model_pwin"])) / (h["live_odds"] - 1.0))
                    * bankroll * 0.25
                )
                if (h["model_pwin"] >= 0.08 and 1.0 < h["live_odds"] <= 35.0 and h["ev_edge"] >= ev_threshold)
                else 0
            ),
            axis=1,
        )

        display_df = pd.DataFrame({
            "No.": r_df["horse_no"].astype(int),
            "Horse Name": r_df["horse_name"],
            "Rating": r_df["rating"].astype(int),
            "Draw": r_df["draw"].astype(int),
            "Model PWIN": r_df["model_pwin"].map(lambda x: f"{x:.1%}"),
            "Harville Top-2": r_df["p_top2"].map(lambda x: f"{x:.1%}"),
            "Fair Odds": r_df["fair_odds"].map(lambda x: f"${x:.2f}"),
            "Live Odds": r_df["live_odds"].map(lambda x: f"${x:.1f}" if x > 0 else "N/A"),
            "EV Edge": r_df["ev_edge"].map(lambda x: f"+{x:.2f}" if x > 0 else f"{x:.2f}"),
            "Kelly Stake": r_df["kelly_stake"].map(lambda x: f"${x:.0f}" if x >= 50 else "-"),
        })

        st.dataframe(
            display_df.sort_values(by="Model PWIN", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

# ------------------------------------------------------------------------------
# TAB 2: PREVIOUS MEETING PERFORMANCE AUDIT
# ------------------------------------------------------------------------------
with main_nav2:
  st.subheader("📜 Historical Meeting Performance & Backtest Audit")
  st.caption("Review model predictive accuracy, hit rates, payouts, and net ROI for completed race meetings.")

  audit_date = st.selectbox("Select Past Meeting for Performance Audit", ["2026-10-01"], index=0)
  audit_df = load_meeting_data(audit_date)

  if audit_df.empty:
    st.info(f"No historical database records for meeting: {audit_date}")
  else:
    official_dict = HISTORICAL_OFFICIAL_RESULTS.get(audit_date, {})
    a_races = sorted(audit_df["race_no"].unique())

    win_outlay, win_payout = 0.0, 0.0
    exotic_outlay, exotic_payout = 0.0, 0.0

    audit_logs = []

    for r_no in a_races:
      r_df = audit_df[audit_df["race_no"] == r_no].copy()
      official = official_dict.get(r_no)

      pwins = r_df["model_pwin"].astype(float).tolist()
      harville_sum = sum(p / (1.0 - p) for p in pwins if p < 1.0)
      r_df["p_top2"] = r_df["model_pwin"].apply(
          lambda p: p * (1.0 + (harville_sum - (p / (1.0 - p)))) if p < 1.0 else p
      )

      # Evaluate WIN Bets
      w_bets = []
      for _, h in r_df.iterrows():
        pwin = float(h["model_pwin"] or 0.0)
        odds = float(h["live_odds"] or 0.0)
        if pwin >= 0.08 and 1.0 < odds <= 35.0:
          edge = (pwin * odds) - 1.0
          if 0.15 <= edge <= 2.50:
            b = odds - 1.0
            f_k = max(0.0, (b * pwin - (1.0 - pwin)) / b)
            stake = round(f_k * 5000.0 * 0.25)
            if stake >= 50:
              w_bets.append((int(h["horse_no"]), h["horse_name"], odds, edge, stake))

      w_bets = sorted(w_bets, key=lambda x: x[3], reverse=True)[:3]

      # Evaluate Exotics
      sorted_top2 = r_df.sort_values(by="p_top2", ascending=False)
      top4_pwin = sorted_top2.head(4)["model_pwin"].sum()
      box_horses = sorted_top2.head(4)["horse_no"].astype(int).tolist()

      r_win_ret = 0.0
      r_win_cost = 0.0
      win_hits_str = []

      if official and w_bets:
        for h_no, name, odds, edge, stake in w_bets:
          r_win_cost += stake
          hit = h_no == official["win"][0]
          payout = (stake / 10.0 * official["win"][1]) if hit else 0.0
          r_win_ret += payout
          if hit:
            win_hits_str.append(f"🎯 WIN HIT #{h_no} {name} (${official['win'][1]})")

      win_outlay += r_win_cost
      win_payout += r_win_ret

      r_exo_cost = 0.0
      r_exo_ret = 0.0
      exo_hit_str = "MISS"

      if official and top4_pwin >= 0.45:
        q_combos = list(itertools.combinations(sorted(box_horses), 2))
        r_exo_cost = len(q_combos) * 20
        exotic_outlay += r_exo_cost

        q_hit_div = sum(official["q"][1] for pair in q_combos if pair == official["q"][0])
        qp_hit_div = sum(qp_div for pair in q_combos for qp_target, qp_div in official["qp"] if pair == qp_target)
        r_exo_ret = q_hit_div + qp_hit_div
        exotic_payout += r_exo_ret
        if r_exo_ret > 0:
          exo_hit_str = f"🎯 EXOTIC HIT (${r_exo_ret:.2f})"

      audit_logs.append({
          "Race": f"Race {r_no}",
          "WIN Outlay": f"${r_win_cost:.0f}",
          "WIN Return": f"${r_win_ret:.2f}",
          "WIN Status": ", ".join(win_hits_str) if win_hits_str else "MISS",
          "Exotic Outlay": f"${r_exo_cost:.0f}",
          "Exotic Return": f"${r_exo_ret:.2f}",
          "Exotic Box": str(box_horses),
          "Exotic Status": exo_hit_str if top4_pwin >= 0.45 else "SKIPPED (<45%)"
      })

    # Summary Metrics
    tot_outlay = win_outlay + exotic_outlay
    tot_payout = win_payout + exotic_payout
    net_profit = tot_payout - tot_outlay
    roi = (net_profit / tot_outlay * 100.0) if tot_outlay > 0 else 0.0

    wcol1, wcol2, wcol3, wcol4 = st.columns(4)
    wcol1.metric("Total Backtest Capital Outlay", f"${tot_outlay:,.2f} HKD")
    wcol2.metric("Total Payout Return", f"${tot_payout:,.2f} HKD")
    wcol3.metric("Net Meeting Profit", f"${net_profit:+,.2f} HKD")
    wcol4.metric("Super-Computer ROI", f"{roi:+.1f}%")

    st.markdown("---")
    st.subheader(f"📊 Detailed Race-by-Race Audit Table ({audit_date})")
    st.dataframe(pd.DataFrame(audit_logs), use_container_width=True, hide_index=True)
