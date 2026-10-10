# -*- coding: utf-8 -*-
"""Web 資料產生器（B3 canonical 版）
- 不再自行計算策略：策略唯一來源 = B3 引擎（b3_canonical.py）產出的 output/latest_signals.json
- 本檔職責：①更新本地庫（TWSE/TPEx 官方源，零 FinLab 流量）②讀 canonical + prices → 組 DATA
          ③生成 index.html（內嵌 DATA，file:// 與 GitHub Pages 皆可開）
- daily_update.py 負責每日更新 output/prices.json（持股最新收盤價）
- 用法：python update_data.py
"""
import os, sys, json, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)


def load_token():
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

import pandas as pd
import numpy as np

# [本地化] 盤後先自動更新本地庫（TWSE/TPEx 官方源優先、零 FinLab 流量）。失敗不阻斷。
try:
    from local_db_updater import main as _local_update
    _local_update()
except Exception as _e:
    print(f"[本地庫更新] 跳過（{str(_e)[:80]}）")

# ---------- 1. 讀 canonical（B3 唯一策略結果）----------
sig_path = "output/latest_signals.json"
if not os.path.exists(sig_path):
    print(f"❌ 找不到 {sig_path} — 請先執行 b3_canonical.py 產生策略結果")
    sys.exit(1)
with open(sig_path, encoding="utf-8") as f:
    SIG = json.load(f)

perf = {}
perf_path = "output/perf.json"
if os.path.exists(perf_path):
    with open(perf_path, encoding="utf-8") as f:
        perf = json.load(f)

# 最近 5 週歷史（history/*.json 內嵌，供網頁切換）
history = []
if os.path.isdir("output/history"):
    _hist = sorted([p for p in os.listdir("output/history") if p.endswith(".json")])
    for _p in _hist[-5:]:
        with open(os.path.join("output/history", _p), encoding="utf-8") as f:
            history.append(json.load(f))
    history = sorted(history, key=lambda x: x["rebalance_date"])

# ---------- 2. 每日股價（output/prices.json，daily_update.py 產）----------
prices = {}
px_date = SIG.get("rebal_date")
if os.path.exists("output/prices.json"):
    try:
        with open("output/prices.json", encoding="utf-8") as f:
            _px = json.load(f)
        prices = {str(k): v for k, v in _px.get("prices", {}).items()}
        px_date = _px.get("px_date") or px_date
    except Exception as _e:
        print(f"[prices.json] 讀取失敗（{_e}），沿用調倉價")

# ---------- 3. 組 DATA（結構：Web 只顯示，不重算）----------
holdings_a, holdings_b, sells = [], [], []
for a in SIG.get("actions", []):
    if a["action"] == "SELL":
        sells.append({"code": a["stock_code"], "name": a["stock_name"], "pool": a["channel"],
                      "reason": a["reason"], "shares": a["sell_shares"],
                      "amount": a["estimated_sell_amount"], "price": a["price"],
                      "current_weight": a.get("current_weight", 0), "rank": a.get("rank")})
        continue
    item = {
        "code": a["stock_code"], "name": a["stock_name"], "channel": a["channel"],
        "rank": a["rank"], "action": a["action"], "reason": a["reason"],
        "target_weight": a["target_weight"], "current_weight": a["current_weight"],
        "weight_diff": a["weight_diff"],
        "target_shares": a["target_shares"], "current_shares": a["current_shares"],
        "trade_shares": a["buy_shares"] + a["sell_shares"],
        "buy_shares": a["buy_shares"], "sell_shares": a["sell_shares"],
        "estimated_buy_amount": a["estimated_buy_amount"],
        "estimated_sell_amount": a["estimated_sell_amount"],
        "px": prices.get(a["stock_code"]) or a["price"],
    }
    if a["channel"] == "A":
        holdings_a.append(item)
    else:
        holdings_b.append(item)

DATA = {
    "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    "strategy_version": SIG.get("strategy_version", "B3"),
    "sig_date": SIG.get("sig_date"),
    "rebal_date": SIG.get("rebalance_date"),
    "px_date": px_date,
    "tsm_on": SIG.get("tsm_on", True),
    "wa_eff": SIG.get("wa_eff"), "wb_eff": SIG.get("wb_eff"),
    "counts": SIG.get("counts", {}),
    "buy_total": SIG.get("buy_total", 0), "sell_total": SIG.get("sell_total", 0),
    "cash": SIG.get("cash", 0), "buffer": SIG.get("buffer", 0),
    "portfolio_value": SIG.get("portfolio_value", 0),
    "warnings": SIG.get("warnings", []),
    "holdings_a": holdings_a, "holdings_b": holdings_b, "sells": sells,
    "history": history,
    "perf": perf,
}

# ---------- 4. 生成 HTML ----------
DATA_JSON = json.dumps(DATA, ensure_ascii=False)

HTML = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>天穹紅蓮三重脈衝時空追擊者 B3（動態A·雙通道 60/40）</title>
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
  h1{font-size:22px; letter-spacing:1px;}
  h1 small{color:var(--dim); font-size:13px; font-weight:normal; margin-left:8px;}
  .meta{color:var(--dim); font-size:13px; text-align:right; line-height:1.7;}
  .cards{display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:16px;}
  .card{background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px;}
  .card .k{color:var(--dim); font-size:12px; margin-bottom:6px;}
  .card .v{font-size:22px; font-weight:700;}
  .card .s{color:var(--dim); font-size:11px; margin-top:4px;}
  .up{color:var(--up);} .down{color:var(--down);}
  section{background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:16px;}
  section h2{font-size:16px; margin-bottom:12px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;}
  section h2 .tag{font-size:11px; background:var(--card2); border:1px solid var(--line); padding:2px 8px; border-radius:20px; color:var(--dim); font-weight:normal;}
  .capbar{display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap:wrap;}
  .capbar input{background:var(--card2); border:1px solid var(--line); color:var(--txt); padding:9px 12px; border-radius:8px; font-size:16px; width:200px; font-variant-numeric:tabular-nums;}
  .capbar .cap-info{color:var(--dim); font-size:13px;}
  .capbar .cap-info b{color:var(--txt);}
  button{background:var(--accent); color:#0f1420; border:none; padding:8px 18px; border-radius:8px; font-size:14px; font-weight:700; cursor:pointer;}
  button:hover{filter:brightness(1.1);}
  button.ghost{background:var(--card2); color:var(--dim); border:1px solid var(--line);}
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
  .badge{display:inline-block; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600; white-space:nowrap;}
  .b-buy{background:rgba(255,77,79,.15); color:var(--up); border:1px solid rgba(255,77,79,.4);}
  .b-hold{background:rgba(128,140,160,.15); color:#9aa8bf; border:1px solid rgba(128,140,160,.35);}
  .b-switch{background:rgba(255,179,0,.14); color:#ffb300; border:1px solid rgba(255,179,0,.45);}
  .b-sell{background:rgba(61,220,132,.12); color:var(--down); border:1px solid rgba(61,220,132,.4);}
  .b-rebup{background:rgba(245,185,66,.12); color:var(--warn); border:1px solid rgba(245,185,66,.4);}
  .b-rebdn{background:rgba(245,185,66,.12); color:var(--warn); border:1px solid rgba(245,185,66,.4);}
  .chart{width:100%; height:340px;}
  .chart.small{height:200px;}
  .note{color:var(--dim); font-size:12px; margin-top:10px; line-height:1.8;}
  .warnbox{background:rgba(245,185,66,.1); border:1px solid rgba(245,185,66,.4); color:var(--warn); padding:10px 14px; border-radius:10px; font-size:13px; margin-bottom:14px;}
  .switch{display:inline-flex; align-items:center; gap:8px; padding:6px 14px; border-radius:20px; font-size:13px; font-weight:600; margin-bottom:12px;}
  .sw-on{background:rgba(61,220,132,.12); color:var(--down); border:1px solid rgba(61,220,132,.4);}
  .sw-off{background:rgba(245,185,66,.1); color:var(--warn); border:1px solid rgba(245,185,66,.4);}
  .empty{color:var(--dim); padding:14px 0; font-size:13px;}
  footer{color:var(--dim); font-size:11px; text-align:center; padding:16px 0 8px; line-height:1.8;}
  select{background:var(--card2); border:1px solid var(--line); color:var(--txt); padding:8px 12px; border-radius:8px; font-size:14px;}
  .opblock{margin-bottom:12px;}
  .opblock:last-child{margin-bottom:0;}
  .op-title{display:flex; align-items:center; gap:8px; font-size:14px; font-weight:700; padding:8px 12px; border-radius:10px 10px 0 0; border:1px solid var(--line); border-bottom:none;}
  .op-title .cnt{font-size:12px; color:var(--dim); font-weight:600;}
  .op-list{display:flex; flex-direction:column; gap:6px; padding:10px 12px; border:1px solid var(--line); border-radius:0 0 10px 10px; background:var(--card2);}
  .op-row{display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:13.5px; line-height:1.6;}
  .op-row .cd{font-weight:700; min-width:64px;}
  .op-row .nm{min-width:96px;}
  .op-row .amt{font-variant-numeric:tabular-nums; color:var(--dim);}
  .op-title.sell{background:rgba(61,220,132,.08); color:var(--down);}
  .op-title.buy{background:rgba(255,77,79,.08); color:var(--up);}
  .op-title.reb{background:rgba(245,185,66,.08); color:var(--warn);}
  .op-title.hold{background:rgba(128,140,160,.08); color:#9aa8bf;}
  @media (max-width:900px){ .cards{grid-template-columns:repeat(2,1fr);} .capbar input{width:100%;} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🌩️ 天穹紅蓮三重脈衝時空追擊者<small>B3 實盤 Dashboard｜動態A·雙通道 60/40</small></h1>
    <div class="meta">
      策略版本：<b>B3</b>｜最近調倉日：<b id="m-rebal"></b><br>
      股價更新：<b id="m-px"></b>（每日收盤 F5）
    </div>
  </header>

  <div id="warnbox" class="warnbox" style="display:none"></div>
  <div id="switchbox" style="margin-bottom:12px"></div>

  <section>
    <h2>💰 實盤本金（唯一資金基準）</h2>
    <div class="capbar">
      <input type="number" id="cap" min="10000" step="10000" placeholder="請輸入實際本金">
      <button id="cap-apply">套用本金</button>
      <button id="cap-reset" class="ghost">重新設定</button>
      <span class="cap-info">目前本金：<b id="cap-now"></b>｜可用現金（Buffer <span id="cap-bufpct"></span>）：<b id="cap-cash"></b>｜預估投入：<b id="cap-invest"></b></span>
    </div>
    <div class="note">🔸 權重（%）是 B3 策略結果，不隨本金改變；所有金額（目標金額、預估股數、現金、投入）都依此本金即時計算。<br>
    🔸 輸入後按「套用本金」記住（存你瀏覽器，重新整理不會消失）；「重新設定」回預設 100,000。</div>
  </section>

  <div class="cards" id="cards"></div>

  <section>
    <h2>📋 本週我要做什麼 <span class="tag">操作順序：先賣 → 減碼 → 買 → 加碼 → 持有</span></h2>
    <div id="ops"></div>
    <div class="note">🔸 先賣出／減碼釋出現金，再買入／加碼。下方每檔的「實際買/賣股數」＝目標股數 − 你輸入的目前持股（未輸入視為 0）；目標股數 = 目標金額 ÷ 現價（取整股，行情價，實際成交價可能不同）。</div>
  </section>

  <section>
    <h2>💼 持股與買賣明細 <span class="tag">B3 分檔權重（Rank1-5×1.5 / 6-10×1.0 / 11-15×0.5）</span></h2>
    <div style="overflow-x:auto">
    <table id="holdtbl">
      <thead><tr>
        <th>動作</th><th>代號</th><th>名稱</th><th>通道</th><th>Rank</th>
        <th>目標權重</th><th>目前權重</th><th>差</th>
        <th>目標金額(估)</th><th>目標股數(估)</th><th>目前股數</th><th>調整股數</th><th>現價</th><th>進場價</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    </div>
    <div id="sellnote" class="note"></div>
    <div class="note">🔸 動作說明：BUY=買入｜SELL=賣出｜REBALANCE_BUY=加碼｜REBALANCE_SELL=減碼｜HOLD=不動（買紅賣綠＝台股習慣）。<br>
    🔸 目標金額 = 實盤本金 × 目標權重；目標股數為「預估」（目標金額 ÷ 現價，無條件取整數股，實際成交價可能不同）。<br>
    🔸 <b>目前股數＝你實際持有的股數（可輸入 0）</b>、進場價＝你的成本價（可輸入）；兩者都存你瀏覽器 localStorage，重新整理／下週重開仍在。<br>
    🔸 調整股數 = 目標股數 − 目前股數：正＝買入/加碼、負＝賣出/減碼、零＝不動；SELL 目標為 0 股（若你已無持股則顯示不需操作）。</div>
  </section>

  <section id="histsec" style="display:none">
    <h2>🕘 歷史調倉週 <span class="tag">金額按目前本金估算</span></h2>
    <div class="capbar">
      <label style="color:var(--dim); font-size:13px;">切換查看歷史週（僅顯示，不影響本週操作）：</label>
      <select id="histsel"></select>
    </div>
    <div id="histview"></div>
  </section>

  <section>
    <h2>📈 績效（B3 回測 2015~2026） <span class="tag">回測口徑｜CAGR <span id="p-cagr" class="up"></span> ｜ MDD <span id="p-mdd" class="down"></span></span></h2>
    <div id="chart-nav" class="chart"></div>
    <div id="chart-dd" class="chart small"></div>
    <div class="note">回測基準：初始本金 100,000、2015-01 ~ 2026-10-02，最終資產 <span id="p-nav"></span>。此為歷史績效，不構成投資建議。</div>
  </section>

  <footer>
    資料由 B3 引擎（b3_canonical.py）計算，Web / TG / Replay 讀同一份 canonical 結果；本頁只顯示與依本金估算，不重算策略。<br>
    本頁僅供資訊展示，不構成投資建議。
  </footer>
</div>

<script>
const DATA = __DATA_JSON__;

const $ = (id) => document.getElementById(id);
const F = (n, d) => (n==null || isNaN(n)) ? "—" : Number(n).toLocaleString("zh-TW", {minimumFractionDigits: d||0, maximumFractionDigits: d||0});
const P = (n, d) => (n==null || isNaN(n)) ? "—" : (n*100).toFixed(d||1) + "%";
const M = (n) => (n==null || isNaN(n)) ? "—" : "NT$" + Number(n).toLocaleString("zh-TW", {maximumFractionDigits: 0});

const ACT_LABEL = {BUY:"🟢 買進", SELL:"🔴 賣出", REBALANCE_BUY:"🟡 加碼", REBALANCE_SELL:"🟡 減碼", HOLD:"⚪ 持有"};
const ACT_CLS = {BUY:"b-buy", SELL:"b-sell", REBALANCE_BUY:"b-rebup", REBALANCE_SELL:"b-rebdn", HOLD:"b-hold"};
const ORDER = ["SELL", "REBALANCE_SELL", "BUY", "REBALANCE_BUY", "HOLD"];

/* ================= 唯一資金基準 ================= */
let USER_CAPITAL = parseFloat(localStorage.getItem("b3_user_capital") || "100000") || 100000;

const AMT = (w) => USER_CAPITAL * (w || 0);
const EST_SH = (w, px) => (px > 0) ? Math.floor(AMT(w) / px) : 0;

/* ================= 排序（B3 權重不變，僅排列） ================= */
function cmp(a, b) { return a < b ? -1 : (a > b ? 1 : 0); }
function sortList(rows, k1, d1, k2, d2) {
  return rows.slice().sort((x, y) => {
    let c = cmp(x[k1], y[k1]) * d1;
    if (c) return c;
    c = cmp(x[k2], y[k2]) * d2;
    if (c) return c;
    return String(x.code || "").localeCompare(String(y.code || ""));
  });
}
const SORTER = {
  "BUY":            (r) => sortList(r, "target_weight", -1, "rank", 1),
  "SELL":           (r) => sortList(r, "current_weight", -1, "rank", 1),
  "REBALANCE_BUY":  (r) => sortList(r, "weight_diff", -1, "rank", 1),
  "REBALANCE_SELL": (r) => sortList(r, "weight_diff", 1, "rank", 1).sort((x, y) => Math.abs(y.weight_diff||0) - Math.abs(x.weight_diff||0) || cmp(x.rank, y.rank) || String(x.code).localeCompare(String(y.code))),
  "HOLD":           (r) => sortList(r, "target_weight", -1, "rank", 1),
};

/* ================= meta / 警告 / 開關 ================= */
$("m-rebal").textContent = DATA.rebal_date ? DATA.rebal_date + "（訊號基準 " + DATA.sig_date + "）" : "—";
$("m-px").textContent = DATA.px_date || "—";

const warns = DATA.warnings || [];
if (warns.length) {
  const w = $("warnbox");
  w.style.display = "block";
  w.innerHTML = warns.map(x => "⚠️ " + x).join("<br>");
}

const sw = $("switchbox");
sw.innerHTML = DATA.tsm_on
  ? '<span class="switch sw-on"><span class="dot" style="background:var(--down)"></span>大盤多頭 ON｜A ' + P(DATA.wa_eff,0) + ' / B ' + P(DATA.wb_eff,0) + '</span>'
  : '<span class="switch sw-off"><span class="dot" style="background:var(--warn)"></span>大盤轉弱 OFF｜A 吃 ' + P(DATA.wa_eff,0) + '</span>';

/* ================= 本金輸入 ================= */
function persistCap() {
  const v = parseFloat($("cap").value);
  if (v && v >= 10000) { USER_CAPITAL = v; localStorage.setItem("b3_user_capital", String(v)); }
  $("cap").value = USER_CAPITAL;
  renderAll();
}
$("cap").addEventListener("input", (e) => {
  const v = parseFloat(e.target.value);
  if (v && v >= 10000) { USER_CAPITAL = v; renderAll(); }
});
$("cap-apply").addEventListener("click", persistCap);
$("cap-reset").addEventListener("click", () => {
  localStorage.removeItem("b3_user_capital");
  USER_CAPITAL = 100000;
  $("cap").value = 100000;
  renderAll();
});

/* ================= 輸入記錄（進場價 / 目前股數） ================= */
function saveEntry(code, key, val) {
  const m = JSON.parse(localStorage.getItem("tw_entries") || "{}");
  if (!m[code]) m[code] = {};
  m[code][key] = val;
  localStorage.setItem("tw_entries", JSON.stringify(m));
}
function loadEntry(code, key, def) {
  try {
    const m = JSON.parse(localStorage.getItem("tw_entries") || "{}");
    return m[code] && m[code][key] != null ? m[code][key] : def;
  } catch(e) { return def; }
}

/* ================= 資料彙整 ================= */
function allHolds() {
  const a = (DATA.holdings_a||[]).map(x => Object.assign({}, x, {channel:"A"}));
  const b = (DATA.holdings_b||[]).map(x => Object.assign({}, x, {channel:"B"}));
  return a.concat(b);
}
function groupByAction(rows) {
  const g = {BUY:[], SELL:[], REBALANCE_BUY:[], REBALANCE_SELL:[], HOLD:[]};
  rows.forEach(r => { if (g[r.action]) g[r.action].push(r); });
  return g;
}

/* ================= 頂部卡片（金額全依本金） ================= */
function renderCards() {
  const c = DATA.counts || {};
  const bufAmt = USER_CAPITAL * (DATA.buffer || 0);
  const investAmt = USER_CAPITAL - bufAmt;
  const cards = [
    ["實盤總資產", M(USER_CAPITAL), "= 你輸入的本金", ""],
    ["可用現金", M(bufAmt), "Buffer " + P(DATA.buffer, 1) + " × 本金", ""],
    ["預估投入", M(investAmt), "總資產 − 現金", ""],
    ["CAGR(回測)", P(DATA.perf.cagr, 2), "B3 2015~2026", "up"],
    ["MDD(回測)", P(DATA.perf.mdd, 2), "最大回撤", "down"],
    ["本週買進", (c.BUY||0) + " 檔", "🟢 BUY", "up"],
    ["本週賣出", (c.SELL||0) + " 檔", "🔴 SELL", "down"],
    ["加碼/減碼", (c.REBALANCE_BUY||0) + "/" + (c.REBALANCE_SELL||0), "🟡 REBALANCE", ""],
    ["持有不動", (c.HOLD||0) + " 檔", "⚪ HOLD", ""],
  ];
  $("cards").innerHTML = cards.map(x =>
    '<div class="card"><div class="k">' + x[0] + '</div><div class="v ' + x[3] + '">' + x[1] + '</div><div class="s">' + x[2] + '</div></div>'
  ).join("");
  $("cap-now").textContent = M(USER_CAPITAL);
  $("cap-bufpct").textContent = P(DATA.buffer, 1);
  $("cap-cash").textContent = M(bufAmt);
  $("cap-invest").textContent = M(investAmt);
}

/* ================= 實際持股計算（使用者輸入 → 目標 → 調整） ================= */
const HOLD_ROWS = allHolds().concat((DATA.sells||[]).map(s => ({
  code: s.code, name: s.name, channel: s.pool || "A", rank: s.rank, action: "SELL",
  target_weight: 0, current_weight: s.current_weight || 0, weight_diff: 0,
  target_shares: 0, current_shares: s.shares || 0, buy_shares: 0, sell_shares: s.shares || 0,
  px: s.price, price: s.price
})));
const ROW_MAP = {};
HOLD_ROWS.forEach(r => { ROW_MAP[r.code] = r; });

function curShares(code) { return parseInt(loadEntry(code, "sh", 0)) || 0; }
function entryPxOf(h) {
  const e = parseFloat(loadEntry(h.code, "px", h.px || h.price));
  return (e && e > 0) ? e : (h.px || h.price || 0);
}
function tgtSharesOf(h) {
  if (h.action === "SELL") return 0;
  return EST_SH(h.target_weight || 0, entryPxOf(h));
}
function actText(h, curSh, tgtSh) {
  const adj = tgtSh - curSh;
  if (h.action === "SELL") return curSh > 0 ? "賣出 " + F(curSh,0) + " 股" : "無持股，不需操作";
  if (adj > 0) return "買入/加碼 " + F(adj,0) + " 股";
  if (adj < 0) return "賣出/減碼 " + F(-adj,0) + " 股";
  return "不動";
}
function opLine(h) {
  const curSh = curShares(h.code);
  const tgtSh = tgtSharesOf(h);
  return "目前 " + F(curSh,0) + " 股｜目標 " + F(tgtSh,0) + " 股 → " + actText(h, curSh, tgtSh);
}
function updRow(el) {
  const tr = el.closest("tr");
  const code = tr.children[1].textContent.trim();
  saveEntry(code, el.dataset.k, el.value);
  recalcRow(tr, code);
  renderOps();
}
function recalcRow(tr, code) {
  const h = ROW_MAP[code];
  if (!h) return;
  const curSh = curShares(code);
  const tgtSh = tgtSharesOf(h);
  const adj = tgtSh - curSh;
  const cells = tr.children;
  cells[8].textContent  = M(h.action === "SELL" ? 0 : AMT(h.target_weight || 0));
  cells[9].textContent  = F(tgtSh, 0);
  cells[11].className   = "num " + (adj > 0 ? "adj-pos" : (adj < 0 ? "adj-neg" : "adj-zero"));
  cells[11].textContent = actText(h, curSh, tgtSh);
}

/* ================= 本週操作（四區塊＋排序＋實際股數） ================= */
function renderOps() {
  const rows = allHolds();
  const g = groupByAction(rows);
  const sells = (DATA.sells||[]).slice();
  sells.sort((x, y) => (y.current_weight||0) - (x.current_weight||0) || cmp(x.rank, y.rank) || String(x.code).localeCompare(String(y.code)));

  const blocks = [];
  if (sells.length) blocks.push({key:"SELL", title:"🔴 先賣出", rows: sells.map(s => ({
    code: s.code, name: s.name, rank: s.rank, px: s.price,
    weight: s.current_weight || 0, reason: s.reason, cls:"sell",
    line: s.name + "｜Rank " + (s.rank||"—") + "｜" + opLine(s) + "｜原因 " + (s.reason||"—")
  }))});

  const rebSell = SORTER["REBALANCE_SELL"](g.REBALANCE_SELL||[]);
  if (rebSell.length) blocks.push({key:"REBALANCE_SELL", title:"🟡 再減碼", rows: rebSell.map(r => ({
    code: r.code, name: r.name, rank: r.rank, px: r.px,
    weight: Math.abs(r.weight_diff||0), cls:"reb",
    line: r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(r.target_weight,2) + "｜" + opLine(r)
  }))});

  const buys = SORTER["BUY"](g.BUY||[]);
  if (buys.length) blocks.push({key:"BUY", title:"🟢 再買入", rows: buys.map(r => ({
    code: r.code, name: r.name, rank: r.rank, px: r.px,
    weight: r.target_weight || 0, cls:"buy",
    line: r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(r.target_weight,2) + "｜" + opLine(r)
  }))});

  const rebBuy = SORTER["REBALANCE_BUY"](g.REBALANCE_BUY||[]);
  if (rebBuy.length) blocks.push({key:"REBALANCE_BUY", title:"🟡 再加碼", rows: rebBuy.map(r => ({
    code: r.code, name: r.name, rank: r.rank, px: r.px,
    weight: r.weight_diff || 0, cls:"reb",
    line: r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(r.target_weight,2) + "｜" + opLine(r)
  }))});

  const holds = SORTER["HOLD"](g.HOLD||[]);
  if (holds.length) blocks.push({key:"HOLD", title:"⚪ 持有（不用動）", rows: holds.map(r => ({
    code: r.code, name: r.name, rank: r.rank, px: r.px,
    weight: r.target_weight || 0, cls:"hold",
    line: r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(r.target_weight,2) + "｜" + opLine(r)
  }))});

  const box = $("ops");
  if (!blocks.length) { box.innerHTML = '<div class="empty">本週無任何買賣動作</div>'; return; }
  box.innerHTML = blocks.map(b =>
    '<div class="opblock"><div class="op-title ' + b.cls + '">' + b.title + ' <span class="cnt">' + b.rows.length + ' 檔</span></div>' +
    '<div class="op-list">' + b.rows.map(r =>
      '<div class="op-row"><span class="badge ' + r.cls + '">' + r.code + '</span><span>' + r.line + '</span></div>'
    ).join("") + '</div></div>'
  ).join("");
}

/* ================= 持股明細（可輸入目前股數 / 進場價，自動算實際調整） ================= */
function renderHoldings() {
  const tbody = document.querySelector("#holdtbl tbody");
  const rows = HOLD_ROWS;
  const g = groupByAction(rows);
  const ordered = []
    .concat(SORTER["SELL"](g.SELL||[]))
    .concat(SORTER["REBALANCE_SELL"](g.REBALANCE_SELL||[]))
    .concat(SORTER["BUY"](g.BUY||[]))
    .concat(SORTER["REBALANCE_BUY"](g.REBALANCE_BUY||[]))
    .concat(SORTER["HOLD"](g.HOLD||[]));
  if (!ordered.length) { tbody.innerHTML = '<tr><td colspan="14" class="empty">本週無持股</td></tr>'; return; }
  tbody.innerHTML = ordered.map(h => {
    const code = h.code;
    const curSh = curShares(code);
    const entryPx = entryPxOf(h);
    const pxNow = h.px || h.price || entryPx;
    const tgtVal = (h.action === "SELL") ? 0 : AMT(h.target_weight || 0);
    const tgtSh = tgtSharesOf(h);
    const adj = tgtSh - curSh;
    const adjCls = adj > 0 ? "adj-pos" : (adj < 0 ? "adj-neg" : "adj-zero");
    const adjTxt = actText(h, curSh, tgtSh);
    const tgtCls = (h.action === "SELL") ? "num tgt" : "num";
    return '<tr>' +
      '<td><span class="badge ' + ACT_CLS[h.action] + '">' + ACT_LABEL[h.action] + '</span></td>' +
      '<td>' + code + '</td><td>' + h.name + '</td>' +
      '<td>' + h.channel + '</td><td>' + (h.rank || "—") + '</td>' +
      '<td class="num">' + P(h.target_weight, 2) + '</td>' +
      '<td class="num">' + P(h.current_weight, 2) + '</td>' +
      '<td class="num tgt">' + P(h.weight_diff, 2) + '</td>' +
      '<td class="num">' + M(tgtVal) + '</td>' +
      '<td class="' + tgtCls + '">' + F(tgtSh, 0) + '</td>' +
      '<td><input class="cell-input" data-k="sh" type="number" min="0" step="1" placeholder="0" value="' + curSh + '" oninput="updRow(this)"></td>' +
      '<td class="num ' + adjCls + '">' + adjTxt + '</td>' +
      '<td class="num">' + F(pxNow, 2) + ' <span style="color:var(--dim);font-size:11px">行情</span></td>' +
      '<td><input class="cell-input" data-k="px" type="number" min="0" step="0.01" value="' + entryPx + '" oninput="updRow(this)"></td>' +
    '</tr>';
  }).join("");
}

function renderSellNote() {
  $("sellnote").innerHTML = (DATA.sells||[]).length
    ? "🔴 本週賣出 " + (DATA.sells||[]).length + " 檔：" + (DATA.sells||[]).map(s => s.name + "（" + (s.reason||"") + "）").join("、")
    : "本週無賣出。";
}

/* ================= 績效圖（回測口徑，不隨本金） ================= */
function renderCharts() {
  const navData = (DATA.perf.nav||[]).map(x => [x[0], x[1]]);
  const ddData = (DATA.perf.dd||[]).map(x => [x[0], x[1]]);
  const navChart = echarts.init($("chart-nav"));
  navChart.setOption({
    backgroundColor:"transparent", grid:{left:70,right:20,top:20,bottom:40},
    tooltip:{trigger:"axis", valueFormatter:v => "$" + Number(v).toLocaleString()},
    xAxis:{type:"time", axisLine:{lineStyle:{color:"#2c3a55"}}, axisLabel:{color:"#8fa0b8"}},
    yAxis:{type:"value", splitLine:{lineStyle:{color:"#1f293d"}}, axisLabel:{color:"#8fa0b8", formatter:v => "$"+(v/10000)+"萬"}},
    series:[{type:"line", data:navData, showSymbol:false, lineStyle:{color:"#4da3ff", width:2}, areaStyle:{color:"rgba(77,163,255,.12)"}}]
  });
  const ddChart = echarts.init($("chart-dd"));
  ddChart.setOption({
    backgroundColor:"transparent", grid:{left:70,right:20,top:20,bottom:40},
    tooltip:{trigger:"axis", valueFormatter:v => v.toFixed(2)+"%"},
    xAxis:{type:"time", axisLine:{lineStyle:{color:"#2c3a55"}}, axisLabel:{color:"#8fa0b8"}},
    yAxis:{type:"value", splitLine:{lineStyle:{color:"#1f293d"}}, axisLabel:{color:"#8fa0b8", formatter:v => v+"%"}},
    series:[{type:"line", data:ddData, showSymbol:false, lineStyle:{color:"#3ddc84", width:2}}]
  });
  $("p-cagr").textContent = P(DATA.perf.cagr, 2);
  $("p-mdd").textContent = P(DATA.perf.mdd, 2);
  $("p-nav").textContent = M(DATA.perf.final_nav);
}

/* ================= 歷史週（金額按目前本金估算） ================= */
function renderHist() {
  if (!DATA.history || !DATA.history.length) return;
  $("histsec").style.display = "block";
  const sel = $("histsel");
  DATA.history.forEach((h, i) => {
    const opt = document.createElement("option");
    opt.value = i; opt.textContent = h.rebalance_date;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => {
    const h = DATA.history[parseInt(sel.value)];
    if (!h) return;
    const g = groupByAction(h.actions || []);
    const rows = []
      .concat(SORTER["SELL"](g.SELL||[]))
      .concat(SORTER["REBALANCE_SELL"](g.REBALANCE_SELL||[]))
      .concat(SORTER["BUY"](g.BUY||[]))
      .concat(SORTER["REBALANCE_BUY"](g.REBALANCE_BUY||[]))
      .concat(SORTER["HOLD"](g.HOLD||[]))
      .map(a => {
        const w = a.action === "REBALANCE_SELL" ? Math.abs(a.weight_diff||0) : (a.action === "BUY" || a.action === "REBALANCE_BUY" || a.action === "SELL" ? (a.action === "SELL" ? a.current_weight||0 : a.target_weight||0) : a.target_weight||0);
        return '<div class="action-row"><span class="tag ' + ACT_CLS[a.action] + '">' + ACT_LABEL[a.action] + '</span><span>' +
          a.stock_name + '｜目標權重 ' + P(a.target_weight,2) + '｜按目前本金估算 ' + M(AMT(w)) + '</span></div>';
      }).join("");
    $("histview").innerHTML = '<div class="note">調倉日：' + h.rebalance_date + '｜訊號基準：' + h.sig_date + '｜' +
      'BUY ' + (h.counts.BUY||0) + ' / SELL ' + (h.counts.SELL||0) + ' / REBAL ' + ((h.counts.REBALANCE_BUY||0)+(h.counts.REBALANCE_SELL||0)) + ' / HOLD ' + (h.counts.HOLD||0) + '</div>' +
      (rows || '<div class="empty">該週無明細</div>');
  });
}

/* ================= 全部渲染 ================= */
function renderAll() {
  renderCards();
  renderOps();
  renderHoldings();
}
$("cap").value = USER_CAPITAL;
renderAll();
renderSellNote();
renderCharts();
renderHist();
</script>
</body>
</html>
"""

HTML = HTML.replace("__DATA_JSON__", DATA_JSON)

# 統一僅輸出 index.html（GitHub Pages 唯一入口）；RSI選股器.html 為舊名冗餘檔，不再生成
for _name in ("index.html",):
    with open(_name, "w", encoding="utf-8") as f:
        f.write(HTML)
    print(f"[OK] 已生成 {_name}（{len(HTML):,} bytes | rebal {DATA.get('rebal_date')} | px {DATA.get('px_date')} | counts {DATA.get('counts')}）")
print("完成。")
