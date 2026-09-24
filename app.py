import psycopg2
import pandas as pd
import requests
import streamlit as st

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
                
                # Check if live odds are properly synced
                raw_live_odds = df["Live Odds"]
                is_dummy_odds = (raw_live_odds == 10.0).all() or (raw_live_odds == 0.0).all() or raw_live_odds.isna().all()

                if is_dummy_odds:
                    st.warning("⚠️ **Live Odds Not Synced!** Showing baseline matrix (10.0 default). Double-click table cells to enter manual odds, or run `HKJC_02_Cloud_Sync_Odd` in Synology.")

                df["Live Odds"] = df["Live Odds"].apply(lambda x: x if (pd.notna(x) and x > 1.0) else 10.0)

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

                # =========================================================================
                # MARKET PROBABILITY BLENDING & CALIBRATION ENGINE
                # =========================================================================
                edited_df["Raw PWIN"] = edited_df["PWIN %"] / 100.0
                edited_df["Market Implied PWIN"] = 1.0 / edited_df["Live Odds"]

                # Blend Model PWIN (50%) with Market Implied PWIN (50%) to anchor longshots
                edited_df["Calibrated PWIN"] = (0.50 * edited_df["Raw PWIN"]) + (0.50 * edited_df["Market Implied PWIN"])
                edited_df["Calibrated PWIN %"] = (edited_df["Calibrated PWIN"] * 100.0).round(2)
                edited_df["Calibrated Fair Odds"] = (1.0 / edited_df["Calibrated PWIN"]).round(2)

                # Compute Calibrated Expected Value (EV)
                edited_df["Expected Value (EV)"] = ((edited_df["Calibrated PWIN"] * edited_df["Live Odds"]) - 1.0).round(3)

                # Guardrail: Cap extreme longshots (> 35.0 odds) from generating fake positive EV
                edited_df.loc[edited_df["Live Odds"] > 35.0, "Expected Value (EV)"] = -0.999

                st.markdown("### 🎯 Value Analysis & Overlays (Calibrated)")
                def highlight_ev(val):
                    if val > 0.15: return 'background-color: #d4edda; color: #155724; font-weight: bold'
                    elif val > 0.0: return 'background-color: #e2e3e5; color: #383d41'
                    else: return 'background-color: #f8d7da; color: #721c24'

                styled_df = edited_df[["No.", "Horse Name", "Jockey", "Trainer", "Draw", "PWIN %", "Calibrated PWIN %", "Calibrated Fair Odds", "Live Odds", "Expected Value (EV)"]].style.map(highlight_ev, subset=["Expected Value (EV)"])
                st.dataframe(styled_df, use_container_width=True, hide_index=True)

                st.divider()
                st.markdown("### 🤖 Gemini Executive Strategist Synthesis")

                if st.button("🚀 Synthesize Investment Strategy (AI 策略分析)", type="primary"):
                    # =========================================================================
                    # GUARDRAIL 1: HARD STOP ON UNSYNCED / DUMMY ODDS
                    # =========================================================================
                    current_odds = edited_df["Live Odds"]
                    if (current_odds == 10.0).all() or (current_odds == 0.0).all():
                        st.error("🛑 **Strategy Engine Halted: Live Odds Not Synced**")
                        st.warning(
                            "Live tote odds have not synced from HKJC (currently showing placeholder 10.0). "
                            "Calculating Expected Value (EV) on dummy odds creates false positive signals and guarantees bad bets.\n\n"
                            "**How to fix:**\n"
                            "1. Double-click the cells in the **Live Odds (Board)** table to enter actual board odds manually, OR\n"
                            "2. Trigger `HKJC_02_Cloud_Sync_Odd` in Synology Task Scheduler when HKJC tote selling is open."
                        )
                        st.stop()  # Halt script execution immediately
                    # =========================================================================

                    if not openrouter_key:
                        st.error("Please enter your OpenRouter API Key in the sidebar.")
                    else:
                        with st.spinner("正在計算最佳投注策略 (Calculating optimal stake strategy)..."):
                            try:
                                payload = {
                                    "model": "google/gemini-2.5-flash",
                                    "max_tokens": 700,
                                    "messages": [{
                                        "role": "user",
                                        "content": f"""
You are an elite HKJC Quant Portfolio Manager operating with strict risk management discipline.
Analyze Race Matrix for Date: {selected_date}, Race: {selected_race}.
Data: {edited_df[['No.', 'Horse Name', 'Calibrated PWIN %', 'Calibrated Fair Odds', 'Live Odds', 'Expected Value (EV)']].to_json(orient='records')}

STRICT QUANT STRATEGY RULES:
1. Positive EV Filter: ONLY recommend a WIN / PLACE / PQ bet if Expected Value (EV) >= +0.10 and Calibrated PWIN % >= 8.5%.
2. PASS RACE / NO BET RULE: If NO horse has EV >= +0.10, or if all EV values are negative/weak, output "本場無值博馬匹，建議觀望 / 棄注 (NO BET / PASS)". Do NOT force bets on negative EV runners.
3. Banker (馬膽) Selection: Pick the highest EV horse with Calibrated PWIN >= 8.5% as Banker. If none meet the criteria, output "無 (None)".
4. Legs (配腳) Selection: Pick 2 to 4 horses with positive EV or top Calibrated PWIN %. Do NOT select the Banker as a Leg.
5. Avoid List (迴避馬匹): List horses with EV < 0 or severely overbet underlays (Live Odds < Calibrated Fair Odds without sufficient win probability), excluding Banker & Legs.
6. Language & Formatting: Output STRICTLY in Traditional Chinese (繁體中文) using Hong Kong racing terminology. Use DOUBLE NEWLINES between every section so Markdown renders properly.

Format strictly like this:

### 🎯 建議投資組合 (Top Investments)

| 馬號 | 馬名 | 勝率 (PWIN %) | 公平賠率 | 即時賠率 | 期望值 (EV) | 建議注項 | 注碼分配 |
|---|---|---|---|---|---|---|---|
| [馬號] | [馬名] | [Calibrated PWIN %] | [Fair Odds] | [Live Odds] | [EV] | [WIN / PLACE / PQ / 觀望] | [注碼%] |

### 🎲 連贏/位置Q 策略

• **馬膽 (Banker)**: [馬號 & 馬名 or 無]

• **配腳 (Legs)**: [馬號 & 馬名 or 無]

### ⚠️ 迴避馬匹 (Severe Underlays)

• **不值博馬匹 (EV < 0，排除已選配腳)**: [列出其餘嚴重偏低/不值博之馬號]
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
