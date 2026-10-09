import streamlit as st
import pandas as pd
import psycopg2

DB_URL = "postgresql://neondb_owner:npg_D2YzinaM8grT@ep-snowy-fire-b59poqzm-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require"

st.set_page_config(page_title="HKJC 量化交易終端", layout="wide")
st.title("🏇 HKJC 量化戰情室 (Neon Cloud 版)")

@st.cache_data(ttl=30)
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

try:
    df = load_data()
    
    if df.empty:
        st.warning("⚠️ 資料庫目前為空，請先執行爬蟲腳本。")
    else:
        target_date = df['race_date'].iloc[0]
        venue = df['venue'].iloc[0]
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f"📅 賽事日期: {target_date}")
        with col2:
            st.subheader(f"🏟️ 場地: {venue}")
            
        st.divider()
        
        races = sorted(df['race_no'].unique())
        selected_race = st.selectbox("📌 選擇場次 (Race No):", races)
        
        race_df = df[df['race_no'] == selected_race].copy()
        
        def calc_ev(row):
            if pd.notna(row['live_odds']) and row['live_odds'] > 0:
                return (row['model_pwin'] * row['live_odds']) - 1.0
            return None
            
        race_df['EV'] = race_df.apply(calc_ev, axis=1)
        
        race_df['model_pwin'] = (race_df['model_pwin'] * 100).apply(lambda x: f"{x:.1f}%")
        race_df['fair_odds'] = race_df['fair_odds'].apply(lambda x: f"{x:.1f}")
        race_df['EV_display'] = race_df['EV'].apply(lambda x: f"{x:+.2f}" if pd.notna(x) else "-")
        
        display_df = race_df[['horse_no', 'horse_name', 'draw', 'rating', 'model_pwin', 'fair_odds', 'live_odds', 'EV_display']]
        display_df.columns = ['馬號', '馬名', '檔位', '基礎評分', '模型勝率', '公平賠率', '即時賠率', '期望值 (EV)']
        
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        
except Exception as e:
    st.error(f"資料庫連線或讀取錯誤: {e}")
