# -*- coding: utf-8 -*-
import itertools
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
# HENERY RUNNING-STYLE CORRELATION HELPERS
# ==========================================
def detect_running_style(in_running_text):
  """Extracts running style from first sectional running position."""
  text = str(in_running_text).strip()
  if not text or text == "None" or text == "nan":
    return "MID"

  try:
    first_pos = int(text.split("-")[0])
    if first_pos <= 3:
      return "LEADER"
    elif first_pos <= 7:
      return "MID"
    else:
      return "CLOSER"
  except Exception:
    return "MID"


def calculate_henery_pair_multiplier(style_a, style_b):
  """Applies Henery correlation dampening based on running style interaction."""
  if style_a == "LEADER" and style_b == "LEADER":
    return 0.70  # 30% penalty: Dual leaders burn out early pace
  elif style_a == "CLOSER" and style_b == "CLOSER":
    return 0.85  # 15% penalty: Both vulnerable in slow-paced races
  else:
    return 1.00  # Optimal tactical complement


# ==========================================
# HEAD-TO-HEAD (H2H) MATRIX CALCULATOR
# ==========================================
def calculate_h2h_multipliers(
    df_race, df_all, pos_col, min_encounters=2, penalty_factor=0.15
):
  """Analyzes in-memory historical race results to detect pairwise dominance."""
  multipliers = {int(h_no): 1.0 for h_no in df_race["horse_no"]}

  if pos_col is None or df_all is None or df_all.empty:
    return multipliers

  horse_names = df_race["horse_name"].dropna().unique().tolist()
  if len(horse_names) < 2:
    return multipliers

  try:
    df_history = df_all[
        (df_all["horse_name"].isin(horse_names)) & (df_all[pos_col].notnull())
    ].copy()

    df_history[pos_col] = pd.to_numeric(df_history[pos_col], errors="coerce")
    df_history = df_history[df_history[pos_col] > 0]

    if df_history.empty:
      return multipliers

    grouped = df_history.groupby(["race_date", "race_no"])
    h2h_stats = {}

    for _, group in grouped:
      if len(group) < 2:
        continue

      runners = group[["horse_name", pos_col]].to_dict("records")

      for r1, r2 in itertools.combinations(runners, 2):
        name_a, pos_a = r1["horse_name"], r1[pos_col]
        name_b, pos_b = r2["horse_name"], r2[pos_col]

        if pos_a == pos_b:
          continue

        pair_key = (name_a, name_b) if name_a < name_b else (name_b, name_a)
        if pair_key not in h2h_stats:
          h2h_stats[pair_key] = {name_a: 0, name_b: 0, "total": 0}

        h2h_stats[pair_key]["total"] += 1
        if pos_a < pos_b:
          h2h_stats[pair_key][name_a] += 1
        else:
          h2h_stats[pair_key][name_b] += 1

    name_to_no = df_race.set_index("horse_name")["horse_no"].to_dict()

    for (name_a, name_b), stats in h2h_stats.items():
      total_meets = stats["total"]
      if total_meets >= min_encounters:
        wins_a = stats[name_a]
        wins_b = stats[name_b]

        if wins_a == 0 and wins_b == total_meets:
          h_no_a = name_to_no.get(name_a)
          if h_no_a in multipliers:
            multipliers[h_no_a] *= 1.0 - penalty_factor
        elif wins_b == 0 and wins_a == total_meets:
          h_no_b = name_to_no.get(name_b)
          if h_no_b in multipliers:
            multipliers[h_no_b] *= 1.0 - penalty_factor

  except Exception as e:
    print(f"⚠️ H2H Notice: {e}")

  return multipliers


# ==========================================
# REFINEMENT ENGINE CORE LOGIC
# ==========================================
def calculate_refined_pwin(df_race, df_all, pos_col, alpha=0.65):
  """Applies Bayesian place penalties, health dampening, H2H dampening,

  and market shrinkage to recalculate clean PWIN and Fair Odds.
  """
  df = df_race.copy()

  df["model_pwin"] = df["model_pwin"].fillna(0.01).astype(float)
  df["live_odds"] = df["live_odds"].fillna(10.0).astype(float)

  if "career_starts" not in df.columns:
    df["career_starts"] = 0
  if "career_places" not in df.columns:
    df["career_places"] = 0
  if "health_notes" not in df.columns:
    df["health_notes"] = ""
  if "in_running" not in df.columns:
    df["in_running"] = ""

  df["career_starts"] = df["career_starts"].fillna(0).astype(int)
  df["career_places"] = df["career_places"].fillna(0).astype(int)
  df["health_notes"] = df["health_notes"].fillna("").astype(str)

  # 1. Maiden Zero-Place Penalty
  def get_place_penalty(row):
    if row["career_starts"] >= 5 and row["career_places"] == 0:
      return 0.20
    elif row["career_starts"] >= 3 and row["career_places"] == 0:
      return 0.50
    return 1.0

  df["place_multiplier"] = df.apply(get_place_penalty, axis=1)

  # 2. Health / Veterinary Penalty
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

  # 3. Direct Head-to-Head (H2H) Multipliers
  h2h_map = calculate_h2h_multipliers(
      df, df_all, pos_col, min_encounters=2, penalty_factor=0.15
  )
  df["h2h_multiplier"] = df["horse_no"].map(h2h_map).fillna(1.0)

  # 4. Running Style & Henery Adjustment
  df["running_style"] = df["in_running"].apply(detect_running_style)

  # 5. Apply All Penalties to Raw PWIN
  df["pwin_adjusted"] = (
      df["model_pwin"]
      * df["place_multiplier"]
      * df["health_multiplier"]
      * df["h2h_multiplier"]
  )

  # 6. Market Probability Blending (Shrinkage)
  df["market_prob"] = df["live_odds"].apply(
      lambda odds: (1.0 / odds) if odds > 0 else 0.05
  )
  df["pwin_blended"] = (alpha * df["pwin_adjusted"]) + (
      (1.0 - alpha) * df["market_prob"]
  )

  # 7. Field Re-normalization
  field_sum = df["pwin_blended"].sum()
  if field_sum > 0:
    df["model_pwin_refined"] = df["pwin_blended"] / field_sum
  else:
    df["model_pwin_refined"] = 1.0 / len(df)

  # 8. Recalculate Fair Odds
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

  df_all = pd.read_sql("SELECT * FROM model_pwin_results;", conn)

  if df_all.empty:
    print("⚠️ No data found in model_pwin_results table.")
    conn.close()
    return

  pos_col = None
  for candidate in [
      "finish_position",
      "place",
      "rank",
      "placing",
      "position",
      "fin_pos",
  ]:
    if candidate in df_all.columns:
      pos_col = candidate
      break

  races = (
      df_all[["race_date", "race_no"]]
      .drop_duplicates()
      .sort_values(by=["race_date", "race_no"], ascending=[False, True])
      .values
  )

  print(f"📊 Found {len(races)} total race meetings to re-process.")

  updated_records = 0

  for race_date, race_no in races:
    df_race = df_all[
        (df_all["race_date"] == race_date) & (df_all["race_no"] == race_no)
    ].copy()

    if df_race.empty:
      continue

    df_refined = calculate_refined_pwin(df_race, df_all, pos_col, alpha=0.65)

    update_data = []
    for _, r in df_refined.iterrows():
      update_data.append((
          float(r["model_pwin_refined"]),
          float(r["fair_odds_refined"]),
          str(race_date),
          int(race_no),
          int(r["horse_no"]),
      ))

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
