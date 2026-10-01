# -*- coding: utf-8 -*-
"""
三頻率 RSI 選股器 — v7f 雙通道（60/40）本地資料更新與 HTML 產生器
============================================================
- 抓取 FinLab 最新資料，依 v7f 規則算出本週選股清單：
    A 通道（60%）：v7e 前60% 15 檔（RSI20 排序）
    B 通道（40%）：權值龍頭 4 檔（tsm_long 開關 ON 時）
- 讀取歷史回測績效（my_nav_v7f.csv / v7f_summary.json）
- 輸出 RSI選股器.html（資料全部內嵌，雙擊即開、免伺服器、免雲端、免 TG）
- 用法：python update_data.py
============================================================
"""
import os, sys, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)


def load_token():
    # GitHub Actions 用 env（secret），本機用 config.json
    env_t = (os.environ.get("FINLAB_TOKEN") or "").strip()
    if env_t:
        return env_t
    try:
        cfg = json.load(open("config.json", encoding="utf-8"))
        t = (cfg.get("FINLAB_TOKEN") or "").strip()
        if t:
            return t
    except Exception:
        pass
    return ""


TOKEN = load_token()
if not TOKEN:
    print("❌ 找不到 FinLab token（請在 config.json 填入 FINLAB_TOKEN）")
    sys.exit(1)

import pandas as pd
import numpy as np
import finlab
from finlab import data

finlab.login(api_token=TOKEN)
data.set_storage(data.FileStorage("finlab_db"))

MAX_HOLD_A = 15
MAX_HOLD_B = 4
W_A, W_B = 0.60, 0.40
TARGET_W_A = W_A * 0.95 / MAX_HOLD_A   # 3.8%
TARGET_W_B = W_B * 0.95 / MAX_HOLD_B   # 9.5%


def rsi(close, n):
    d = close.diff()
    g = d.clip(lower=0)
    l = (-d).clip(lower=0)
    ag = g.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    al = l.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return 100 - 100 / (1 + ag / al)


def common(code):
    s = str(code)
    return len(s) == 4 and s.isdigit() and not s.startswith("0")


# ---------- 1. 抓取 FinLab 資料 ----------
print("① 抓取 FinLab 資料（首次約 1~3 分鐘，之後有快取）…")
# [擬真同步] 訊號主價改用未還權收盤價（與回測/實盤同口徑；除息跳空真實反映）
close = data.get("price:收盤價").apply(pd.to_numeric, errors="coerce")
raw = close
roe_raw = data.get("fundamental_features:ROE稅後")
tv = data.get("price:成交金額").apply(pd.to_numeric, errors="coerce")

keep = [c for c in close.columns if common(c)]
close = close[keep]
raw = raw[[c for c in keep if c in raw.columns]]
tv = tv[[c for c in keep if c in tv.columns]]

name_map = {}
excl_special = set()
try:
    info = data.get("company_basic_info")
    if info is not None and "公司簡稱" in info.columns:
        key = "stock_id" if "stock_id" in info.columns else info.index.name
        name_map = info.set_index(key)["公司簡稱"].to_dict()
        excl_special |= set(info[info["公司簡稱"].astype(str).str.contains("-創", na=False, regex=False)][key].astype(str))
except Exception:
    pass

# [v7g-2] 歷史股本（季頻財報 deadline 對齊）：最新已知股本 < 6 億 排除
# = 回測引擎同源（financial_statement:股本，仟元單位）＝實盤當下已知資訊、零污染
CAP_MIN = 6e5  # 仟元 = 6 億
try:
    cap_q = data.get("financial_statement:股本").apply(pd.to_numeric, errors="coerce")
    cap_q = cap_q[[c for c in keep if c in cap_q.columns]]
    cap_dl = cap_q.deadline().reindex(close.index).ffill().shift(1)
    cap_last = cap_dl.iloc[-1]  # 最新已知公告股本
    excl_special |= set(cap_last[cap_last < CAP_MIN].index)  # NaN（無財報資料）→ 放行，不排除
except Exception as e:
    print("歷史股本載入失敗（跳過排除）:", str(e)[:100])

# [v7g-2] 排除創新板 + 最新股本 < 6 億＝實盤可操作性
if excl_special:
    keep = [c for c in keep if c not in excl_special]
    close = close[keep]
    raw = raw[[c for c in keep if c in raw.columns]]
    tv = tv[[c for c in keep if c in tv.columns]]
    print(f"② [v7g-2] 排除 -創 + 最新股本<6億 {len(excl_special)} 檔，選股池 {len(keep)} 檔")

# ---------- 2. 指標與訊號（v7f，全部 shift(1) 防未來函數） ----------
r20, r60, r120 = rsi(close, 20), rsi(close, 60), rsi(close, 120)
r20s = r20.shift(1)
ma60 = close.rolling(60).mean()
tv20 = tv.rolling(20).mean()
liq = tv20.gt(tv20.quantile(0.40, axis=1), axis=0)   # A：前60%

# A 通道（v7e）
long_up = (r120 > 55).shift(1)
mid_ok = (r60 < 75).shift(1)
rally = (r20.pct_change(3, fill_method=None) > 0.02).shift(1)
stuck = ((r20 > 75).rolling(3).sum() == 3).shift(1)
# [擬真同步] ROE 公告 deadline 對齊（公告日次日起才可用，消除前視）
roe_dl = roe_raw.deadline()
roe_dl = roe_dl[[c for c in keep if c in roe_dl.columns]]
roe_daily = roe_dl.reindex(close.index).ffill().shift(1)
roe_ok = (roe_daily > 0).fillna(True)
buyA = long_up & mid_ok & rally & stuck & roe_ok & liq.shift(1).fillna(True)
buyA_gh = buyA.shift(1)
sellA = (close.shift(1) < ma60.shift(1)) | buyA_gh.shift(80).fillna(False)
posA = buyA_gh.hold_until(sellA)

# B 通道（權值龍頭動能）
rank_tv = tv20.rank(axis=1, ascending=False)
big = rank_tv.shift(1) <= 15
bull = close.shift(1) > ma60.shift(1)
lt_up = (r120 > 60).shift(1)
not_hot = r20s < 88
buyB = big & bull & lt_up & not_hot & roe_ok
buyB_gh = buyB.shift(1)
sellB = (close.shift(1) < ma60.shift(1)) | buyB_gh.shift(80).fillna(False)
posB = buyB_gh.hold_until(sellB)

# tsm_long 開關
tsm_on = False
try:
    tsm = close["2330"]
    if pd.notna(tsm.rolling(20).mean().iloc[-1]) and pd.notna(tsm.rolling(60).mean().iloc[-1]):
        tsm_on = bool(tsm.rolling(20).mean().shift(1).iloc[-1] >= tsm.rolling(60).mean().shift(1).iloc[-1]
                      and tsm.shift(1).iloc[-1] >= tsm.rolling(60).mean().shift(1).iloc[-1])
except Exception:
    tsm_on = False

# ---------- 3. 調倉日選股清單（可由 REBAL_DAY 凍結在指定日，預設最新交易日） ----------
last_day = close.dropna(how="all").index[-1]

# [斷更偵測] FinLab 資料新鮮度：實際最後日 vs 預期最近交易日（含休市表）
def _load_holidays():
    try:
        h = json.load(open(os.path.join(HERE, "holidays.json"), encoding="utf-8"))
        s = set()
        for v in h.values():
            s.update(v)
        return s
    except Exception:
        return set()


HOLS = _load_holidays()


def _trading(d):
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in HOLS


_exp = pd.Timestamp(datetime.datetime.now().date())
while not _trading(_exp):
    _exp -= pd.Timedelta(days=1)
_missing_days = 0
_d = last_day + pd.Timedelta(days=1)
while _d <= _exp:
    if _trading(_d):
        _missing_days += 1
    _d += pd.Timedelta(days=1)
data_stale = bool(last_day < _exp and _missing_days > 1)
stale_date = last_day.strftime("%Y-%m-%d")
if data_stale:
    print(f"⚠️ 警告：FinLab 資料停在 {stale_date}，已缺 {_missing_days} 個交易日（訊號非最新）")

_rebal_env = os.environ.get("REBAL_DAY", "").strip()
if _rebal_env:
    last_day = pd.Timestamp(_rebal_env)
sig_day = last_day.strftime("%Y-%m-%d")

# A 通道：RSI20 排序前 15
curA = posA.loc[last_day]
codesA = [c for c in curA[curA].index.tolist() if c in r20.columns]
scoredA = sorted(codesA, key=lambda c: float(r20s[c].loc[last_day])
                 if pd.notna(r20s[c].loc[last_day]) else -1, reverse=True)
cur_codes_a = scoredA[:MAX_HOLD_A]

# B 通道：成交金額排序前 4
curB = posB.loc[last_day]
codesB = [c for c in curB[curB].index.tolist() if c in tv20.columns]
scoredB = sorted(codesB, key=lambda c: float(tv20[c].loc[last_day])
                 if pd.notna(tv20[c].loc[last_day]) else -1, reverse=True)
cur_codes_b = scoredB[:MAX_HOLD_B]

# 與上次清單比對（買 / 賣 / 持有）
state = {}
if os.path.exists("state.json"):
    try:
        state = json.load(open("state.json", encoding="utf-8"))
    except Exception:
        state = {}
prev_codes = state.get("codes", [])
prev_codes_b = state.get("codes_b", [])
prev_date = state.get("date")

def build_holdings(cur_codes, prev_codes, w_target):
    out = []
    for c in cur_codes:
        px = float(raw[c].loc[last_day]) if pd.notna(raw[c].loc[last_day]) else None
        r = float(r20[c].loc[last_day]) if pd.notna(r20[c].loc[last_day]) else None
        st = "hold"
        if prev_date and c not in prev_codes:
            st = "buy"
        out.append({
            "code": c, "name": name_map.get(c, c),
            "px": round(px, 2) if px else None,
            "rsi20": round(r, 1) if r is not None else None,
            "status": st, "w": w_target,
        })
    return out

_nA, _nB = len(cur_codes_a), len(cur_codes_b)
# [v7g-3 動態A] B 空手 → A 吃 95%（留 5% 現金）；B 有候選 → A60/B40（各 ×0.95）
if tsm_on and _nB == 0:
    _wa_eff, _wb_eff = 1.0, 0.0
else:
    _wa_eff, _wb_eff = (W_A, W_B) if tsm_on else (1.0, 0.0)
twA = (_wa_eff * 0.95 / _nA) if (_nA and _wa_eff > 0) else TARGET_W_A
twB = (_wb_eff * 0.95 / _nB) if (_nB and _wb_eff > 0) else TARGET_W_B
holdings_a = build_holdings(cur_codes_a, prev_codes, twA)
holdings_b = build_holdings(cur_codes_b, prev_codes_b, twB)

sells = []
if prev_date:
    sells = [{"code": c, "name": name_map.get(c, c), "pool": "A"} for c in prev_codes if c not in cur_codes_a]
    sells += [{"code": c, "name": name_map.get(c, c), "pool": "B"} for c in prev_codes_b if c not in cur_codes_b]

# 每次由 check_and_run 在「本週最後交易日」喚起即為正式調倉，
# 覆寫 state.json 作為下週比對基準（僅 COMMIT_STATE=0 的重跑/預覽不覆寫）
if os.environ.get("COMMIT_STATE", "1") != "0":
    json.dump({"date": sig_day, "codes": cur_codes_a, "codes_b": cur_codes_b},
              open("state.json", "w", encoding="utf-8"), ensure_ascii=False)

# ---------- 4. 績效資料（v7f） ----------
nav_df = pd.read_csv("my_nav_v7f.csv", encoding="utf-8-sig")
nav_df["date"] = pd.to_datetime(nav_df["date"])
nav_df = nav_df.sort_values("date").drop_duplicates("date")
nav_list = [[d.strftime("%Y-%m-%d"), round(float(n), 0)]
            for d, n in zip(nav_df["date"], nav_df["nav"])]

nav_arr = nav_df["nav"].to_numpy()
peak = np.maximum.accumulate(nav_arr)
dd_arr = (nav_arr / peak - 1) * 100
dd_list = [[d.strftime("%Y-%m-%d"), round(float(x), 2)]
           for d, x in zip(nav_df["date"], dd_arr)]

yearly = []
for y, g in nav_df.groupby(nav_df["date"].dt.year):
    if len(g) < 2:
        continue
    first, last = g["nav"].iloc[0], g["nav"].iloc[-1]
    yearly.append({"year": int(y), "ret": round((last / first - 1) * 100, 2)})

summary = json.load(open("v7f_summary.json", encoding="utf-8"))

# AI 訊號
ai = {}
try:
    ai = json.load(open("ai_signals.json", encoding="utf-8"))
except Exception:
    ai = {}

DATA = {
    "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    "sig_date": sig_day,
    "rebal_date": sig_day,
    "px_date": sig_day,
    "stale": data_stale,
    "stale_date": stale_date,
    "stale_missing": _missing_days,
    "holdings_a": holdings_a,
    "holdings_b": holdings_b,
    "sells": sells,
    "has_prev": bool(prev_date),
    "prev_date": prev_date,
    "tsm_on": tsm_on,
    "ai": ai,
    "perf": {
        "final_nav": summary.get("final_nav"),
        "cagr": summary.get("cagr"),
        "mdd": summary.get("mdd"),
        "start_capital": 100000,
        "nav": nav_list,
        "dd": dd_list,
        "yearly": yearly,
    },
    "target_w_a": twA,
    "target_w_b": twB,
    "wa_eff": round(_wa_eff, 3),
    "wb_eff": round(_wb_eff, 3),
}

# ---------- 5. 生成 HTML ----------
DATA_JSON = json.dumps(DATA, ensure_ascii=False)

HTML = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>天穹紅蓮三重脈衝時空追擊者 v7g-3（動態A·雙通道 60/40）</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
<style>
  :root{
    --bg:#0f1420; --card:#1a2233; --card2:#202b42; --line:#2c3a55;
    --txt:#e8edf5; --dim:#8fa0b8; --up:#ff4d4f; --down:#3ddc84;
    --accent:#4da3ff; --warn:#f5b942; --gold:#f5c542;
  }
  *{box-sizing:border-box; margin:0; padding:0;}
  body{background:var(--bg); color:var(--txt); font-family:"Segoe UI","Microsoft JhengHei",sans-serif; padding:20px;}
  .wrap{max-width:1180px; margin:0 auto;}
  header{display:flex; justify-content:space-between; align-items:flex-end; flex-wrap:wrap; gap:10px; margin-bottom:16px;}
  h1{font-size:24px; letter-spacing:1px;}
  h1 small{color:var(--dim); font-size:13px; font-weight:normal; margin-left:8px;}
  .meta{color:var(--dim); font-size:13px; text-align:right; line-height:1.7;}
  .cards{display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:16px;}
  .card{background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px;}
  .card .k{color:var(--dim); font-size:12px; margin-bottom:6px;}
  .card .v{font-size:24px; font-weight:700;}
  .card .s{color:var(--dim); font-size:11px; margin-top:4px;}
  .up{color:var(--up);} .down{color:var(--down);}
  section{background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:16px;}
  section h2{font-size:16px; margin-bottom:12px; display:flex; align-items:center; gap:8px;}
  section h2 .tag{font-size:11px; background:var(--card2); border:1px solid var(--line); padding:2px 8px; border-radius:20px; color:var(--dim); font-weight:normal;}
  .assetbar{display:flex; align-items:center; gap:10px; margin-bottom:12px; flex-wrap:wrap;}
  .assetbar label{color:var(--dim); font-size:13px;}
  .assetbar input{background:var(--card2); border:1px solid var(--line); color:var(--txt); padding:8px 12px; border-radius:8px; font-size:15px; width:180px;}
  table{width:100%; border-collapse:collapse; font-size:13.5px;}
  th{color:var(--dim); text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); font-weight:600; white-space:nowrap;}
  td{padding:8px 10px; border-bottom:1px solid #1f293d; white-space:nowrap;}
  tr:hover td{background:#1d2840;}
  .num{text-align:right; font-variant-numeric:tabular-nums;}
  .cell-input{width:92px; background:var(--card2); border:1px solid var(--line); color:var(--txt); padding:5px 7px; border-radius:6px; font-size:13px; text-align:right; font-variant-numeric:tabular-nums;}
  .cell-input:focus{outline:none; border-color:var(--accent);}
  .tgt{text-align:right; font-variant-numeric:tabular-nums; color:var(--dim);}
  .adj-pos{color:var(--up); font-weight:700; white-space:nowrap;}
  .adj-neg{color:var(--down); font-weight:700; white-space:nowrap;}
  .adj-zero{color:var(--dim); white-space:nowrap;}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 5px 0 0;vertical-align:-1px;}
  .badge{display:inline-block; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600;}
  .b-buy{background:rgba(255,77,79,.15); color:var(--up); border:1px solid rgba(255,77,79,.4);}
  .b-hold{background:rgba(77,163,255,.12); color:var(--accent); border:1px solid rgba(77,163,255,.35);}
  .b-sell{background:rgba(61,220,132,.12); color:var(--down); border:1px solid rgba(61,220,132,.4);}
  .chart{width:100%; height:340px;}
  .chart.small{height:200px;}
  .note{color:var(--dim); font-size:12px; margin-top:10px; line-height:1.8;}
  .warnbox{background:rgba(245,185,66,.1); border:1px solid rgba(245,185,66,.4); color:var(--warn); padding:10px 14px; border-radius:10px; font-size:13px; margin-bottom:14px;}
  .switch{display:inline-flex; align-items:center; gap:8px; padding:6px 14px; border-radius:20px; font-size:13px; font-weight:600; margin-bottom:12px;}
  .sw-on{background:rgba(61,220,132,.12); color:var(--down); border:1px solid rgba(61,220,132,.4);}
  .sw-off{background:rgba(245,185,66,.1); color:var(--warn); border:1px solid rgba(245,185,66,.4);}
  .ai-grid{display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:10px;}
  .ai-item{background:var(--card2); border:1px solid var(--line); border-radius:10px; padding:10px 12px;}
  .ai-item .k{color:var(--dim); font-size:12px;}
  .ai-item .v{font-size:16px; font-weight:600; margin-top:4px;}
  .empty{color:var(--dim); padding:14px 0; font-size:13px;}
  footer{color:var(--dim); font-size:11px; text-align:center; padding:16px 0 8px; line-height:1.8;}
  button{background:var(--accent); color:#0f1420; border:none; padding:8px 20px; border-radius:8px; font-size:14px; font-weight:700; cursor:pointer;}
  button:hover{filter:brightness(1.1);}
  input[type=month]{background:var(--card2); border:1px solid var(--line); color:var(--txt); padding:8px 12px; border-radius:8px; font-size:14px; width:150px;}
  @media (max-width:900px){ .cards{grid-template-columns:repeat(2,1fr);} .ai-grid{grid-template-columns:1fr;} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🌩️ 天穹紅蓮三重脈衝時空追擊者<small>v7g-3 動態A·雙通道 60/40</small></h1>
    <div class="meta">
      持股買賣推薦：<b id="m-rebal"></b>（每週最後交易日盤後更新一次）<br>
      股價更新：<b id="m-px"></b>（每日收盤 F5，持股買賣不變）
    </div>
  </header>

  <div id="stalebox" class="warnbox" style="display:none"></div>
  <div id="warnbox" class="warnbox" style="display:none"></div>

  <div class="cards">
    <div class="card"><div class="k">年化報酬率 CAGR</div><div class="v up" id="c-cagr">—</div><div class="s">2015-01 ~ 2026-09</div></div>
    <div class="card"><div class="k">最大回撤 MDD</div><div class="v down" id="c-mdd">—</div><div class="s">歷史回測口徑</div></div>
    <div class="card"><div class="k">最終資產</div><div class="v" id="c-nav">—</div><div class="s">初始本金 <span id="c-cap">—</span></div></div>
    <div class="card"><div class="k">權值通道</div><div class="v" id="c-sw">—</div><div class="s">tsm_long 開關</div></div>
  </div>

  <div id="ai-banner" class="warnbox" style="display:none"></div>

  <section>
    <h2>🎯 A 通道選股清單（60%）<span class="tag" id="h-cnt-a"></span></h2>
    <div class="assetbar">
      <label>目前總資產（元）</label>
      <input type="number" id="asset" value="1000000" step="10000" min="0">
      <span class="note">輸入你的進場價與持股，系統自動算目標股數與加減碼（資料只存在你自己的瀏覽器）</span>
    </div>
    <table>
      <thead><tr>
        <th>狀態</th><th>代號</th><th>名稱</th><th class="num">收盤價</th>
        <th class="num">RSI20</th><th class="num">我的進場價</th><th class="num">目前持股</th>
        <th class="num">損益(%)</th><th class="num">目標股數</th><th class="num">調整(+-)</th>
      </tr></thead>
      <tbody id="tb-a"></tbody>
    </table>
    <div class="note"><span class="dot" style="background:#ff4d4f"></span>買進 = 本週新進訊號　<span class="dot" style="background:#4da3ff"></span>持有 = 續抱　<span class="dot" style="background:#3ddc84"></span>賣出 = 掉出清單建議賣出　A 通道額度（tsm ON 約 60%、OFF 為 100%）按當週實際檔數均分</div>
  </section>

  <section id="b-sec">
    <h2>📌 B 通道權值（40%）<span class="tag" id="h-cnt-b"></span></h2>
    <div class="switch" id="sw-box">—</div>
    <table>
      <thead><tr>
        <th>狀態</th><th>代號</th><th>名稱</th><th class="num">收盤價</th>
        <th class="num">RSI20</th><th class="num">我的進場價</th><th class="num">目前持股</th>
        <th class="num">損益(%)</th><th class="num">目標股數</th><th class="num">調整(+-)</th>
      </tr></thead>
      <tbody id="tb-b"></tbody>
    </table>
    <div class="note">B 通道僅在台積電站上季線（tsm_long ON）時啟用，額度約 40% 按當週實際檔數均分（最多 4 檔）</div>
  </section>

  <section id="sell-sec" style="display:none">
    <h2>📉 本週建議賣出<span class="tag" id="s-cnt"></span></h2>
    <table>
      <thead><tr><th>通道</th><th>代號</th><th>名稱</th><th>原因</th></tr></thead>
      <tbody id="tb-sell"></tbody>
    </table>
    <div class="note">賣出原因：持有滿 80 交易日 或 收盤跌破 MA60（季線）→ 請於週五盤後以市價賣出</div>
  </section>

  <section>
    <h2>🤖 AI 紅利監控<span class="tag" id="ai-upd"></span></h2>
    <div class="ai-grid" id="ai-grid"></div>
    <div class="note" id="ai-note"></div>
  </section>

  <section>
    <h2>🧪 互動回測試算<span class="tag">自訂年月・本金</span></h2>
    <div class="assetbar">
      <label>起始年月</label>
      <input type="month" id="bt-start">
      <label>結束年月</label>
      <input type="month" id="bt-end">
      <label>初始本金（元）</label>
      <input type="number" id="bt-cap" value="1000000" step="100000" min="0">
      <button id="bt-run">重新計算</button>
    </div>
    <div class="cards" id="bt-cards">
      <div class="card"><div class="k">區間最終資產</div><div class="v" id="bt-nav">—</div><div class="s" id="bt-nav-s">—</div></div>
      <div class="card"><div class="k">區間年化 CAGR</div><div class="v up" id="bt-cagr">—</div><div class="s">年化報酬率</div></div>
      <div class="card"><div class="k">區間最大回撤</div><div class="v down" id="bt-mdd">—</div><div class="s">期間內最深跌幅</div></div>
      <div class="card"><div class="k">區間累積報酬</div><div class="v" id="bt-tot">—</div><div class="s">起訖期間總報酬</div></div>
    </div>
    <div class="chart small" id="chart-bt" style="margin-top:10px"></div>
    <div class="note" style="margin-top:6px">區間內淨值曲線（依每週 v7f 回測淨值等比縮放至指定本金）</div>
    <table id="bt-yearly" style="margin-top:10px">
      <thead><tr><th>年度</th><th class="num">報酬率</th><th class="num">年底資產（按本金）</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>

  <section>
    <h2>📈 歷史績效（2015-01 ~ 2026-09 回測）</h2>
    <div class="chart" id="chart-nav"></div>
    <div class="note" style="margin-top:6px">淨值曲線（初始 10 萬元、單筆複利、每週五調倉、60/40 雙通道）</div>
    <div class="chart small" id="chart-dd" style="margin-top:10px"></div>
    <div class="note" style="margin-top:6px">回撤（Drawdown）走勢</div>
    <div class="chart small" id="chart-yr" style="margin-top:10px"></div>
    <div class="note" style="margin-top:6px">年度報酬率（%）</div>
  </section>

  <section>
    <h2>🕘 說明</h2>
    <div class="note">
      ▸ 資料源 FinLab API，訊號基準日為最新交易日（盤後收盤價）。<br>
      ▸ A 通道（60%）：RSI120&gt;55、RSI60&lt;75、RSI20三日漲&gt;2%、RSI20&gt;75 連3日、ROE&gt;0、成交金額前60%、15 檔。<br>
      ▸ B 通道（40%）：成交金額前15 + 站上MA60 + RSI120&gt;60 + RSI20&lt;88 + ROE&gt;0，取 4 檔；僅 tsm_long ON 時啟用。<br>
      ▸ 出場：持有滿 80 交易日 或 跌破 MA60。AI 訊號轉弱時建議手動降 B 通道（80/20 或全關）。<br>
      ▸ [v7g] 選股池排除：創新板（-創）與最新公告股本 &lt; 6 億之微型股（歷史股本 deadline 對齊、零未來污染；避開流動性差/易暴跌之妖股）。<br>
      ▸ 績效為 2015-01 ~ 2026-09 歷史回測（賣出成本 0.3%、買入 0%、含漲跌停跳過）。
    </div>
  </section>

  <footer>
    天穹紅蓮三重脈衝時空追擊者 v7g ｜ 資料來源：FinLab API（未還權收盤價 / ROE 稅後公告日對齊 / 成交金額 / 排除創新板與股本&lt;6億）｜績效為歷史回測統計，僅供研究參考，不構成投資建議<br>
    過去績效不代表未來表現；實盤操作請以口袋證券 App 為準。
  </footer>
</div>

<script>
const DATA = __DATA__;

function fmt(n){ return (n===null||n===undefined||isNaN(n)) ? "—" : n.toLocaleString("en-US",{maximumFractionDigits:0}); }
function pct(x){ return (x===null||x===undefined||isNaN(x)) ? "—" : (x*100).toFixed(1)+"%"; }
const $ = id => document.getElementById(id);

$("m-rebal").textContent = DATA.rebal_date || DATA.sig_date;
$("m-px").textContent = DATA.px_date || DATA.sig_date;
const P = DATA.perf;
$("c-cagr").textContent = pct(P.cagr);
$("c-mdd").textContent = pct(P.mdd);
$("c-nav").textContent = fmt(P.final_nav);
$("c-cap").textContent = fmt(P.start_capital);
$("c-sw").textContent = DATA.tsm_on ? "🟢 ON" : "⚪ OFF";
$("c-sw").className = "v " + (DATA.tsm_on ? "down" : "warn");

$("warnbox").style.display = "block";
$("warnbox").textContent = "ℹ️ 持股買賣清單為「" + (DATA.rebal_date||DATA.sig_date) + "」結算結果，下次於本週最後交易日盤後更新；中間每日僅刷新股價，持股買賣不變。";

if(DATA.stale){
  $("stalebox").style.display = "block";
  $("stalebox").textContent = "⚠️ 資料源斷更警告：FinLab 資料停在 " + DATA.stale_date + "，已缺 " + DATA.stale_missing + " 個交易日！請勿依此下單，待資料源恢復後再操作。";
}

// AI 訊號
const AI = DATA.ai || {};
$("ai-upd").textContent = AI.updated || "—";
const aiMap = [
  ["Hyperscaler capex", AI.capex],
  ["AI 變現率", AI.monet],
  ["CoWoS 產能", AI.cowos],
];
const AG = $("ai-grid");
aiMap.forEach(([k,v])=>{
  const emoji = (v||"").match(/正|跟上|滿載|成長/) ? "🟢" : ((v||"").match(/轉負|落後|鬆動|下降|減速/) ? "🔴" : "⚪");
  const el = document.createElement("div");
  el.className = "ai-item";
  el.innerHTML = `<div class="k">${k}</div><div class="v">${emoji} ${v||"—"}</div>`;
  AG.appendChild(el);
});
const aiWeak = ["capex","monet","cowos"].some(k => ["轉負","落後","鬆動","下降","減速"].some(x => (AI[k]||"").includes(x)));
if(aiWeak){
  $("ai-banner").style.display = "block";
  $("ai-banner").textContent = "⚠️ AI 訊號轉弱：建議將權值通道降為 80/20 或全關！";
}
if(AI.note) $("ai-note").textContent = "ℹ️ " + AI.note;

// B 通道開關狀態
$("sw-box").textContent = DATA.tsm_on ? "🟢 權值通道 ON（台積電站上季線 → 60/40）" : "⚪ 權值通道 OFF（台積電未站上季線 → 100% A）";
$("sw-box").className = "switch " + (DATA.tsm_on ? "sw-on" : "sw-off");

// ===== 個人參數：進場價 / 持股（localStorage 只存使用者自己瀏覽器） =====
const STORE_KEY = "rsi_user_pos_v1";
const MAXA = 15;
let userStore = (()=>{ try{ return JSON.parse(localStorage.getItem(STORE_KEY))||{}; }catch(e){ return {}; } })();
function saveStore(){ try{ localStorage.setItem(STORE_KEY, JSON.stringify(userStore)); }catch(e){} }

const assetInput = $("asset");
const isFirst = !DATA.has_prev;
// 目標權重＝池有效額度×0.95÷該池實際檔數（與回測引擎一致）
function wA(){ return DATA.target_w_a; }
const wB = DATA.target_w_b;

// 掉出最新清單（建議賣出）的標的：下週不顯示，並清掉本地殘留
const curCodeSet = new Set([...DATA.holdings_a.map(h=>h.code), ...DATA.holdings_b.map(h=>h.code)]);
Object.keys(userStore).forEach(c=>{ if(!curCodeSet.has(c)) delete userStore[c]; });
saveStore();

function recalcRow(code, w){
  const pxEl = document.querySelector(`input[data-code="${code}"][data-field="px"]`);
  if(!pxEl) return;
  const qtyEl = document.querySelector(`input[data-code="${code}"][data-field="qty"]`);
  const spot = parseFloat(pxEl.dataset.spot)||0;   // 現價（收盤價）
  const cost = parseFloat(pxEl.value)||0;          // 我的進場價（僅用於損益）
  const qty = parseFloat(qtyEl.value)||0;
  const asset = parseFloat(assetInput.value)||0;

  // 損益：現價 vs 進場價
  const plEl = document.querySelector(`[data-code="${code}"][data-field="pl"]`);
  if(qty>0 && cost>0 && spot>0){
    const plAmt=(spot-cost)*qty, plPct=(spot/cost-1)*100;
    plEl.innerHTML=`<span class="${plAmt>=0?"adj-pos":"adj-neg"}">${plAmt>=0?"+":""}${Math.round(plAmt).toLocaleString("en-US")}<br>(${plPct>=0?"+":""}${plPct.toFixed(1)}%)</span>`;
  } else plEl.innerHTML="—";

  // 目標股數與再平衡，一律用現價 spot
  const tgt = (spot>0 && asset>0) ? Math.floor(asset*w/spot) : 0;
  document.querySelector(`[data-code="${code}"][data-field="tgt"]`).textContent = tgt.toLocaleString("en-US");
  const tgtVal = asset*w, curVal = qty*spot;
  let adj = 0;
  if(tgtVal>0 && Math.abs(tgtVal-curVal) > tgtVal*0.25){ adj = tgt-qty; }
  const adjEl = document.querySelector(`[data-code="${code}"][data-field="adj"]`);
  if(adj>0) adjEl.innerHTML = `<span class="adj-pos">+${adj.toLocaleString("en-US")} 增持</span>`;
  else if(adj<0) adjEl.innerHTML = `<span class="adj-neg">${adj.toLocaleString("en-US")} 減持</span>`;
  else adjEl.innerHTML = `<span class="adj-zero">持平</span>`;
}
function onCell(code, field, w){
  const v = document.querySelector(`input[data-code="${code}"][data-field="${field}"]`).value;
  if(!userStore[code]) userStore[code] = {};
  userStore[code][field] = v;
  saveStore();
  recalcRow(code, w);
}
function renderTable(tbId, list, w, isA){
  const TB = $(tbId);
  const asset = parseFloat(assetInput.value)||0;
  TB.innerHTML = "";
  if(!isA && !DATA.tsm_on){
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="10" class="empty">B 通道關閉（tsm_long OFF）— 資金 100% 於 A 通道</td>`;
    TB.appendChild(tr);
    return;
  }
  list.forEach(h=>{
    const isBuy = (h.status==="buy" || isFirst);
    const st = isBuy ? ["買進","b-buy"] : ["持有","b-hold"];
    const saved = userStore[h.code] || {};
    const pxV = (saved.px!==undefined && saved.px!=="") ? saved.px : (h.px!==null ? h.px : "");
    const qtyV = (saved.qty!==undefined && saved.qty!=="") ? saved.qty : (isBuy && h.px ? Math.floor(asset*w/h.px) : "");
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td><span class="badge ${st[1]}">${st[0]}</span></td>` +
      `<td><b>${h.code}</b></td><td>${h.name}</td>` +
      `<td class="num">${h.px!==null?h.px:"—"}</td>` +
      `<td class="num">${h.rsi20!==null?h.rsi20:"—"}</td>` +
      `<td class="num"><input class="cell-input" type="number" min="0" step="0.01" data-code="${h.code}" data-field="px" data-spot="${h.px!==null?h.px:''}" value="${pxV}"></td>` +
      `<td class="num"><input class="cell-input" type="number" min="0" step="1" data-code="${h.code}" data-field="qty" value="${qtyV}"></td>` +
      `<td class="num pl" data-code="${h.code}" data-field="pl">—</td>` +
      `<td class="tgt" data-code="${h.code}" data-field="tgt">—</td>` +
      `<td class="num" data-code="${h.code}" data-field="adj">—</td>`;
    TB.appendChild(tr);
    tr.querySelector(`input[data-field="px"]`).addEventListener("input", ()=>onCell(h.code,"px",w));
    tr.querySelector(`input[data-field="qty"]`).addEventListener("input", ()=>onCell(h.code,"qty",w));
    recalcRow(h.code, w);
  });
}
$("h-cnt-a").textContent = DATA.holdings_a.length + "檔・每檔" + (wA()*100).toFixed(1) + "%・A池額度" + (DATA.wa_eff*100).toFixed(0) + "%";
$("h-cnt-b").textContent = DATA.holdings_b.length ? (DATA.holdings_b.length + "檔・每檔" + (wB*100).toFixed(1) + "%・B池額度" + (DATA.wb_eff*100).toFixed(0) + "%") : (DATA.tsm_on ? "0檔・額度併入A通道（動態）" : "0檔・B通道關閉");
function renderAll(){
  renderTable("tb-a", DATA.holdings_a, wA(), true);
  renderTable("tb-b", DATA.holdings_b, wB, false);
}
assetInput.addEventListener("input", renderAll);
renderAll();

// 賣出清單
const S = DATA.sells;
if(S && S.length){
  $("sell-sec").style.display = "block";
  $("s-cnt").textContent = S.length + " 檔";
  const TS = $("tb-sell");
  S.forEach(s=>{
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${s.pool==="B"?"B":"A"}</td><td><b>${s.code}</b></td><td>${s.name}</td><td>掉出前 ${s.pool==="B"?"4":"15"} 名（賣出換股）</td>`;
    TS.appendChild(tr);
  });
}

// ECharts
function initChart(id, opt){
  const el = $(id);
  if(typeof echarts === "undefined"){
    el.innerHTML = '<div class="empty">⚠️ 無法載入 ECharts（需網路連線）；資料表格仍可正常查看。</div>';
    return;
  }
  const ch = echarts.init(el);
  ch.setOption(opt);
  window.addEventListener("resize", ()=>ch.resize());
}
const AXIS = { axisLine:{lineStyle:{color:"#2c3a55"}}, axisLabel:{color:"#8fa0b8"}, splitLine:{lineStyle:{color:"#1f293d"}} };

initChart("chart-nav", {
  backgroundColor:"transparent",
  tooltip:{trigger:"axis", valueFormatter:v=>fmt(v)},
  grid:{left:70,right:20,top:20,bottom:60},
  xAxis:{type:"category", data:P.nav.map(x=>x[0]), ...AXIS, axisLabel:{color:"#8fa0b8", hideOverlap:true}},
  yAxis:{type:"value", ...AXIS, axisLabel:{color:"#8fa0b8", formatter:v=> (v>=10000 ? (v/10000).toFixed(0)+"萬" : v)}},
  dataZoom:[{type:"inside",start:60,end:100},{type:"slider",height:18,bottom:10,start:60,end:100}],
  series:[{
    type:"line", data:P.nav.map(x=>x[1]), showSymbol:false,
    lineStyle:{width:2,color:"#4da3ff"},
    areaStyle:{color:{type:"linear",x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:"rgba(77,163,255,.35)"},{offset:1,color:"rgba(77,163,255,0)"}]}}
  }]
});

initChart("chart-dd", {
  backgroundColor:"transparent",
  tooltip:{trigger:"axis", valueFormatter:v=>v.toFixed(2)+"%"},
  grid:{left:60,right:20,top:15,bottom:30},
  xAxis:{type:"category", data:P.dd.map(x=>x[0]), ...AXIS, axisLabel:{show:false}},
  yAxis:{type:"value", ...AXIS, axisLabel:{color:"#8fa0b8", formatter:v=>v+"%"}},
  series:[{
    type:"line", data:P.dd.map(x=>x[1]), showSymbol:false,
    lineStyle:{width:1.5,color:"#3ddc84"},
    areaStyle:{color:{type:"linear",x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:"rgba(61,220,132,.3)"},{offset:1,color:"rgba(61,220,132,0)"}]}}
  }]
});

initChart("chart-yr", {
  backgroundColor:"transparent",
  tooltip:{trigger:"axis", valueFormatter:v=>v.toFixed(2)+"%"},
  grid:{left:60,right:20,top:15,bottom:30},
  xAxis:{type:"category", data:P.yearly.map(x=>x.year), ...AXIS},
  yAxis:{type:"value", ...AXIS, axisLabel:{color:"#8fa0b8", formatter:v=>v+"%"}},
  series:[{
    type:"bar", data:P.yearly.map(x=>({value:x.ret, itemStyle:{color:x.ret>=0?"#ff4d4f":"#3ddc84"}})),
    barWidth:"55%", label:{show:true, position:"top", color:"#8fa0b8", formatter:p=>p.value.toFixed(1)+"%"}
  }]
});

// ===== 互動回測（自訂年月・本金） =====
function fmtDate(d){ return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); }
const btStart = $("bt-start"), btEnd = $("bt-end"), btCap = $("bt-cap"), btRun = $("bt-run");
btStart.value = P.nav[0][0].slice(0,7);
btEnd.value = P.nav[P.nav.length-1][0].slice(0,7);
let btChart = null;
function runBt(){
  const nav = P.nav.map(x=>[new Date(x[0]), x[1]]);
  const start = new Date(btStart.value + "-01");
  const end = new Date(btEnd.value + "-01"); end.setMonth(end.getMonth()+1);
  const seg = nav.filter(([d])=> d>=start && d<end);
  if(seg.length < 2){ alert("區間資料不足（需至少 2 週），請調整年月"); return; }
  const cap = parseFloat(btCap.value) || 0;
  const first = seg[0][1], last = seg[seg.length-1][1];
  const scale = cap / first;
  const navScaled = seg.map(([d,v])=>[d, v*scale]);
  const finalVal = last * scale;
  const totRet = (last/first - 1) * 100;
  const days = (seg[seg.length-1][0] - seg[0][0]) / 86400000;
  const cagr = days > 30 ? (Math.pow(last/first, 365/days) - 1) * 100 : null;
  let peakV = -Infinity, mdd = 0;
  navScaled.forEach(([d,v])=>{ if(v>peakV) peakV=v; const dd=(v/peakV-1)*100; if(dd<mdd) mdd=dd; });
  $("bt-nav").textContent = fmt(finalVal);
  $("bt-nav-s").textContent = fmtDate(seg[0][0]) + " 至 " + fmtDate(seg[seg.length-1][0]);
  $("bt-cagr").textContent = cagr===null ? "—" : cagr.toFixed(2)+"%";
  $("bt-cagr").className = "v up";
  $("bt-mdd").textContent = mdd.toFixed(2)+"%";
  $("bt-tot").textContent = (totRet>=0?"+":"") + totRet.toFixed(2)+"%";
  $("bt-tot").className = "v " + (totRet>=0?"up":"down");
  const TB = $("bt-yearly").querySelector("tbody"); TB.innerHTML = "";
  const years = [...new Set(navScaled.map(([d])=> d.getFullYear()))];
  years.forEach(y=>{
    const ys = navScaled.filter(([d])=> d.getFullYear()===y);
    if(ys.length < 2) return;
    const yf = ys[0][1], yl = ys[ys.length-1][1];
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${y}</td><td class="num">${((yl/yf-1)*100>=0?"+":"")+((yl/yf-1)*100).toFixed(2)}%</td><td class="num">${fmt(yl)}</td>`;
    TB.appendChild(tr);
  });
  if(typeof echarts !== "undefined"){
    if(!btChart) btChart = echarts.init($("chart-bt"));
    btChart.setOption({
      backgroundColor:"transparent",
      tooltip:{trigger:"axis", valueFormatter:v=>fmt(v)},
      grid:{left:70,right:20,top:20,bottom:40},
      xAxis:{type:"category", data:navScaled.map(x=>x[0]), ...AXIS, axisLabel:{color:"#8fa0b8", hideOverlap:true}},
      yAxis:{type:"value", ...AXIS, axisLabel:{color:"#8fa0b8", formatter:v=> (v>=10000 ? (v/10000).toFixed(0)+"萬" : v)}},
      series:[{type:"line", data:navScaled.map(x=>x[1]), showSymbol:false, lineStyle:{width:2,color:"#f5c542"}, areaStyle:{color:{type:"linear",x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:"rgba(245,197,66,.3)"},{offset:1,color:"rgba(245,197,66,0)"}]}}}]
    });
    window.addEventListener("resize", ()=>btChart.resize());
  }
}
btRun.addEventListener("click", runBt);
runBt();
</script>
</body>
</html>
"""

HTML = HTML.replace("__DATA__", DATA_JSON)
with open("RSI選股器.html", "w", encoding="utf-8") as f:
    f.write(HTML)
with open("index.html", "w", encoding="utf-8") as f:
    f.write(HTML)

print("✅ 完成！")
print(f"   訊號基準日：{sig_day}")
print(f"   A 通道：{len(holdings_a)} 檔｜B 通道：{len(holdings_b)} 檔｜權值開關：{'ON' if tsm_on else 'OFF'}")
print(f"   已生成：RSI選股器.html　→　雙擊即可用瀏覽器開啟")
