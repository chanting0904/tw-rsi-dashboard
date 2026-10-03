# -*- coding: utf-8 -*-
"""Web 資料產生器（B3 canonical 版）
- 不再自行計算策略：策略唯一來源 = B3 引擎（b3_canonical.py）產出的 output/latest_signals.json
- 本檔職責：①更新本地庫（TWSE/TPEx 官方源，零 FinLab 流量）②讀 canonical + prices → 組 DATA
          ③生成 index.html / RSI選股器.html（內嵌 DATA，file:// 與 GitHub Pages 皆可開）
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
                      "amount": a["estimated_sell_amount"], "price": a["price"]})
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
  .b-switch{background:rgba(255,179,0,.14); color:#ffb300; border:1px solid rgba(255,179,0,.45);}
  .b-sell{background:rgba(61,220,132,.12); color:var(--down); border:1px solid rgba(61,220,132,.4);}
  .b-rebup{background:rgba(255,77,79,.12); color:var(--up); border:1px solid rgba(255,77,79,.35);}
  .b-rebdn{background:rgba(61,220,132,.12); color:var(--down); border:1px solid rgba(61,220,132,.35);}
  .chart{width:100%; height:340px;}
  .chart.small{height:200px;}
  .note{color:var(--dim); font-size:12px; margin-top:10px; line-height:1.8;}
  .warnbox{background:rgba(245,185,66,.1); border:1px solid rgba(245,185,66,.4); color:var(--warn); padding:10px 14px; border-radius:10px; font-size:13px; margin-bottom:14px;}
  .switch{display:inline-flex; align-items:center; gap:8px; padding:6px 14px; border-radius:20px; font-size:13px; font-weight:600; margin-bottom:12px;}
  .sw-on{background:rgba(61,220,132,.12); color:var(--down); border:1px solid rgba(61,220,132,.4);}
  .sw-off{background:rgba(245,185,66,.1); color:var(--warn); border:1px solid rgba(245,185,66,.4);}
  .empty{color:var(--dim); padding:14px 0; font-size:13px;}
  footer{color:var(--dim); font-size:11px; text-align:center; padding:16px 0 8px; line-height:1.8;}
  button{background:var(--accent); color:#0f1420; border:none; padding:8px 20px; border-radius:8px; font-size:14px; font-weight:700; cursor:pointer;}
  button:hover{filter:brightness(1.1);}
  select{background:var(--card2); border:1px solid var(--line); color:var(--txt); padding:8px 12px; border-radius:8px; font-size:14px;}
  .action-list{display:flex; flex-direction:column; gap:8px; margin-bottom:8px;}
  .action-row{display:flex; align-items:center; gap:8px; padding:8px 12px; border-radius:10px; background:var(--card2); border:1px solid var(--line); font-size:14px; flex-wrap:wrap;}
  .action-row .tag{min-width:110px;}
  @media (max-width:900px){ .cards{grid-template-columns:repeat(2,1fr);} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🌩️ 天穹紅蓮三重脈衝時空追擊者<small>B3｜動態A·雙通道 60/40</small></h1>
    <div class="meta">
      持股買賣推薦：<b id="m-rebal"></b>（每週最後交易日盤後更新一次）<br>
      股價更新：<b id="m-px"></b>（每日收盤 F5，持股買賣不變）
    </div>
  </header>

  <div id="warnbox" class="warnbox" style="display:none"></div>
  <div id="switchbox" style="margin-bottom:12px"></div>

  <div class="cards" id="cards"></div>

  <section id="actionsec">
    <h2>📋 本週我要做什麼 <span class="tag">先賣 → 減碼 → 買 → 加碼</span></h2>
    <div id="actionlist"></div>
  </section>

  <section>
    <h2>💼 持股與買賣明細 <span class="tag">B3 分檔權重（Rank1-5×1.5 / 6-10×1.0 / 11-15×0.5）</span></h2>
    <div class="assetbar">
      <label>我的總資產（NT$）</label>
      <input type="number" id="asset" value="100000" min="10000" step="10000">
      <label style="color:var(--dim)">建議股數會依你的資產縮放（權重不變）</label>
    </div>
    <div style="overflow-x:auto">
    <table id="holdtbl">
      <thead><tr>
        <th>動作</th><th>代號</th><th>名稱</th><th>通道</th><th>Rank</th>
        <th>目標權重</th><th>目前權重</th><th>差</th>
        <th>目標股數</th><th>目前股數</th><th>買/賣股數</th><th>現價</th>
        <th>進場價</th><th>調整股數</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    </div>
    <div id="sellnote" class="note"></div>
    <div class="note">🔸 動作說明：BUY=買入｜SELL=賣出｜REBALANCE_BUY=加碼｜REBALANCE_SELL=減碼｜HOLD=不動。<br>
    🔸 目標股數 = 我的資產 × 目標權重 ÷ 現價（你輸入資產後自動縮放）；調整股數 = 目標股數 − 目前股數（負=減持、正=增持）。<br>
    🔸 進場價與目前股數存在你自己瀏覽器（localStorage），下週重開仍會記得；要改直接輸入即可。</div>
  </section>

  <section>
    <h2>📈 績效（B3 回測 2015~2026） <span class="tag">CAGR <span id="p-cagr" class="up"></span> ｜ MDD <span id="p-mdd" class="down"></span></span></h2>
    <div id="chart-nav" class="chart"></div>
    <div id="chart-dd" class="chart small"></div>
  </section>

  <section id="histsec" style="display:none">
    <h2>🕘 歷史調倉週</h2>
    <div class="assetbar">
      <label>切換查看歷史週（僅顯示，不影響本週操作）：</label>
      <select id="histsel"></select>
    </div>
    <div id="histview"></div>
  </section>

  <footer>
    資料由 B3 引擎（b3_canonical.py）計算，Web / TG / Replay 讀同一份 canonical 結果。<br>
    本頁僅供資訊展示，不構成投資建議。
  </footer>
</div>

<script>
const DATA = __DATA_JSON__;

const $ = (id) => document.getElementById(id);
const F = (n, d) => (n==null || isNaN(n)) ? "—" : Number(n).toLocaleString("zh-TW", {minimumFractionDigits: d||0, maximumFractionDigits: d||0});
const P = (n, d) => (n==null || isNaN(n)) ? "—" : (n*100).toFixed(d||1) + "%";

const ACT_LABEL = {BUY:"🟢 買進", SELL:"🔴 賣出", REBALANCE_BUY:"🟡 加碼", REBALANCE_SELL:"🟡 減碼", HOLD:"⚪ 持有"};
const ACT_CLS = {BUY:"b-buy", SELL:"b-sell", REBALANCE_BUY:"b-rebup", REBALANCE_SELL:"b-rebdn", HOLD:"b-hold"};

let asset = parseFloat(localStorage.getItem("tw_asset") || "100000") || 100000;
$("asset").value = asset;
$("asset").addEventListener("input", (e) => {
  asset = parseFloat(e.target.value) || 100000;
  localStorage.setItem("tw_asset", asset);
  renderHoldings();
});

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

// ---------- meta ----------
$("m-rebal").textContent = DATA.rebal_date ? DATA.rebal_date + "（訊號基準 " + DATA.sig_date + "）" : "—";
$("m-px").textContent = DATA.px_date || "—";

// ---------- 警告 ----------
const warns = DATA.warnings || [];
if (warns.length) {
  const w = $("warnbox");
  w.style.display = "block";
  w.innerHTML = warns.map(x => "⚠️ " + x).join("<br>");
}

// ---------- 大盤開關 ----------
const sw = $("switchbox");
sw.innerHTML = DATA.tsm_on
  ? '<span class="switch sw-on"><span class="dot" style="background:var(--down)"></span>大盤多頭 ON｜A ' + P(DATA.wa_eff,0) + ' / B ' + P(DATA.wb_eff,0) + '</span>'
  : '<span class="switch sw-off"><span class="dot" style="background:var(--warn)"></span>大盤轉弱 OFF｜A 吃 ' + P(DATA.wa_eff,0) + '</span>';

// ---------- 頂部卡片 ----------
const c = DATA.counts || {};
const cards = [
  ["CAGR", P(DATA.perf.cagr, 2), "B3 回測 2015~2026", "up"],
  ["最大回撤", P(DATA.perf.mdd, 2), "回測", "down"],
  ["本週買進", (c.BUY||0) + " 檔", "🟢 BUY", "up"],
  ["本週賣出", (c.SELL||0) + " 檔", "🔴 SELL", "down"],
  ["加碼/減碼", (c.REBALANCE_BUY||0) + "/" + (c.REBALANCE_SELL||0), "🟡 REBALANCE", ""],
  ["持有不動", (c.HOLD||0) + " 檔", "⚪ HOLD", ""],
  ["總資產(回測)", "$" + F(DATA.portfolio_value, 0), "B3 引擎口徑", ""],
  ["可用現金(回測)", "$" + F(DATA.cash, 0), "Buffer " + P(DATA.buffer, 1), ""],
];
$("cards").innerHTML = cards.map(x =>
  '<div class="card"><div class="k">' + x[0] + '</div><div class="v ' + x[3] + '">' + x[1] + '</div><div class="s">' + x[2] + '</div></div>'
).join("");

// ---------- 本週我要做什麼 ----------
function renderActions() {
  const acts = [];
  (DATA.sells||[]).forEach(s => acts.push({key:"SELL", row:s}));
  (DATA.holdings_a||[]).forEach(a => acts.push({key:a.action, row:a}));
  (DATA.holdings_b||[]).forEach(a => acts.push({key:a.action, row:a}));
  const order = ["SELL","REBALANCE_SELL","BUY","REBALANCE_BUY","HOLD"];
  acts.sort((x,y) => order.indexOf(x.key) - order.indexOf(y.key) || (x.row.code||"").localeCompare(y.row.code||""));
  const box = $("actionlist");
  if (!acts.length) { box.innerHTML = '<div class="empty">本週無任何買賣動作</div>'; return; }
  box.innerHTML = acts.map(a => {
    const r = a.row;
    let txt;
    if (a.key === "SELL") txt = r.name + "｜原因：" + (r.reason||"—") + "｜賣出 " + F(r.shares||r.sell_shares,0) + " 股 約 $" + F(r.amount||r.estimated_sell_amount,0);
    else if (a.key === "BUY") txt = r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(r.target_weight,2) + "｜買入 " + F(r.buy_shares,0) + " 股 約 $" + F(r.estimated_buy_amount,0);
    else if (a.key === "REBALANCE_BUY") txt = r.name + "｜目前 " + P(r.current_weight,2) + " → 目標 " + P(r.target_weight,2) + "｜加碼 +" + F(r.buy_shares,0) + " 股";
    else if (a.key === "REBALANCE_SELL") txt = r.name + "｜目前 " + P(r.current_weight,2) + " → 目標 " + P(r.target_weight,2) + "｜減碼 -" + F(r.sell_shares,0) + " 股";
    else txt = r.name + "｜持有（目標 " + P(r.target_weight,2) + "）";
    return '<div class="action-row"><span class="tag ' + ACT_CLS[a.key] + '">' + ACT_LABEL[a.key] + '</span><span>' + txt + '</span></div>';
  }).join("");
}
renderActions();

// ---------- 持股明細（含互動試算）----------
function allHolds() {
  const a = (DATA.holdings_a||[]).map(x => Object.assign({}, x, {channel:"A"}));
  const b = (DATA.holdings_b||[]).map(x => Object.assign({}, x, {channel:"B"}));
  return a.concat(b);
}

function renderHoldings() {
  const tbody = document.querySelector("#holdtbl tbody");
  const rows = allHolds();
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="14" class="empty">本週無持股</td></tr>'; return; }
  tbody.innerHTML = rows.map(h => {
    const code = h.code;
    const entryPx = parseFloat(loadEntry(code, "px", h.px || h.price)) || h.px || h.price;
    const curSh = parseInt(loadEntry(code, "sh", 0)) || 0;
    const pxNow = h.px || h.price || entryPx;
    const tgtVal = asset * (h.target_weight || 0);
    const tgtSh = pxNow > 0 ? Math.floor(tgtVal / pxNow) : 0;
    const adj = tgtSh - curSh;
    const adjCls = adj > 0 ? "adj-pos" : (adj < 0 ? "adj-neg" : "adj-zero");
    const adjTxt = adj > 0 ? "增持 +" + F(adj,0) : (adj < 0 ? "減持 " + F(adj,0) : "不動");
    return '<tr>' +
      '<td><span class="badge ' + ACT_CLS[h.action] + '">' + ACT_LABEL[h.action] + '</span></td>' +
      '<td>' + code + '</td><td>' + h.name + '</td>' +
      '<td>' + h.channel + '</td><td>' + (h.rank || "—") + '</td>' +
      '<td class="num">' + P(h.target_weight, 2) + '</td>' +
      '<td class="num">' + P(h.current_weight, 2) + '</td>' +
      '<td class="num ' + (h.weight_diff>0?"up":(h.weight_diff<0?"down":"tgt")) + '">' + (h.weight_diff>0?"+":"") + P(h.weight_diff, 2) + '</td>' +
      '<td class="num">' + F(tgtSh, 0) + '</td>' +
      '<td class="num">' + F(curSh, 0) + '</td>' +
      '<td class="num ' + ((h.buy_shares||0)>0?"up":(h.sell_shares||0)>0?"down":"tgt") + '">' + ((h.buy_shares||0)>0?"+"+F(h.buy_shares,0):(h.sell_shares||0)>0?"-"+F(h.sell_shares,0):"—") + '</td>' +
      '<td class="num">' + F(pxNow, 2) + '</td>' +
      '<td><input class="cell-input" data-k="px" type="number" value="' + entryPx + '" onchange="rowIn(this)"></td>' +
      '<td><input class="cell-input" data-k="sh" type="number" value="' + curSh + '" onchange="rowIn(this)"></td>' +
      '<td class="num ' + adjCls + '">' + adjTxt + '</td>' +
    '</tr>';
  }).join("");
}
function rowIn(el) {
  const tr = el.closest("tr");
  const code = tr.children[1].textContent.trim();
  saveEntry(code, el.dataset.k, el.value);
  renderHoldings();
}
renderHoldings();

$("sellnote").innerHTML = (DATA.sells||[]).length
  ? "🔴 本週賣出 " + (DATA.sells||[]).length + " 檔：" + (DATA.sells||[]).map(s => s.name + "（" + (s.reason||"") + "）").join("、")
  : "本週無賣出。";

// ---------- 績效圖 ----------
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

// ---------- 歷史週 ----------
if (DATA.history && DATA.history.length) {
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
    const rows = (h.actions||[]).map(a => {
      const amt = (a.estimated_buy_amount||0) - (a.estimated_sell_amount||0);
      return '<div class="action-row"><span class="tag ' + ACT_CLS[a.action] + '">' + ACT_LABEL[a.action] + '</span><span>' +
        a.stock_name + '｜目標權重 ' + P(a.target_weight,2) + '｜' +
        ((a.buy_shares||0)>0 ? "買 " + F(a.buy_shares,0) + " 股" : (a.sell_shares||0)>0 ? "賣 " + F(a.sell_shares,0) + " 股" : "不動") +
        '｜約 $' + F(Math.abs(amt),0) + '</span></div>';
    }).join("");
    $("histview").innerHTML = '<div class="note">調倉日：' + h.rebalance_date + '｜訊號基準：' + h.sig_date + '｜' +
      'BUY ' + (h.counts.BUY||0) + ' / SELL ' + (h.counts.SELL||0) + ' / REBAL ' + ((h.counts.REBALANCE_BUY||0)+(h.counts.REBALANCE_SELL||0)) + ' / HOLD ' + (h.counts.HOLD||0) +
      '｜現金 ' + F(h.cash,0) + '</div>' + (rows || '<div class="empty">該週無明細</div>');
  });
}
</script>
</body>
</html>
"""

HTML = HTML.replace("__DATA_JSON__", DATA_JSON)

for _name in ("index.html", "RSI選股器.html"):
    with open(_name, "w", encoding="utf-8") as f:
        f.write(HTML)
    print(f"[OK] 已生成 {_name}（{len(HTML):,} bytes | rebal {DATA.get('rebal_date')} | px {DATA.get('px_date')} | counts {DATA.get('counts')}）")
print("完成。")
