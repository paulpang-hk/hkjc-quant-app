import time
import google.generativeai as genai
import streamlit as st

# Configure Gemini Vision API Key
genai.configure(api_key=st.secrets.get("GEMINI_API_KEY", ""))

with st.expander(
    "⚡ Fast Live Odds Manual / Screenshot Sync (Click to Open)", expanded=False
):
  st.markdown("### 📸 Batch Upload HKJC / on.cc Odds Screenshots")

  # Enable accept_multiple_files=True for multi-file batch drag-and-drop
  uploaded_files = st.file_uploader(
      "Choose HKJC / on.cc Odds Screenshots (Select up to 11 files)...",
      type=["png", "jpg", "jpeg", "webp"],
      accept_multiple_files=True,  # Allows batch upload
  )

  if uploaded_files and st.button("🚀 Process All Uploaded Screenshots (OCR)"):
    model = genai.GenerativeModel("gemini-1.5-flash")
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
        # Load image bytes
        image_bytes = uploaded_file.getvalue()
        image_parts = [{"mime_type": uploaded_file.type, "data": image_bytes}]

        # Prompt Gemini Vision to extract Race Number and Runner Odds
        prompt = """
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

        response = model.generate_content([prompt, image_parts[0]])
        cleaned_json_str = (
            response.text.replace("```json", "").replace("```", "").strip()
        )

        import json

        parsed_data = json.loads(cleaned_json_str)

        race_no = parsed_data.get("race_no")
        odds_map = parsed_data.get("odds", {})

        if race_no and odds_map:
          # Update Neon Database for extracted race
          import psycopg2

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

      except Exception as e:
        st.error(f"❌ Failed to process {uploaded_file.name}: {e}")

      # Update progress bar
      progress_bar.progress((idx + 1) / total_files)

    status_text.text(
        f"🎉 Batch processing complete! Successfully updated {success_count}/{total_files} race screenshots."
    )
    time.sleep(1)
    st.rerun()
