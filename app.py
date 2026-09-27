import base64
import json
import time
import psycopg2
import requests
import streamlit as st

# Retrieve Gemini API Key from Streamlit Secrets
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")

with st.expander(
    "⚡ Fast Live Odds Manual / Screenshot Sync (Click to Open)", expanded=False
):
  st.markdown("### 📸 Batch Upload HKJC / on.cc Odds Screenshots")

  # Multi-file batch uploader
  uploaded_files = st.file_uploader(
      "Choose HKJC / on.cc Odds Screenshots (Select up to 11 files)...",
      type=["png", "jpg", "jpeg", "webp"],
      accept_multiple_files=True,
  )

  if uploaded_files and st.button("🚀 Process All Uploaded Screenshots (OCR)"):
    if not GEMINI_API_KEY:
      st.error(
          "❌ GEMINI_API_KEY is missing in Streamlit Secrets! Please add it in"
          " App Settings."
      )
      st.stop()

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
        # 1. Encode image bytes to Base64
        image_bytes = uploaded_file.getvalue()
        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        # 2. Build Gemini REST API Payload
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

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt_text},
                    {
                        "inline_data": {
                            "mime_type": uploaded_file.type,
                            "data": base64_image,
                        }
                    },
                ]
            }]
        }

        # 3. Call Gemini REST API directly
        res = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )

        if res.status_code == 200:
          resp_json = res.json()
          raw_text = (
              resp_json.get("candidates", [{}])[0]
              .get("content", {})
              .get("parts", [{}])[0]
              .get("text", "")
          )

          # Parse JSON output
          cleaned_json_str = (
              raw_text.replace("```json", "").replace("```", "").strip()
          )
          parsed_data = json.loads(cleaned_json_str)

          race_no = parsed_data.get("race_no")
          odds_map = parsed_data.get("odds", {})

          if race_no and odds_map:
            # 4. Update Neon Cloud DB
            conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
            cur = conn.cursor()

            for horse_str, odds_val in odds_map.items():
              cur.execute(
                  """
                                UPDATE model_pwin_results 
                                SET live_odds = %s 
                                WHERE race_date = %s AND race_no = %s AND horse_no = %s;
                            """,
                  (
                      float(odds_val),
                      selected_date,
                      int(race_no),
                      int(horse_str),
                  ),
              )

            conn.commit()
            cur.close()
            conn.close()

            success_count += 1
            st.success(
                f"✅ Race {race_no}: Parsed {len(odds_map)} runners from"
                f" {uploaded_file.name}"
            )
          else:
            st.warning(f"⚠️ Could not parse race odds from {uploaded_file.name}")
        else:
          st.error(
              f"❌ Gemini API returned status {res.status_code}: {res.text}"
          )

      except Exception as e:
        st.error(f"❌ Error processing {uploaded_file.name}: {e}")

      progress_bar.progress((idx + 1) / total_files)

    status_text.text(
        f"🎉 Batch processing complete! Successfully updated {success_count}/{total_files} race screenshots."
    )
    time.sleep(1)
    st.rerun()
