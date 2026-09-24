import psycopg2
import pandas as pd
import requests
import streamlit as st
import json
import base64

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

def update_odds_in_neon(conn_url, r_date, r_no, odds_dict):
    """Batch updates live odds in Neon Cloud DB from a dictionary {horse_no: odds_val}"""
    try:
        cloud_conn = psycopg2.connect(conn_url)
        cloud_cur = cloud_conn.cursor()
        count = 0
        for h_no, o_val in odds_dict.items():
            cloud_cur.execute("""
                UPDATE model_pwin_results 
                SET live_odds = %s 
                WHERE race_date = %s AND race_no = %s AND horse_no = %s;
            """, (float(o_val), r_date, r_no, int(h_no)))
            count += 1
        cloud_conn.commit()
        cloud_cur.close(); cloud_conn.close()
        return count
    except Exception as e:
        st.error(f"Failed to update Neon Cloud DB: {e}")
        return 0

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

            # =========================================================================
            # FAST ODDS INGESTION PANEL (SCREENSHOT OCR + QUICK PASTE)
            # =========================================================================
            with st.expander("⚡ **Fast Live Odds Manual / Screenshot Sync (Click to Open)**", expanded=False):
                tab1, tab2 = st.tabs(["📸 Upload Odds Screenshot (AI OCR)", "✍️ Quick String Paste"])

                # --- TAB 1: SCREENSHOT OCR ---
                with tab1:
                    st.write("Upload a screenshot of the HKJC live odds board. Gemini will read the odds and sync Neon Cloud DB automatically.")
                    uploaded_img = st.file_uploader("Choose HKJC Odds Screenshot...", type=["png", "jpg", "jpeg", "webp"])
                    if uploaded_img and st.button("🔍 Parse Screenshot & Sync to Cloud DB"):
                        if not openrouter_key:
                            st.error("Please enter your OpenRouter API Key in the sidebar.")
                        else:
                            with st.spinner("Gemini is reading screenshot and extracting live odds..."):
                                try:
                                    img_bytes = uploaded_img.read()
                                    base64_img = base64.b64encode(img_bytes).decode('utf-8')
                                    
                                    vision_payload = {
                                        "model": "google/gemini-2.5-flash",
                                        "messages": [{
                                            "role": "user",
                                            "content": [
                                                {"type": "text", "text": "Extract all horse numbers and their current WIN odds from this HKJC board screenshot. Output strictly a JSON object where keys are horse numbers as strings and values are float odds. Example: {\"1\": 3.5, \"2\": 12.0, \"3\": 8.5}. Output ONLY JSON."},
                                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                                            ]
                                        }]
                                    }
                                    v_res = requests.post("https://openrouter.ai/api/v1/chat/completions", json=vision_payload, headers={"Authorization": f"Bearer {openrouter_key}", "Content-Type": "application/json"}, timeout=20)
                                    if v_res.status_code == 200:
                                        raw_json_str = v_res.json()["choices"][0]["message"]["content"].replace("```json", "").replace("```", "").strip()
                                        parsed_dict = json.loads(raw_json_str)
                                        updated_n = update_odds_in_neon(neon_url, selected_date, selected_race, parsed_dict)
                                        st.success(f"✅ Successfully read screenshot and updated {updated_n} horses in Neon Cloud DB!")
                                        st.rerun()
                                    else:
                                        st.error(f"Vision API Error: {v_res.text}")
                                except Exception as ve:
                                    st.error(f"Screenshot Processing Error: {ve}")

                # --- TAB 2: QUICK PASTE STRING ---
                with tab2:
                    st.write("Format example: `1=3.5; 2=8.2; 3=14.0; 4=5.1` or `1:3.5, 2:8.2, 3:14.0`")
                    paste_str = st.text_input("Paste Odds String:", placeholder="1=3.5; 2=8.2; 3=14.0; 4=5.1")
                    if st.button("🚀 Push Pasted Odds to Cloud DB"):
                        if paste_str:
                            pairs = re.findall(r'(\d{1,2})\s*[:=,\s-]\s*([\d\.]+)', paste_str)
                            if pairs:
                                parsed_dict = {p[0]: float(p[1]) for p in pairs}
                                updated_n = update_odds_in_neon(neon_url, selected_date, selected_race, parsed_dict)
                                st.success(f"✅ Successfully updated {updated_n} horses in Neon Cloud DB!")
                                st.rerun()
                            else:
                                st.error("Could not parse numbers from string. Use format like: `1=3.5; 2=8.2; 3=14.0`")

            # =========================================================================
            # MAIN RACECARD QUERY
            # =========================================================================
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
                
                raw_live_odds = df["Live Odds"]
                is_dummy_odds = (raw_live_odds == 10.0).all() or (raw_live_odds == 0.0).all() or raw_live_odds.isna().all()

                if is_dummy_odds:
                    st.warning("⚠️ **Live Odds Not Synced!** Upload a screenshot above or enter manual odds below.")

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

                # --- MARKET PROBABILITY BLENDING & CALIBRATION ENGINE ---
                edited_df["Raw PWIN"] = edited_df["PWIN %"] / 100.0
                edited_df["Market Implied PWIN"] = 1.0 / edited_df["Live Odds"]

                # Blend Model PWIN (50%) with Market Implied PWIN (50%)
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
                    current_odds = edited_df["Live Odds"]
                    if (current_odds == 10.0).all() or (current_odds == 0.0).all():
                        st.error("🛑 **Strategy Engine Halted: Live Odds Not Synced**")
                        st.warning("Live odds are showing baseline defaults. Upload a screenshot above or enter manual odds first.")
                        st.stop()

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
