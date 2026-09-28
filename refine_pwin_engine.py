# -*- coding: utf-8 -*-
import os
import itertools
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
# HEAD-TO-HEAD (H2H) MATRIX CALCULATOR
# ==========================================
def calculate_h2h_multipliers(df_race, conn, min_encounters=2, penalty_factor=0.15):
  """
  Queries historical race results to detect pairwise dominance between runners
  in the current race field. Returns a dict of {horse_no: h2h_multiplier}.
  """
  horse_names = df_race["horse_name"].dropna().unique().tolist()
  multipliers = {int(h_no): 1.0 for h_no in df_race["horse_no"]}

  if len(horse_names) < 2:
    return multipliers

  try:
    # Query past finished races where at least two of the current runners competed
    query = """
            SELECT race_date, race_no, horse_name, finish_position
            FROM model_pwin_results
            WHERE horse_name = ANY(%s) 
              AND finish_position IS NOT NULL 
              AND finish_position > 0
            ORDER BY race_date DESC, race_no ASC;
        """
    df_history = pd.read_sql(query, conn, params=(horse_names,))

    if df_history.empty:
      return multipliers

    # Group by past race event to analyze head-to-head outcomes
    grouped = df_history.groupby(["race_date", "race_no"])

    # Dictionary to track pairwise wins: h2h_stats[(Horse_A, Horse_B)] = {'meets': X, 'a_losses': Y}
    h2h_stats = {}

    for _, group in grouped:
      if len(group) < 2:
        continue  # Need at least two horses in the same historical race
      
      runners_in_race = group[["horse_name", "finish_position"]].to_dict("records")
      
      for r1, r2 in itertools.combinations(runners_in_race, 2):
        name_a, pos_a = r1["horse_name"], r1["finish_position"]
        name_b, pos_b = r2["horse_name"], r2["finish_position"]

        if pos_a == pos_b:
          continue

        # Sort names to maintain consistent dict key order
        pair_key = (name_a, name_b) if name_a < name_b else (name_b, name_a)
        if pair_key not in h2h_stats:
          h2h_stats[pair_key] = {name_a: 0, name_b: 0, "total": 0}

        h2h_stats[pair_key]["total"] += 1
        if pos_a < pos_b:
          h2h_stats[pair_key][name_a] += 1  # Name A beat Name B
        else:
          h2h_stats[pair_key][name_b] += 1  # Name B beat Name A

    # Calculate penalties based on historical dominance
    name_to_no = df_race.set_index("horse_name")["horse_no"].to_dict()

    for (name_a, name_b), stats in h2h_stats.items():
      total_meets = stats["total"]
      if total_meets >= min_encounters:
        wins_a = stats[name_a]
        wins_b = stats[name_b]

        # Check if Name B completely dominates Name A (100% loss rate for A)
        if wins_a == 0 and wins_b == total_meets:
          h_no_a = name_to_no.get(name_a)
          if h_no_a in multipliers:
            multipliers[h_no_a] *= (1.0 - penalty_factor)

        # Check if Name A completely dominates Name B (100% loss rate for B)
        elif wins_b == 0 and wins_a == total_meets:
          h_no_b = name_to_no.get(name_b)
          if h_no_b in multipliers:
            multipliers[h_no_b] *= (1.0 - penalty_factor)

  except Exception as e:
    print(f"⚠️ H2H Matrix calculation skipped due to notice: {e}")

  return multipliers


# ==========================================
# REFINEMENT ENGINE CORE LOGIC
# ==========================================
def calculate_refined_pwin(df_race, conn, alpha=0.65):
  """
  Applies Bayesian place penalties, health dampening, H2H matrix dampening,
  and market shrinkage to recalculate clean PWIN and Fair Odds.
  """
  df = df_race.copy()

  # Ensure numerical types
  df["model_pwin"] = df["model_pwin"].fillna(0.01).astype(float)
  df["live_odds"] = df["live_odds"].fillna(10.0).astype(float)

  # Safely handle missing optional columns if not present in DB schema
  if "career_starts" not in df.columns:
    df["career_starts"] = 0
  if "career_places" not in df.columns:
    df["career_places"] = 0
  if "health_notes" not in df.columns:
    df["health_notes"] = ""

  df["career_starts"] = df["career_starts"].fillna(0).astype(int)
  df["career_places"] = df["career_places"].fillna(0).astype(int)
  df["health_notes"] = df["health_notes"].fillna("").astype(str)

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
    if "unacceptable performance" in text or "unacceptable" in text:
      penalty *= 0.60
    if "withdrawn" in text:
      penalty *= 0.80
    return penalty

  df["health_multiplier"] = df["health_notes"].apply(get_health_penalty)

  # 3. Calculate Direct Head-to-Head (H2H) Multipliers
  h2h_map = calculate_h2h_multipliers(df, conn, min_encounters=2, penalty_factor=0.15)
  df["h2h_multiplier"] = df["horse_no"].map(h2h_map).fillna(1.0)

  # 4. Apply All Compound Penalties to Raw PWIN
  df["pwin_adjusted"] = (
      df["model_pwin"] 
      * df["place_multiplier"] 
      * df["health_multiplier"]
      * df["h2h_multiplier"]
  )

  # 5. Market Probability Blending (Shrinkage)
  df["market_prob"] = df["live_odds"].apply(
      lambda odds: (1.0 / odds) if odds > 0 else 0.05
  )
  df["pwin_blended"] = (alpha * df["pwin_adjusted"]) + (
      (1.0 - alpha) * df["market_prob"]
  )

  # 6. Field Re-normalization (Sum = 1.0)
  field_sum = df["pwin_blended"].sum()
  if field_sum > 0:
    df["model_pwin_refined"] = df["pwin_blended"] / field_sum
  else:
    df["model_pwin_refined"] = 1.0 / len(df)

  # 7. Recalculate Fair Odds
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
            SELECT *
            FROM model_pwin_results
            WHERE race_date = %s AND race_no = %s;
        """
    df_race = pd.read_sql(query, conn, params=(race_date, race_no))

    if df_race.empty:
      continue

    # Execute mathematical refinement including H2H Matrix
    df_refined = calculate_refined_pwin(df_race, conn, alpha=0.65)

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
      f"✅ Successfully recalculated and updated {updated_records} runners with H2H Matrix in Neon PostgreSQL!"
  )


if __name__ == "__main__":
  run_pipeline()
