# -*- coding: utf-8 -*-
import base64
import io
import itertools
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


# Compress uploaded image in memory
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
# HARVILLE Q & PQ PROBABILITY CALCULATOR
# ==========================================
def calculate_q_pq_matrix(df_runners):
  runners = df_runners[["horse_no", "horse_name", "pwin_val"]].to_dict(
      "records"
  )
  pairs_data = []

  for h1, h2 in itertools.combinations(runners, 2):
    i, j = h1["horse_no"], h2["horse_no"]
    pi, pj = h1["pwin_val"], h2["pwin_val"]

    # Quinella Probability
    p_i1_j2 = (pi * pj) / (1.0 - pi) if (1.0 - pi) > 0 else 0
    p_j1_i2 = (pj * pi) / (1.0 - pj) if (1.0 - pj) > 0 else 0
    p_q = p_i1_j2 + p_j1_i2

    # Place Quinella Probability
    p_pq = 0.0
    for h3 in runners:
      k = h3["horse_no"]
      pk = h3["pwin_val"]
      if k == i or k == j:
        continue

      denom_i = 1.0 - pi
      denom_ij = 1.0 - pi - pj
      p_ijk = (
          (pi * pj * pk) / (denom_i * denom_ij)
          if (denom_i > 0 and denom_ij > 0)
          else 0
      )

      denom_ik = 1.0 - pi - pk
      p_ikj = (
          (pi * pk * pj) / (denom_i * denom_ik)
          if (denom_i > 0 and denom_ik > 0)
          else 0
      )

      denom_j = 1.0 - pj
      denom_ji = 1.0 - pj - pi
      p_jik = (
          (pj * pi * pk) / (denom_j * denom_ji)
          if (denom_j > 0 and denom_ji > 0)
          else 0
      )

      denom_jk = 1.0 - pj - pk
      p_jki = (
          (pj * pk * pi) / (denom_j * denom_jk)
          if (denom_j > 0 and denom_jk > 0)
          else 0
      )

      denom_k = 1.0 - pk
      denom_ki = 1.0 - pk - pi
      p_kij = (
          (pk * pi * pj) / (denom_k * denom_ki)
          if (denom_k > 0 and denom_ki > 0)
          else 0
      )

      denom_kj = 1.0 - pk - pj
      p_kji = (
          (pk * pj * pi) / (denom_k * denom_kj)
          if (denom_k > 0 and denom_kj > 0)
          else 0
      )

      p_pq += p_ijk + p_ikj + p_jik + p_jki + p_kij + p_kji

    fair_q = (1.0 / p_q) if p_q > 0 else 999.0
    fair_pq = (1.0 / p_pq) if p_pq > 0 else 999.0

    pairs_data.append({
        "Pair": f"{i} - {j}",
        "h1_no": i,
        "h2_no": j,
        "Runners": f"#{i} {h1['horse_name']} + #{j} {h2['horse_name']}",
        "Q Model %": p_q * 100,
        "Fair Q Odds": fair_q,
        "PQ Model %": p_pq * 100,
        "Fair PQ Odds": fair_pq,
    })

  return pd.DataFrame(pairs_data)


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
# EXPANDER: BATCH OCR & MANUAL SYNC
# ==========================================
with st.expander(
    "⚡ Fast Live Odds Manual / Screenshot Sync (Click to Open)", expanded=False
):
  tab_ocr, tab_manual = st.tabs(
      ["📸 Upload Odds Screenshots (AI OCR)", "✏️ Quick String Paste"]
  )

  with tab_ocr:
    st.markdown("Upload one or multiple screenshots of the odds board.")
    uploaded_files = st.file_uploader(
        "Choose Screenshots...",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="batch_ocr_uploader",
    )

    if uploaded_files and st.button(
        "🚀 Process All Uploaded Screenshots (OCR)"
    ):
      clean_api_key = OPENROUTER_API_KEY.strip()
      if not clean_api_key:
        st.error("❌ OPENROUTER_API_KEY is missing in Streamlit Secrets!")
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
                        Return ONLY a valid raw JSON object:
                        { "race_no": 1, "odds": { "1": 3.5, "2": 12.0 } }
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
                  time.sleep(8)
                else:
                  break
              except requests.exceptions.RequestException:
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
          except Exception as ex:
            st.warning(f"⚠️ Error parsing {uploaded_file.name}: {ex}")

          time.sleep(5)
          progress_bar.progress((idx + 1) / total_files)

        status_text.text(
            f"🎉 Batch processing complete! Updated {success_count}/{total_files} race screenshots."
        )
        time.sleep(1)
        st.rerun()

  with tab_manual:
    st.markdown("Paste odds string in format: `1=3.5 2=12.0 3=5.2` or line-by-line")
    with st.form("manual_odds_form"):
      manual_input = st.text_area("Live Odds String", height=100)
      submit_manual = st.form_submit_button(
          "💾 Apply Manual Odds to Current Race"
      )

      if submit_manual and manual_input.strip():
        pairs = re.findall(r"(\d+)\s*=\s*([0-9\.]+)", manual_input)
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
          st.success(
              f"✅ Updated {len(pairs)} runners for Race {selected_race}!"
          )
          time.sleep(1)
          st.rerun()

# ==========================================
# FETCH DATA & QUANT CALCULATIONS
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

# Basic calculations
df["pwin_val"] = df["model_pwin"].fillna(0).astype(float)
df["live_val"] = df["live_odds"].fillna(10.0).astype(float)
df["market_prob"] = df["live_val"].apply(lambda x: (1.0 / x) if x > 0 else 0.0)
df["EV"] = (df["pwin_val"] * df["live_val"]) - 1.0

# Calculate Quarter Kelly Stake ($10,000 default bankroll)
bankroll_default = 10000.0
kelly_frac = 0.25


def calc_kelly(row):
  p = row["pwin_val"]
  b = row["live_val"] - 1.0
  if b <= 0 or p <= 0:
    return 0.0
  f_star = (p * b - (1.0 - p)) / b
  if f_star <= 0:
    return 0.0
  return round(f_star * kelly_frac * bankroll_default, 0)


df["kelly_stake"] = df.apply(calc_kelly, axis=1)

# Sort positive EV runners for WIN bets
win_candidates = df[df["EV"] > 0.0].sort_values(by="EV", ascending=False)

# Q and PQ Pairs calculation
df_pairs = calculate_q_pq_matrix(df)
top_q_df = df_pairs.sort_values(by="Q Model %", ascending=False)
top_pq_df = df_pairs.sort_values(by="PQ Model %", ascending=False)

# ==========================================
# 🚨 2-MINUTE EXECUTIVE ACTION BET SLIP
# ==========================================
st.markdown("---")
st.subheader(
    f"🚨 2-Minute Executive Bet Slip — {selected_date} | Race {selected_race}"
)

slip_col1, slip_col2 = st.columns([1, 1])

with slip_col1:
  st.markdown("### 🏆 Top WIN Value Bets (Max 3)")
  if win_candidates.empty:
    st.info("⚠️ No positive EV WIN overlays found. Skip WIN bets for this race.")
  else:
    # Take top 3 highest EV runners
    top_3_win = win_candidates.head(3)
    win_slip_data = []
    for _, r in top_3_win.iterrows():
      win_slip_data.append({
          "Horse": f"#{r['horse_no']} {r['horse_name']}",
          "Live Odds": f"{r['live_val']:.1f}",
          "EV Edge": f"{r['EV']:+.2f}",
          "Bet Stake ($)": f"${r['kelly_stake']:,.0f}",
      })
    st.table(pd.DataFrame(win_slip_data))

with slip_col2:
  st.markdown("### 🎯 Top Exotics (Q / PQ Recommendations)")

  # Top Favorite Anchor
  top_fav = df.loc[df["pwin_val"].idxmax()]

  # Top Overlay Runners
  top_overlays_list = [
      str(h) for h in win_candidates["horse_no"].head(3).tolist()
  ]
  overlay_str = ", ".join([f"#{h}" for h in top_overlays_list])

  st.success(
      f"**Q / PQ Banker Strategy:** Anchor **#{top_fav['horse_no']}"
      f" {top_fav['horse_name']}** with Overlays **({overlay_str})**"
  )

  top_q_1 = top_q_df.iloc[0]
  top_pq_1 = top_pq_df.iloc[0]

  exotic_summary = [
      {
          "Type": "Quinella (Q) Best Pair",
          "Pair": top_q_1["Pair"],
          "Runners": top_q_1["Runners"],
          "Fair Min Odds": f"${top_q_1['Fair Q Odds']:.2f}",
      },
      {
          "Type": "Place Quinella (PQ) Best",
          "Pair": top_pq_1["Pair"],
          "Runners": top_pq_1["Runners"],
          "Fair Min Odds": f"${top_pq_1['Fair PQ Odds']:.2f}",
      },
  ]
  st.table(pd.DataFrame(exotic_summary))

# ==========================================
# FULL QUANT ENGINE TABS
# ==========================================
st.markdown("---")
tab_win, tab_q, tab_pq = st.tabs([
    "🏆 WIN Analytics & Kelly",
    "🎯 Quinella (Q) Matrix",
    "🥉 Place Quinella (PQ) Matrix",
])

with tab_win:
  st.markdown("### 📋 Full WIN Racecard Matrix")
  df_win_disp = df.copy()
  df_win_disp.rename(
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
          "live_odds": "Live Odds",
      },
      inplace=True,
  )

  df_win_disp["Model PWIN %"] = df_win_disp["Model PWIN %"].apply(
      lambda x: f"{float(x)*100:.2f}%" if pd.notnull(x) else "None"
  )
  df_win_disp["Fair Odds"] = df_win_disp["Fair Odds"].apply(
      lambda x: f"{float(x):.2f}" if pd.notnull(x) else "None"
  )
  df_win_disp["Live Odds"] = df_win_disp["Live Odds"].apply(
      lambda x: f"{float(x):.1f}" if pd.notnull(x) else "10.0"
  )
  df_win_disp["Expected Value (EV)"] = df["EV"].apply(lambda x: f"{x:+.2f}")
  df_win_disp["Kelly Stake"] = df["kelly_stake"].apply(lambda x: f"${x:,.0f}")

  st.dataframe(
      df_win_disp[[
          "No.",
          "Horse Name",
          "Jockey",
          "Trainer",
          "Model PWIN %",
          "Fair Odds",
          "Live Odds",
          "Expected Value (EV)",
          "Kelly Stake",
      ]],
      use_container_width=True,
      hide_index=True,
  )

with tab_q:
  st.markdown("### 🎯 Quinella (Q) Full Pair Matrix")
  df_q_disp = pd.DataFrame({
      "Pair": top_q_df["Pair"],
      "Horse Names": top_q_df["Runners"],
      "Model Q Probability": top_q_df["Q Model %"].apply(
          lambda x: f"{x:.2f}%"
      ),
      "Fair Minimum Q Odds": top_q_df["Fair Q Odds"].apply(
          lambda x: f"${x:.2f}"
      ),
  })
  st.dataframe(df_q_disp, use_container_width=True, hide_index=True)

with tab_pq:
  st.markdown("### 🥉 Place Quinella (PQ) Full Pair Matrix")
  df_pq_disp = pd.DataFrame({
      "Pair": top_pq_df["Pair"],
      "Horse Names": top_pq_df["Runners"],
      "Model PQ Probability": top_pq_df["PQ Model %"].apply(
          lambda x: f"{x:.2f}%"
      ),
      "Fair Minimum PQ Odds": top_pq_df["Fair PQ Odds"].apply(
          lambda x: f"${x:.2f}"
      ),
  })
  st.dataframe(df_pq_disp, use_container_width=True, hide_index=True)
