import streamlit as st
import psycopg2
import pandas as pd
import requests

st.set_page_config(page_title="HKJC Quant Cloud Engine", page_icon="🏇", layout="wide")
st.title("🏇 HKJC Quant Strategy Engine (Cloud Edition)")

default_neon = "postgresql://neondb_owner:npg_D2YzinaM8grT@ep-snowy-fire-b59poqzm-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require"

# Safe retrieval of OpenRouter secret
default_or_key = ""
try:
    if "OPENROUTER_API_KEY" in st.secrets:
        default_or_key = st.secrets["OPENROUTER_API_KEY"]
except Exception:
    pass

st.sidebar.header("⚙️ Cloud Credentials")
neon_url = st.sidebar.text_input("Neon Cloud DB Connection String", value=default_neon, type="password")
openrouter_key = st.sidebar.text_input("OpenRouter API Key", value=default_or_key, type="password")

if neon_url:
    try:
        conn = psycopg2.connect(neon_url)
        dates_df = pd.read_sql("SELECT DISTINCT race_date FROM model_pwin_results ORDER BY race_date DESC;", conn)
        available_dates = dates_df['race_date'].astype(str).tolist()

        if not available_dates:
            st.warning("⚠️ Connected to Neon Cloud DB, but no race records were found.")
        else:
            c1, c2 = st.columns([2, 1])
            with c1:
                selected_date = st.selectbox("📅 Select Race Meeting Date", available_dates)
            with c2:
                selected_race = st.selectbox("🏁 Select Race Number", list(range(1, 12)), index=0)

            query = f"""
                SELECT 
                    horse_no AS "No.", horse_name AS "Horse Name", brand_code AS "Brand",
                    jockey AS "Jockey", trainer AS "Trainer", draw AS "Draw",
                    carried_weight AS "Wt", rating AS "Rtg", model_pwin AS "PWIN", 
                    fair_odds AS "Fair Odds", live_odds AS "Live Odds"
                FROM model_pwin_results
                WHERE race_date = '{selected_date}' AND race_no = {selected_race}
                ORDER BY model_pwin DESC;
            """
            df = pd.read_sql(query, conn)

            if not df.empty:
                df["PWIN %"] = (df["PWIN"] * 100).round(2)
                # Fallback display odds if live odds are 0.0
                df["Live Odds"] = df["Live Odds"].apply(lambda x: x if x > 1.0 else 10.0)

                st.markdown(f"### 📊 Racecard Matrix: {selected_date} | Race {selected_race}")

                edited_df = st.data_editor(
                    df[["No.", "Horse Name", "Brand", "Jockey", "Trainer", "Draw", "Wt", "Rtg", "PWIN %", "Fair Odds", "Live Odds"]],
                    column_config={
                        "Live Odds": st.column_config.NumberColumn("Live Odds (Board)", min_value=1.1, max_value=200.0, step=0.1, format="%.1f"),
                        "PWIN %": st.column_config.NumberColumn("Model PWIN %", format="%.2f%%")
                    },
                    disabled=["No.", "Horse Name", "Brand", "Jockey", "Trainer", "Draw", "Wt", "Rtg", "PWIN %", "Fair Odds"],
                    hide_index=True, use_container_width=True
                )

                # Compute Expected Value (EV)
                edited_df["PWIN"] = edited_df["PWIN %"] / 100.0
                edited_df["Expected Value (EV)"] = ((edited_df["PWIN"] * edited_df["Live Odds"]) - 1.0).round(3)

                st.markdown("### 🎯 Value Analysis & Overlays")
                def highlight_ev(val):
                    if val > 0.15: return 'background-color: #d4edda; color: #155724; font-weight: bold'
                    elif val > 0.0: return 'background-color: #e2e3e5; color: #383d41'
                    else: return 'background-color: #f8d7da; color: #721c24'

                styled_df = edited_df[["No.", "Horse Name", "Jockey", "Trainer", "Draw", "PWIN %", "Fair Odds", "Live Odds", "Expected Value (EV)"]].style.map(highlight_ev, subset=["Expected Value (EV)"])
                st.dataframe(styled_df, use_container_width=True, hide_index=True)

                st.divider()
                st.markdown("### 🤖 Gemini Executive Strategist Synthesis")

                if st.button("🚀 Synthesize Investment Strategy (AI 策略分析)", type="primary"):
                    if not openrouter_key:
                        st.error("Please enter your OpenRouter API Key in the sidebar.")
                    else:
                        with st.spinner("正在計算最佳投注策略 (Calculating optimal stake strategy)..."):
                            try:
                                payload = {
                                    "model": "google/gemini-2.5-flash",
                                    "max_tokens": 600,
                                    "messages": [{
                                        "role": "user",
                                        "content": f"""
                                        You are an elite HKJC Quant Portfolio Manager.
                                        Analyze Race Matrix for Date: {selected_date}, Race: {selected_race}.
                                        Data: {edited_df[['No.', 'Horse Name', 'PWIN %', 'Fair Odds', 'Live Odds', 'Expected Value (EV)']].to_json(orient='records')}

                                        Please output EXACTLY in Traditional Chinese (繁體中文) using Hong Kong racing terminology.
                                        CRITICAL: Use DOUBLE NEWLINES between every section so Markdown renders each section on a new line!

                                        Format strictly like this:

                                        ### 🎯 建議投資組合 (Top Investments)

                                        | 馬號 | 馬名 | 勝率 (PWIN %) | 公平賠率 | 即時賠率 | 期望值 (EV) | 建議注項 | 注碼分配 |
                                        |---|---|---|---|---|---|---|---|
                                        | [馬號] | [馬名] | [PWIN %] | [Fair Odds] | [Live Odds] | [EV] | [WIN / PLACE / PQ] | [注碼%] |

                                        ### 🎲 連贏/位置Q 策略

                                        • **馬膽 (Banker)**: [馬號 & 馬名]

                                        • **配腳 (Legs)**: [馬號 & 馬名]

                                        ### ⚠️ 迴避馬匹 (Underlays)

                                        • **不值博馬匹 (EV < 0)**: [列出所有迴避馬號]
                                        """
                                    }]
                                }
                                headers = {
                                    "Authorization": f"Bearer {openrouter_key}",
                                    "Content-Type": "application/json"
                                }
                                res = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=15)
                                if res.status_code == 200:
                                    analysis = res.json()["choices"][0]["message"]["content"]
                                    st.success("分析完成 (Analysis Complete)!")
                                    st.markdown(analysis)
                                else:
                                    st.error(f"OpenRouter Error: {res.text}")
                            except Exception as e:
                                st.error(f"API Execution Error: {e}")
            else:
                st.warning(f"No runners found for Race {selected_race} on {selected_date}.")

        conn.close()

    except Exception as e:
        st.error(f"Application Error: {e}")
