import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch

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

def update_manual_odds(updated_data, race_date, race_no):
    """Pushes manually edited odds back to Neon DB and recalculates P_win/Kelly"""
    updates = []
    for _, row in updated_data.iterrows():
        odds_val = float(row['live_odds']) if pd.notna(row['live_odds']) and row['live_odds'] > 0 else None
        updates.append((odds_val, race_date, race_no, row['horse_no']))
        
    sql = """
        UPDATE model_pwin_results 
        SET live_odds = %s, updated_at = CURRENT_TIMESTAMP
        WHERE race_date = %s AND race_no = %s AND horse_no = %s;
    """
    with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
        execute_batch(cur, sql, updates)

try:
    df = load_data()
    
    if df.empty:
        st.warning("⚠️ Database is currently empty. Run the daily ingest script first.")
    else:
        target_date = df['race_date'].iloc[0]
        venue = df['venue'].iloc[0]
        
        # --- SIDEBAR: Bankroll & Global Controls ---
        st.sidebar.header("⚙️ Betting Controls")
        bankroll = st.sidebar.number_input("Total Bankroll (HKD)", min_value=1000, value=50000, step=1000)
        kelly_fraction = st.sidebar.slider("Kelly Fraction", min_value=0.10, max_value=0.50, value=0.25, step=0.05)
        min_ev_threshold = st.sidebar.slider("Min EV Threshold (+EV)", min_value=0.0, max_value=0.5, value=0.05, step=0.01)
        
        # --- HEADER ---
        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f"📅 Race Date: {target_date}")
        with col2:
            st.subheader(f"🏟️ Venue: {venue}")
            
        st.divider()
        
        # --- RACE SELECTOR ---
        races = sorted(df['race_no'].unique())
        selected_race = st.selectbox("📌 Select Race Number:", races)
        
        race_df = df[df['race_no'] == selected_race].copy()
        
        # --- CALCULATIONS ---
        def compute_metrics(row):
            p_win = row['model_pwin'] if pd.notna(row['model_pwin']) else 0.0
            odds = row['live_odds']
            
            if pd.notna(odds) and odds > 1.0 and p_win > 0:
                ev = (p_win * odds) - 1.0
                if ev > 0:
                    k_pct = (ev / (odds - 1.0)) * kelly_fraction
                    stake = bankroll * k_pct
                    return pd.Series([ev, k_pct, stake])
            return pd.Series([None, 0.0, 0.0])

        race_df[['EV', 'Kelly_Pct', 'Recommended_Stake']] = race_df.apply(compute_metrics, axis=1)
        
        # --- TOP VALUE BETS (ACTION CARDS) ---
        value_bets = race_df[race_df['EV'] >= min_ev_threshold].sort_values(by='EV', ascending=False)
        
        st.markdown("### ⭐ Actionable Opportunities")
        if not value_bets.empty:
            cols = st.columns(min(len(value_bets), 4))
            for idx, (_, bet) in enumerate(value_bets.iterrows()):
                if idx < 4:
                    with cols[idx]:
                        st.success(
                            f"**Race {selected_race} - No. {bet['horse_no']} {bet['horse_name']}**\n\n"
                            f"• Live Odds: **{bet['live_odds']:.1f}** (Fair: {bet['fair_odds']:.1f})\n\n"
                            f"• Expected Value: **{bet['EV']:+.2f}**\n\n"
                            f"• Bet Stake: **HKD ${bet['Recommended_Stake']:,.0f}** ({bet['Kelly_Pct']*100:.1f}%)"
                        )
        else:
            st.info("No value bets identified for this race based on current odds.")

        st.divider()

        # --- INTERACTIVE TABLE & OVERRIDE ---
        st.markdown("### 📊 Interactive Racecard & Odds Override")
        st.caption("💡 You can double-click any cell in the **Live Odds** column below to manually enter/edit odds, then click 'Save Manual Odds'.")
        
        # Format columns for editing display
        edit_df = race_df[['horse_no', 'horse_name', 'draw', 'rating', 'model_pwin', 'fair_odds', 'live_odds', 'EV', 'Recommended_Stake']].copy()
        
        edited_df = st.data_editor(
            edit_df,
            column_config={
                "horse_no": st.column_config.NumberColumn("No.", disabled=True),
                "horse_name": st.column_config.TextColumn("Horse Name", disabled=True),
                "draw": st.column_config.NumberColumn("Draw", disabled=True),
                "rating": st.column_config.NumberColumn("Rtg", disabled=True),
                "model_pwin": st.column_config.NumberColumn("P(win)", format="%.3f", disabled=True),
                "fair_odds": st.column_config.NumberColumn("Fair Odds", format="%.1f", disabled=True),
                "live_odds": st.column_config.NumberColumn("Live Odds (Editable)", format="%.1f", min_value=1.0, max_value=999.0),
                "EV": st.column_config.NumberColumn("EV", format="%+.2f", disabled=True),
                "Recommended_Stake": st.column_config.NumberColumn("Stake (HKD)", format="$%d", disabled=True),
            },
            hide_index=True,
            use_container_width=True,
            key=f"editor_race_{selected_race}"
        )
        
        # Save Button for Manual Overrides
        if st.button("💾 Save Manual Odds & Update DB"):
            try:
                update_manual_odds(edited_df, target_date, selected_race)
                st.success("✅ Manual odds saved! Refreshing database...")
                st.cache_data.clear()
                st.rerun()
            except Exception as ex:
                st.error(f"Failed to update odds: {ex}")

except Exception as e:
    st.error(f"Error loading dashboard: {e}")
