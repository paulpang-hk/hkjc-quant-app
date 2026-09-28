# -*- coding: utf-8 -*-
import base64
import io
import json
import re
import time
import pandas as pd
from PIL import Image
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


# Compress uploaded image in memory to reduce network payload
def compress_image_bytes(image_bytes, max_dim=1000, quality=70):
  try:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
      img = img.convert("RGB")

    width, height = img.size
    if max(width, height) > max_dim:
      if width > height:
        new_w = max_dim
        new_h = int(height * (max_dim / width))
      else:
        new_h = max_dim
        new_w = int(width * (max_dim / height))
      img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=quality)
    return output.getvalue(), "image/jpeg"
  except Exception:
    return image_bytes, "image/jpeg"


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

  # --- TAB 1: BATCH OCR ---
  with tab_ocr:
    st.markdown(
        "Upload one or multiple screenshots of the HKJC or on.cc odds board."
        " AI Vision will automatically extract the race number and live odds."
    )

    uploaded_files = st.file_uploader(
        "Choose HKJC / on.cc Odds Screenshots...",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="batch_ocr_uploader",
    )

    if uploaded_files and st.button(
        "🚀 Process All Uploaded Screenshots (OCR)"
    ):
      clean_api_key = OPENROUTER_API_KEY.strip()
      if not clean_api_key:
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
              f"Processing image {idx+1}/{total_files}:"
              f" {uploaded_file.name}..."
          )

          try:
            raw_bytes = uploaded_file.getvalue()
            compressed_bytes, mime_type = compress_image_bytes(raw_bytes)
            base64_image = base64.b64encode(compressed_bytes).decode("utf-8")

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
                "Authorization": f"Bearer {clean_api_key}",
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
                                "url": f"data:{mime_type};base64,{base64_image}"
                            },
                        },
                    ],
                }],
            }

            res = None
            for attempt in range(3):
              try:
                res = requests.post(
                    url, headers=headers, json=payload, timeout=45
                )
                if res.status_code in [402, 429]:
                  status_text.text(
                      f"⏳ Rate limit hit. Pausing 8s before retrying"
                      f" {uploaded_file.name} (Attempt {attempt+1}/3)..."
                  )
                  time.sleep(8)
                else:
                  break
              except requests.exceptions.RequestException:
                status_text.text(
                    f"⏳ Timeout. Retrying {uploaded_file.name} (Attempt"
                    f" {attempt+1}/3)..."
                )
                time.sleep(4)

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
                    f"⚠️ Could not extract odds JSON from {uploaded_file.name}"
                )
            else:
              err_code = str(res.status_code) if res else "Timeout"
              st.warning(
                  f"⚠️ Skipped {uploaded_file.name} due to API response"
                  f" ({err_code}). Continuing batch..."
              )

          except Exception as ex:
            st.warning(
                f"⚠️ Error processing {uploaded_file.name}: {ex}. Skipping to"
                " next file."
            )

          time.sleep(5)
          progress_bar.progress((idx + 1) / total_files)

        status_text.text(
            f"🎉 Batch processing complete! Updated {success_count}/{total_files} race screenshots."
        )
        time.sleep(1)
        st.rerun()

  # --- TAB 2: MANUAL STRING PASTE (FORM WRAPPED) ---
  with tab_manual:
    st.markdown("Paste odds string in format: `1=3.5 2=12.0 3=5.2` or line-by-line")

    with st.form("manual_odds_form"):
      manual_input = st.text_area("Live Odds String", height=100)
      submit_manual = st.form_submit_button(
          "💾 Apply Manual Odds to Current Race"
      )

      if submit_manual:
        if manual_input.strip():
          pairs = re.findall(r"(\d+)\s*=\s*([0-9\.]+)", manual_input)
          if pairs:
            try:
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

              st.success(
                  f"✅ Successfully updated {len(pairs)} runners for Race"
                  f" {selected_race}!"
              )
              time.sleep(1)
              st.rerun()

            except Exception as e:
              st.error(f"❌ Database error: {e}")
          else:
            st.error("⚠️ No valid odds found! Please use format: `1=5.8`")
        else:
          st.warning("⚠️ Text box is empty. Please paste odds first.")

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
# STATUS BANNER & DATA PREPARATION
# ==========================================
has_live_odds = (
    df["live_odds"].notnull().any()
    and (df["live_odds"] > 0).any()
    and not (df["live_odds"] == 10.0).all()
)

if not has_live_odds:
  st.warning(
      "⚠️ Live Odds Not Synced! Displaying baseline odds (10.0). Sync screenshots"
      " above or paste manually."
  )
else:
  st.success("🟢 Live Odds Synced & Active!")

# Calculate Quant Analytics Columns
df["pwin_val"] = df["model_pwin"].fillna(0).astype(float)
df["live_val"] = df["live_odds"].fillna(10.0).astype(float)

# Market Implied Probability (1 / Live Odds)
df["market_prob"] = df["live_val"].apply(
    lambda x: (1.0 / x) if x > 0 else 0.0
)

# Expected Value (EV) = (pwin * live_odds) - 1.0
df["EV"] = (df["pwin_val"] * df["live_val"]) - 1.0

# Edge / Probability Spread (Model PWIN % - Market Implied %)
df["prob_edge"] = df["pwin_val"] - df["market_prob"]

df["Overlay Flag"] = df["EV"].apply(
    lambda x: "🔥 VALUE OVERLAY"
    if x > 0.15
    else ("✅ MILD VALUE" if x > 0.0 else "❌ UNDERLAY")
)

# ==========================================
# SECTION 1: EXECUTIVE QUANT METRICS
# ==========================================
st.markdown("---")
st.subheader("⚡ Quant Intelligence Overview")

# 1. Top Model Pick
top_pwin_row = df.loc[df["pwin_val"].idxmax()]
# 2. Top Value Overlay
top_ev_row = df.loc[df["EV"].idxmax()]
# 3. Market Overround (Sum of Implied Probabilities)
total_overround = df["market_prob"].sum() * 100
# 4. Value Opportunities Count
value_count = len(df[df["EV"] > 0.0])

m_col1, m_col2, m_col3, m_col4 = st.columns(4)

with m_col1:
  st.metric(
      label="🥇 Top Model Pick",
      value=f"#{top_pwin_row['horse_no']} {top_pwin_row['horse_name']}",
      delta=f"{top_pwin_row['pwin_val']*100:.1f}% PWIN",
  )

with m_col2:
  ev_delta_str = f"EV {top_ev_row['EV']:+.2f}"
  st.metric(
      label="🔥 Top Value Overlay",
      value=f"#{top_ev_row['horse_no']} {top_ev_row['horse_name']}",
      delta=ev_delta_str,
      delta_color="normal" if top_ev_row["EV"] > 0 else "inverse",
  )

with m_col3:
  st.metric(
      label="📈 Market Overround Margin",
      value=f"{total_overround:.1f}%",
      delta=f"{total_overround - 100:+.1f}% House Take",
      delta_color="inverse",
  )

with m_col4:
  st.metric(
      label="🎯 Positive EV Overlays",
      value=f"{value_count} Runners",
      delta=f"Out of {len(df)} Field Size",
  )

# ==========================================
# SECTION 2: INTERACTIVE VISUAL ANALYTICS
# ==========================================
st.markdown("---")
st.subheader("📊 Model vs. Market Mispricing Analysis")

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
  st.markdown("**Probability Comparison (Model PWIN % vs. Market Implied %)**")

  # Prepare bar chart dataset
  chart_df = pd.DataFrame({
      "Horse": df.apply(
          lambda r: f"#{r['horse_no']} {r['horse_name']}", axis=1
      ),
      "Model PWIN %": df["pwin_val"] * 100,
      "Market Implied %": df["market_prob"] * 100,
  }).set_index("Horse")

  st.bar_chart(chart_df, height=320)

with chart_col2:
  st.markdown("**Expected Value (EV) Profile by Runner**")

  ev_chart_df = pd.DataFrame({
      "Horse": df.apply(
          lambda r: f"#{r['horse_no']} {r['horse_name']}", axis=1
      ),
      "Expected Value (EV)": df["EV"],
  }).set_index("Horse")

  st.bar_chart(ev_chart_df, height=320)

# ==========================================
# SECTION 3: KELLY STAKING & BET SIZING
# ==========================================
st.markdown("---")
st.subheader("💰 Recommended Kelly Bet Sizing")

k_col1, k_col2 = st.columns([1, 2])

with k_col1:
  bankroll = st.number_input(
      "Session Bankroll ($ HKD)",
      min_value=100,
      value=10000,
      step=500,
      key="kelly_bankroll",
  )
  kelly_fraction = st.slider(
      "Kelly Fraction (Risk Model)",
      min_value=0.10,
      max_value=1.00,
      value=0.25,
      step=0.05,
      help="0.25 = Quarter Kelly (Recommended for sports betting variance)",
  )

with k_col2:
  # Full Kelly formula: f* = (p * b - q) / b where b = odds - 1
  def calc_kelly_stake(row):
    p = row["pwin_val"]
    b = row["live_val"] - 1.0
    if b <= 0 or p <= 0:
      return 0.0
    q = 1.0 - p
    f_star = (p * b - q) / b
    if f_star <= 0:
      return 0.0
    adjusted_stake = f_star * kelly_fraction * bankroll
    return round(adjusted_stake, 0)

  df["kelly_stake"] = df.apply(calc_kelly_stake, axis=1)

  overlay_df = df[df["EV"] > 0.0].copy()

  if overlay_df.empty:
    st.info(
        "ℹ️ No positive EV overlays detected for this race. Kelly Model"
        " recommends NO BET."
    )
  else:
    stake_summary = pd.DataFrame({
        "No.": overlay_df["horse_no"],
        "Horse Name": overlay_df["horse_name"],
        "Model PWIN %": overlay_df["pwin_val"].apply(lambda x: f"{x*100:.2f}%"),
        "Live Odds": overlay_df["live_val"].apply(lambda x: f"{x:.1f}"),
        "Expected Value": overlay_df["EV"].apply(lambda x: f"{x:+.2f}"),
        "Recommended Stake ($ HKD)": overlay_df["kelly_stake"].apply(
            lambda x: f"${x:,.0f}"
        ),
    })
    st.dataframe(stake_summary, use_container_width=True, hide_index=True)

# ==========================================
# SECTION 4: DETAILED RACECARD MATRIX
# ==========================================
st.markdown("---")
st.subheader(f"📋 Full Racecard Matrix: {selected_date} | Race {selected_race}")

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
        "Overlay Flag": "Value Status",
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
df_display["Expected Value (EV)"] = df["EV"].apply(lambda x: f"{x:+.2f}")

matrix_cols = [
    "No.",
    "Horse Name",
    "Brand",
    "Jockey",
    "Trainer",
    "Draw",
    "Wt",
    "Rtg",
    "Model PWIN %",
    "Fair Odds",
    "Live Odds (Board)",
    "Expected Value (EV)",
    "Value Status",
]

st.dataframe(df_display[matrix_cols], use_container_width=True, hide_index=True)
