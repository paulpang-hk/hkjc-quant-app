# -*- coding: utf-8 -*-
import os
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch

# ==========================================
# CONFIGURATION & NEON DB CONNECTION
# ==========================================
NEON_DB_URL = os.environ.get(
    "NEON_DB_URL",
    "postgresql://neondb_owner:npg_D2YzinaM8grT@ep-snowy-fire-b59poqzm-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
  return psycopg2.connect(NEON_DB_URL)


# ==========================================
# REFINEMENT ENGINE CORE LOGIC
# ==========================================
def calculate_refined_pwin(df_race, alpha=0.65):
  """Applies Bayesian place penalties, health dampening, and market shrinkage

  to recalculate clean PWIN and Fair Odds across a race field.
  """
  df = df_race.copy()

  # Ensure numerical types
  df["model_pwin"] = df["model_pwin"].fillna(0.01).astype(float)
  df["live_odds"] = df["live_odds"].fillna(10.0).astype(float)

  # Extract optional penalty columns if present in DB schema, otherwise set defaults
  if "career_starts" not in df.columns:
    df["career_starts"] = 0
  if "career_places" not in df.columns:
    df["career_places"] = 0
  if "health_notes" not in df.columns:
    df["health_notes"] = ""

  # 1. Calculate Maiden Zero-Place Penalty
  def get_place_penalty(row):
    if row["career_starts"] >= 5 and row["career_places"] == 0:
      return 0.20
    elif row["career_starts"] >= 3 and row["career_places"] == 0:
      return 0.50
    return 1.0

  df["place_multiplier"] = df.apply(get_place_penalty, axis=1)

  # 2. Calculate Health / Veterinary Penalty
  def get_health_penalty(notes):
    text = str(notes).lower()
    penalty = 1.0
    if "lame" in text:
      penalty *= 0.50
    if "unacceptable performance" in text:
      penalty *= 0.60
    if "withdrawn" in text:
      penalty *= 0.80
    return penalty

  df["health_multiplier"] = df["health_notes"].apply(get_health_penalty)

  # 3. Apply Penalties to Raw PWIN
  df["pwin_adjusted"] = (
      df["model_pwin"] * df["place_multiplier"] * df["health_multiplier"]
  )

  # 4. Market Probability Blending (Shrinkage)
  df["market_prob"] = df["live_odds"].apply(
      lambda odds: (1.0 / odds) if odds > 0 else 0.05
  )
  df["pwin_blended"] = (alpha * df["pwin_adjusted"]) + (
      (1.0 - alpha) * df["market_prob"]
  )

  # 5. Field Re-normalization (Sum = 1.0)
  field_sum = df["pwin_blended"].sum()
  if field_sum > 0:
    df["model_pwin_refined"] = df["pwin_blended"] / field_sum
  else:
    df["model_pwin_refined"] = 1.0 / len(df)

  # 6. Recalculate Fair Odds
  df["fair_odds_refined"] = df["model_pwin_refined"].apply(
      lambda p: round(1.0 / p, 2) if p > 0 else 999.0
  )
  df["model_pwin_refined"] = df["model_pwin_refined"].apply(
      lambda p: round(p, 4)
  )

  return df


# ==========================================
# DATABASE RE-CALCULATION & SYNC PIPELINE
# ==========================================
def run_pipeline():
  print("🔄 Connecting to Neon Cloud DB...")
  conn = get_db_connection()
  cur = conn.cursor()

  # Fetch distinct race meetings
  cur.execute(
      "SELECT DISTINCT race_date, race_no FROM model_pwin_results ORDER BY"
      " race_date DESC, race_no ASC;"
  )
  races = cur.fetchall()

  print(f"📊 Found {len(races)} total race meetings to re-process.")

  updated_records = 0

  for race_date, race_no in races:
    query = """
            SELECT horse_no, model_pwin, live_odds, 
                   COALESCE(career_starts, 0) as career_starts, 
                   COALESCE(career_places, 0) as career_places, 
                   COALESCE(health_notes, '') as health_notes
            FROM model_pwin_results
            WHERE race_date = %s AND race_no = %s;
        """
    df_race = pd.read_sql(query, conn, params=(race_date, race_no))

    if df_race.empty:
      continue

    # Execute mathematical refinement
    df_refined = calculate_refined_pwin(df_race, alpha=0.65)

    # Prepare batch update data
    update_data = []
    for _, r in df_refined.iterrows():
      update_data.append((
          float(r["model_pwin_refined"]),
          float(r["fair_odds_refined"]),
          str(race_date),
          int(race_no),
          int(r["horse_no"]),
      ))

    # Bulk update Neon DB
    update_query = """
            UPDATE model_pwin_results
            SET model_pwin = %s,
                fair_odds = %s
            WHERE race_date = %s AND race_no = %s AND horse_no = %s;
        """
    execute_batch(cur, update_query, update_data)
    conn.commit()
    updated_records += len(update_data)

  cur.close()
  conn.close()
  print(
      f"✅ Successfully recalculated and updated {updated_records} runners in"
      " Neon PostgreSQL!"
  )


if __name__ == "__main__":
  run_pipeline()
