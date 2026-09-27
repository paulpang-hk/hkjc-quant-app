# -*- coding: utf-8 -*-
import base64
import json
import time
import pandas as pd
import psycopg2
import requests
import streamlit as st

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="HKJC Quant Strategy Engine",
    page_icon="🏇",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# SECRETS & DATABASE CONNECTIONS
# ==========================================
NEON_DB_URL = st.secrets.get(
    "NEON_DB_URL",
    "postgresql://neondb_owner:npg_D2YzinaM8grT@ep-snowy-fire-b59poqzm-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require",
)
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", "")


def get_db_connection():
  return psycopg2.connect(NEON_DB_URL)


# ==========================================
# HEADER & TITLE
# ==========================================
st.title("🏇 HKJC Quant Strategy Engine (Cloud Edition)")

# ==========================================
# FETCH DATES & RACES
# ==========================================
try:
  conn = get_db_connection()
  cur = conn.cursor()

  cur.execute(
      "SELECT DISTINCT race_date FROM model_pwin_results ORDER BY race_date"
      " DESC;"
  )
  available_dates = [str(r[0]) for r in cur.fetchall()]

  if not available_dates:
    st.error("❌ No racecard data found in Neon Cloud DB.")
    st.stop()

  col_date, col_race = st.columns(2)

  with col_date:
    selected_date = st.selectbox(
        "📅 Select Race Meeting Date", available_dates, index=0
    )

  cur.execute(
      "SELECT DISTINCT race_no FROM model_pwin_results WHERE race_date = %s"
      " ORDER BY race_no ASC;",
      (selected_date,),
  )
  available_races = [r[0] for r in cur.fetchall()]

  with col_race:
    selected_race = st.selectbox(
        "🏁 Select Race Number",
        available_races,
        index=0 if available_races else 0,
    )

  cur.close()
  conn.close()

except Exception as e:
  st.error(f"❌ Failed to connect to Neon Cloud DB: {e}")
  st.stop()

# ==========================================
# EXPANDER: BATCH OCR & MANUAL ODDS SYNC
# ==========================================
with st.expander(
    "⚡ Fast Live Odds Manual / Screenshot Sync (Click to Open)", expanded=False
):
  tab_ocr, tab_manual = st.tabs(
      ["📸 Upload Odds Screenshots (AI OCR)", "✏️ Quick String Paste"]
  )

  # --- TAB 1: BATCH MULTI-FILE SCREENSHOT OCR (PACED WITH AUTO-RETRY) ---
  with tab_ocr:
    st.markdown(
        "Upload one or multiple screenshots of the HKJC or on.cc odds board."
        " AI Vision will automatically extract the race number and live odds."
    )

    uploaded_files = st.file_uploader(
        "Choose HKJC / on.cc Odds Screenshots (Select up to 11 files)...",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="batch_ocr_uploader",
    )

    if uploaded_files and st.button(
        "🚀 Process All Uploaded Screenshots (OCR)"
    ):
      if not OPENROUTER_API_KEY:
        st.error(
            "❌ OPENROUTER_API_KEY is missing in Streamlit Secrets! Please add"
            " it under App Settings."
        )
      else:
        progress_bar = st.progress(0)
        status_text = st.empty()
        total_files = len(uploaded_files)
        success_count = 0

        for idx, uploaded_file in enumerate(uploaded_files):
          status_text.text(
              f"Processing screenshot {idx+1}/{total_files}:"
              f" {uploaded_file.name}..."
          )

          try:
            image_bytes = uploaded_file.getvalue()
            base64_image = base64.b64encode(image_bytes).decode("utf-8")

            prompt_text = """
                        Analyze this HKJC / on.cc horse racing odds screenshot.
                        Extract the Race Number and WIN odds for each horse number.
                        Return ONLY a valid raw JSON object in this exact format without markdown formatting:
                        {
                          "race_no": 1,
                          "odds": {
                            "1": 3.5,
                            "2": 12.0,
                            "3": 5.2
                          }
                        }
                        """

            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY.strip()}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "openai/gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{uploaded_file.type};base64,{base64_image}"
                                )
                            },
                        },
                    ],
                }],
            }

            # Attempt request with automatic retry if rate-limited (402/429)
            res = None
            for attempt in range(3):
              res = requests.post(
                  url, headers=headers, json=payload, timeout=30
              )
              if res.status_code in [402, 429]:
                status_text.text(
                    f"⚠️ Rate limit hit. Pausing 5 seconds before retrying"
                    f" {uploaded_file.name} (Attempt {attempt+1}/3)..."
                )
                time.sleep(5)
              else:
                break

            if res and res.status_code == 200:
              resp_json = res.json()
              raw_text = (
                  resp_json.get("choices", [{}])[0]
                  .get("message", {})
                  .get("content", "")
              )

              cleaned_json_str = (
                  raw_text.replace("```json", "").replace("```", "").strip()
              )
              parsed_data = json.loads(cleaned_json_str)

              parsed_race_no = parsed_data.get("race_no")
              odds_map = parsed_data.get("odds", {})

              if parsed_race_no and odds_map:
                db_conn = get_db_connection()
                db_cur = db_conn.cursor()

                updated_runners = 0
                for horse_str, odds_val in odds_map.items():
                  if odds_val is not None:
                    try:
                      clean_odds = float(odds_val)
                      db_cur.execute(
                          """
                                                UPDATE model_pwin_results 
                                                SET live_odds = %s 
                                                WHERE race_date = %s AND race_no = %s AND horse_no = %s;
                                            """,
                          (
                              clean_odds,
                              selected_date,
                              int(parsed_race_no),
                              int(horse_str),
                          ),
                      )
                      updated_runners += 1
                    except (ValueError, TypeError):
                      continue

                db_conn.commit()
                db_cur.close()
                db_conn.close()

                success_count += 1
                st.success(
                    f"✅ Race {parsed_race_no}: Parsed {updated_runners}"
                    f" runners from {uploaded_file.name}"
                )
              else:
                st.warning(
                    f"⚠️ Could not extract valid odds JSON from"
                    f" {uploaded_file.name}"
                )
            else:
              st.error(
                  f"❌ OpenRouter API error ({res.status_code if res else 'No'}"
                  f" Response}): {res.text if res else ''}"
              )

          except Exception as ex:
            st.error(f"❌ Error parsing {uploaded_file.name}: {ex}")

          # Add 2-second delay between files to respect OpenRouter rate limits
          time.sleep(2)
          progress_bar.progress((idx + 1) / total_files)

        status_text.text(
            f"🎉 Batch processing complete! Successfully updated"
            f" {success_count}/{total_files} race screenshots."
        )
        time.sleep(1)
        st.rerun()

  # --- TAB 2: MANUAL STRING PASTE ---
  with tab_manual:
    st.markdown("Paste odds string in format: `1=3.5 2=12.0 3=5.2` or line-by-line")
    manual_input = st.text_area("Live Odds String", height=100)
    if st.button("💾 Apply Manual Odds to Current Race"):
      if manual_input.strip():
        import re

        pairs = re.findall(r"(\d+)=([\d\.]+)", manual_input)
        if pairs:
          db_conn = get_db_connection()
          db_cur = db_conn.cursor()
          for h_no, o_val in pairs:
            db_cur.execute(
                """
                            UPDATE model_pwin_results 
                            SET live_odds = %s 
                            WHERE race_date = %s AND race_no = %s AND horse_no = %s;
                        """,
                (float(o_val), selected_date, selected_race, int(h_no)),
            )
          db_conn.commit()
          db_cur.close()
          db_conn.close()
          st.success(f"✅ Updated {len(pairs)} runners for Race {selected_race}!")
          st.rerun()

# ==========================================
# FETCH DATA FOR SELECTED RACE
# ==========================================
conn = get_db_connection()
query = """
    SELECT horse_no, horse_name, brand_code, jockey, trainer, draw, carried_weight, 
           rating, model_pwin, fair_odds, live_odds
    FROM model_pwin_results
    WHERE race_date = %s AND race_no = %s
    ORDER BY horse_no ASC;
"""
df = pd.read_sql(query, conn, params=(selected_date, selected_race))
conn.close()

if df.empty:
  st.warning("⚠️ No runners found for the selected race.")
  st.stop()

# ==========================================
# STATUS BANNER
# ==========================================
has_live_odds = (
    df["live_odds"].notnull().any()
    and (df["live_odds"] > 0).any()
    and not (df["live_odds"] == 10.0).all()
)

if not has_live_odds:
  st.warning(
      "⚠️ Live Odds Not Synced! Displaying default baseline odds (10.0). Upload"
      " screenshots above or run local poll_and_sync poller."
  )
else:
  st.success("🟢 Live Odds Synced & Active!")

# ==========================================
# TABLE 1: RACECARD MATRIX
# ==========================================
st.subheader(f"📊 Racecard Matrix: {selected_date} | Race {selected_race}")

df_display = df.copy()
df_display.rename(
    columns={
        "horse_no": "No.",
        "horse_name": "Horse Name",
        "brand_code": "Brand",
        "jockey": "Jockey",
        "trainer": "Trainer",
        "draw": "Draw",
        "carried_weight": "Wt",
        "rating": "Rtg",
        "model_pwin": "Model PWIN %",
        "fair_odds": "Fair Odds",
        "live_odds": "Live Odds (Board)",
    },
    inplace=True,
)

df_display["Model PWIN %"] = df_display["Model PWIN %"].apply(
    lambda x: f"{float(x)*100:.2f}%" if pd.notnull(x) else "None"
)
df_display["Fair Odds"] = df_display["Fair Odds"].apply(
    lambda x: f"{float(x):.2f}" if pd.notnull(x) else "None"
)
df_display["Live Odds (Board)"] = df_display["Live Odds (Board)"].apply(
    lambda x: f"{float(x):.1f}" if pd.notnull(x) else "10.0"
)

st.dataframe(df_display, use_container_width=True, hide_index=True)

# ==========================================
# TABLE 2: VALUE ANALYSIS & OVERLAYS
# ==========================================
st.subheader("🎯 Value Analysis & Overlays (Calibrated)")

df_val = df.copy()
df_val["pwin_val"] = df_val["model_pwin"].fillna(0).astype(float)
df_val["live_val"] = df_val["live_odds"].fillna(10.0).astype(float)

df_val["EV"] = (df_val["pwin_val"] * df_val["live_val"]) - 1.0
df_val["Overlay Flag"] = df_val["EV"].apply(
    lambda x: "🔥 VALUE OVERLAY"
    if x > 0.15
    else ("✅ MILD VALUE" if x > 0.0 else "❌ UNDERLAY")
)

df_val_display = pd.DataFrame({
    "No.": df_val["horse_no"],
    "Horse Name": df_val["horse_name"],
    "Jockey": df_val["jockey"],
    "Trainer": df_val["trainer"],
    "PWIN %": df_val["pwin_val"].apply(lambda x: f"{x*100:.2f}%"),
    "Fair Odds": df_val["fair_odds"].apply(
        lambda x: f"{float(x):.2f}" if pd.notnull(x) else "None"
    ),
    "Live Odds": df_val["live_val"].apply(lambda x: f"{x:.1f}"),
    "Expected Value (EV)": df_val["EV"].apply(lambda x: f"{x:+.2f}"),
    "Value Status": df_val["Overlay Flag"],
})

df_val_display.sort_values(
    by="Expected Value (EV)", ascending=False, inplace=True
)

st.dataframe(df_val_display, use_container_width=True, hide_index=True)
