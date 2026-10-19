import streamlit as st
import psycopg2
import pandas as pd
from itertools import combinations

# ----------------------------------------------------------------------
# 1. 頁面全屏寬度配置與 Neon DB 連線 (大寫 T 密碼)
# ----------------------------------------------------------------------
st.set_page_config(page_title="HKJC 量化博弈決策系統", layout="wide", page_icon="🏇")

DB_URL = "postgresql://neondb_owner:npg_D2YzinaM8grT@ep-snowy-fire-b59poqzm-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require"

st.title("🏇 HKJC 端到端量化博弈決策系統")
st.caption("數據源：on.cc 即時賠率 | 核心算法：Softmax 三階段校準與 Quarter-Kelly 配置")

# ----------------------------------------------------------------------
# 2. 頂部水平導航與控制卡 (Top Control Bar)
# ----------------------------------------------------------------------
try:
    with psycopg2.connect(DB_URL) as conn:
        dates_df = pd.read_sql("SELECT DISTINCT race_date FROM race_predictions ORDER BY race_date DESC;", conn)
except Exception as e:
    st.error(f"資料庫連線失敗: {e}")
    st.stop()

if dates_df.empty:
    st.warning("資料庫尚無排位數據，請先運行 1_ingest.py。")
    st.stop()

available_dates = [str(d) for d in dates_df['race_date']]

col_date, col_race, col_bankroll = st.columns([1.2, 1.2, 1.2])

with col_date:
    selected_date = st.selectbox("📅 選擇賽事日期", available_dates)

with col_race:
    selected_race = st.selectbox(
        "🏇 選擇場次 (Race)", 
        options=list(range(1, 12)), 
        format_func=lambda x: f"第 {x} 場 (Race {x})",
        index=0
    )

with col_bankroll:
    bankroll = st.number_input(
        "💰 總投注本金 (HKD)", 
        min_value=1000, 
        max_value=1000000, 
        value=10000, 
        step=1000
    )

st.markdown("---")

# ----------------------------------------------------------------------
# 3. 讀取數據 (自動剔除退賽馬匹)
# ----------------------------------------------------------------------
with psycopg2.connect(DB_URL) as conn:
    sql = """
        SELECT horse_no, horse_name, draw, rating, raw_score,
               p_base, p_refined, fair_odds, oncc_odds, single_ev, kelly_pct
        FROM race_predictions
        WHERE race_date = %s AND race_no = %s 
          AND draw > 0 
          AND horse_name NOT LIKE '%%Scratched%%' 
          AND horse_name NOT LIKE '%%退出%%'
        ORDER BY horse_no ASC;
    """
    df = pd.read_sql(sql, conn, params=(selected_date, selected_race))

if df.empty:
    st.info(f"第 {selected_race} 場尚未找到有效參戰馬匹資料。")
    st.stop()

# ----------------------------------------------------------------------
# 4. 決策狀態與防禦條款判定 (Anti-SPOF)
# ----------------------------------------------------------------------
sit_type = "WAITING"
sit_title = "⏳ 等待 on.cc 即時賠率更新"
sit_desc = "當前尚無即時賠率數據，請先執行 2_odds.py 採集賠率。"
box_func = st.warning

df_sorted = df.sort_values(by='p_refined', ascending=False).reset_index(drop=True)
top_horse = df_sorted.iloc[0]
top_p = top_horse['p_refined'] if pd.notnull(top_horse['p_refined']) else 0.0
has_odds = df['oncc_odds'].notnull().any()
max_ev = df['single_ev'].max() if pd.notnull(df['single_ev'].max()) else 0.0

if not has_odds:
    pass # 保持 WAITING
elif top_p >= 0.28 and max_ev > 1.15:
    sit_type = "A"
    sit_title = "情況 A：超級強膽 (Super Banker)"
    sit_desc = f"首選馬 {int(top_horse['horse_no'])}號【{top_horse['horse_name']}】精煉勝率達 {top_p*100:.2f}% (>=28%)，組合 EV > 1.15，具備高信心單膽條件。"
    box_func = st.success
elif top_p >= 0.18 and max_ev > 1.0:
    sit_type = "B"
    sit_title = "情況 B：勢均力敵 / 噪訊防禦 (Boxed 4 Defense)"
    sit_desc = f"首選馬 {int(top_horse['horse_no'])}號【{top_horse['horse_name']}】精煉勝率為 {top_p*100:.2f}% (<28%)，觸發 Anti-SPOF 條款，嚴禁單膽，強制切換至 4 馬複式防禦！"
    box_func = st.info
elif max_ev > 1.20:
    sit_type = "C"
    sit_title = "情況 C：彩池優勢偏低 (Fallback Single W/P)"
    sit_desc = "勝率或組合期望值不足，退回單場獨贏/位置 (Win/Place)，僅投注單馬 EV > 1.20 標的。"
    box_func = st.warning
else:
    sit_type = "D"
    sit_title = "情況 D：無博弈優勢 (NO BET / PASS)"
    sit_desc = "全場馬匹單馬 EV 均 <= 1.00，無正期望值博弈空間，本場放棄投注。"
    box_func = st.error

# ----------------------------------------------------------------------
# 🎯 區塊一：策略說明與精確投注指令 (置頂)
# ----------------------------------------------------------------------
st.subheader("1. 策略判定與精確投注指令 (Strategy & Executions)")

# 顯示決策大標與邏輯說明
box_func(f"**決策彩池：{sit_title}**\n\n**防禦邏輯：** {sit_desc}")

c1, c2 = st.columns(2)
with c1:
    top4 = [int(h) for h in df_sorted.head(4)['horse_no'].tolist()]
    top4_names = df_sorted.head(4)['horse_name'].tolist()
    top4_str = ", ".join([f"{h}號 ({n})" for h, n in zip(top4, top4_names)])
    st.markdown(f"**🏅 推薦前四強馬匹：** {top4_str}")
    st.markdown("**📡 賠率數據源：** `on.cc Live Feed (唯一指定來源)`")

with c2:
    total_kelly = df['kelly_pct'].fillna(0).sum()
    capped_k = min(total_kelly, 0.05)
    total_stake = int(capped_k * bankroll)
    st.metric("Quarter-Kelly 資金建議 (單場上限 5%)", f"{capped_k * 100:.2f}%", f"建議總注額: HKD ${total_stake}")

st.markdown("##### 📝 組合注數與資金配置明細")

if sit_type == "A":
    banker = top4[0]
    legs = top4[1:]
    combs = [f"{min(banker, l)}-{max(banker, l)}" for l in legs]
    st.info(f"**1 膽 3 腳 (Q / QP)** | 主膽：`{banker}號`，配腳：`{legs}`\n\n• **連贏 (Q) (60% 資金)**：組合 {combs}\n\n• **位置 Q (QP) (40% 資金)**：組合 {combs}")
elif sit_type == "B":
    combs = [f"{a}-{b}" for a, b in combinations(top4, 2)]
    st.success(f"**4 馬複式 (Boxed 4 Q/QP)** | 選定馬匹：`{top4}`\n\n• **連贏 (Q) 複式 (50% 資金)**：組合 {combs} (共 6 注)\n\n• **位置 Q (QP) 複式 (50% 資金)**：組合 {combs} (共 6 注)")
elif sit_type == "C":
    ev_horses = df[df['single_ev'] > 1.20]
    if not ev_horses.empty:
        w_p_desc = "**單場 W/P 配置 (30% 獨贏 / 70% 位置)**：\n"
        for _, r in ev_horses.iterrows():
            rec_bet = int(min(r['kelly_pct'], 0.05) * bankroll)
            w_p_desc += f"\n• `{int(r['horse_no'])}號 {r['horse_name']}` | 單馬 EV: `{r['single_ev']:.2f}` | 建議注碼: `${rec_bet}`"
        st.warning(w_p_desc)
    else:
        st.warning("無單馬 EV > 1.20 之標的，建議本場觀望。")
elif sit_type == "D":
    st.error("🛑 指令：PASS / 本場放棄投注")

st.markdown("---")

# ----------------------------------------------------------------------
# 📊 區塊二：核心勝率與期望值矩陣 (置底)
# ----------------------------------------------------------------------
st.subheader("2. 核心勝率與期望值矩陣 (Probability & EV Matrix)")

def format_pct(val):
    return f"{val * 100:.2f}%" if pd.notnull(val) else "-"

def format_odds(val):
    return f"{val:.2f}" if pd.notnull(val) else "-"

def format_ev(val):
    return f"{val:.4f}" if pd.notnull(val) else "-"

def format_stake(val, total_bankroll):
    if pd.notnull(val) and val > 0:
        actual_stake =
