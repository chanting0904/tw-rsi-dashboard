# -*- coding: utf-8 -*-
"""Web 資料產生器（V5 canonical 版）
- 不再自行計算策略：策略唯一來源 = V5 引擎（v5_canonical.py）產出的 output/latest_signals.json
- 本檔職責：①更新本地庫（TWSE/TPEx 官方源，零 FinLab 流量）②讀 canonical + prices → 組 DATA
          ③生成 index.html（內嵌 DATA，file:// 與 GitHub Pages 皆可開）
- daily_update.py 負責每日更新 output/prices.json（持股最新收盤價）
- 用法：python update_data.py
"""
import os, sys, json, datetime
from zoneinfo import ZoneInfo
from publish_gate import load_holidays, last_trading_day_of_week, fingerprint
from v5_engine.config import StrategyConfig
from version import RELEASE_VERSION, STRATEGY_VERSION
from v5_engine.jsonio import validation_matches
from v5_engine.recommendations import hidden_zero_asset_codes, public_warnings
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


# [本地化] 盤後先自動更新本地庫（TWSE/TPEx 官方源優先、零 FinLab 流量）。失敗不阻斷。
if os.environ.get('V5_SKIP_LOCAL_UPDATE') != '1':
    try:
        from local_db_updater import main as _local_update
        _local_update()
    except Exception as _e:
        print(f"[本地庫更新] 跳過（{str(_e)[:80]}）")

# ---------- 1. 讀 canonical（V5 唯一策略結果）----------
sig_path = "output/latest_signals.json"
if not os.path.exists(sig_path):
    print(f"❌ 找不到 {sig_path} — 請先執行 v5_canonical.py 產生策略結果")
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
px_date = SIG.get("price_basis_date") or SIG.get("rebalance_date")
if os.path.exists("output/prices.json"):
    try:
        with open("output/prices.json", encoding="utf-8") as f:
            _px = json.load(f)
        prices = {str(k): v for k, v in _px.get("prices", {}).items()}
        px_date = _px.get("px_date") or px_date
    except Exception as _e:
        print(f"[prices.json] 讀取失敗（{_e}），沿用調倉價")

# ---------- 3. 組 DATA（結構：Web 只顯示，不重算）----------
holdings_a, holdings_b, sells, restricted_holdings = [], [], [], []
_hidden_zero_assets = hidden_zero_asset_codes(SIG)
for a in SIG.get("actions", []):
    if str(a["stock_code"]) in _hidden_zero_assets:
        continue
    if a["action"] == "SELL":
        sells.append({"code": a["stock_code"], "name": a["stock_name"], "pool": a["channel"],
                      "reason": a["reason"], "shares": a["sell_shares"],
                      "amount": a["estimated_sell_amount"], "price": a["price"],
                      "current_weight": a.get("current_weight", 0), "rank": a.get("rank")})
        continue
    quote_date = a.get('price_date') or SIG.get('price_basis_date', SIG.get('rebalance_date'))
    minimum_quote_date=max(a.get('price_valid_from') or '',quote_date or '')
    use_latest_quote = a['stock_code'] in prices and px_date >= minimum_quote_date and a.get('allocation_eligible', True)
    item = {
        "code": a["stock_code"], "name": a["stock_name"], "channel": a["channel"],
        "rank": a["rank"], "action": a["action"], "reason": a["reason"],
        "target_weight": a["target_weight"], "current_weight": a["current_weight"],
        "weight_diff": a["weight_diff"],
        "target_shares": a["target_shares"], "current_shares": a["current_shares"],
        "buy_shares": a["buy_shares"], "sell_shares": a["sell_shares"],
        "estimated_buy_amount": a["estimated_buy_amount"],
        "estimated_sell_amount": a["estimated_sell_amount"],
        "px": prices.get(a["stock_code"]) if use_latest_quote else a["price"],
        "px_date": px_date if use_latest_quote else quote_date,
        "price_kind": 'CLOSE' if use_latest_quote else a.get('price_kind','CLOSE'),
        "price_valid_from":minimum_quote_date,
        "post_weight": a.get('post_weight', a['target_weight']),
        "post_shares": a.get('post_shares', a['target_shares']),
        "recommendation_note": a.get('recommendation_note', ''),
        "allocation_eligible": a.get('allocation_eligible', True),
        "trading_status": a.get('trading_status', 'TRADABLE'),
    }
    if not item['allocation_eligible']:
        if item['post_shares'] > 0:
            restricted_holdings.append(item)
        continue
    holdings_a.append(item)

for asset in SIG.get('restricted_assets', []):
    if str(asset['code']) in _hidden_zero_assets:
        continue
    if any(row['code']==asset['code'] for row in restricted_holdings):
        continue
    restricted_holdings.append({'code':asset['code'],'name':asset.get('name',asset['code']),
        'post_shares':asset['shares'],'post_weight':asset['book_value']/SIG['portfolio_value'],
        'px':asset.get('last_quote'),'px_date':asset.get('last_quote_date'),
        'recommendation_note':'歷史受限持股；保守估值 '+str(round(asset['book_value'],2))+' 元，不列入下單或可用現金。'})

week_last = last_trading_day_of_week(datetime.datetime.now(ZoneInfo("Asia/Taipei")).date(), load_holidays())
def read_optional(path):
    try:
        with open(path, encoding='utf-8') as source:
            return json.load(source)
    except (OSError, ValueError):
        return {}

_calendar = read_optional('holidays.json')
_validation = read_optional('output/validation_log.json')
from weekly_status import sync_ready_status
_weekly_status = sync_ready_status(SIG, _validation, perf)
from v5_engine.order_quotes import build_order_quotes
_order_quotes=build_order_quotes(SIG,prices,px_date,read_optional('output/security_status.json'))
_previous_capital_signal = next((h for h in reversed(history) if h.get('rebalance_date','') < SIG.get('rebalance_date','')), None)
DATA = {
    "capital_signal": SIG,
    "previous_capital_signal": _previous_capital_signal,
    "canonical_hash": fingerprint(SIG),
    "order_quotes": _order_quotes,
    "fee_model": SIG.get("fee_model", {}),
    "share_delivery_check": SIG.get("share_delivery_check", {}),
    "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    "expected_rebal_date": week_last.isoformat() if week_last else None,
    "calendar_years": list(_calendar),
    "holidays": sorted({day for days in _calendar.values() for day in days}),
    "weekly_status": _weekly_status,
    "sig_validated": validation_matches(SIG,_validation,perf) and _validation.get('verdict') == 'PASS' and _validation.get('strategy_version') == STRATEGY_VERSION and _validation.get('rebalance_date') == SIG.get('rebalance_date') and _validation.get('release_version') == SIG.get('release_version') == RELEASE_VERSION,
    "signal_mode": SIG.get("signal_mode", "CLOSE_SIMULATION"),
    "price_basis_date": SIG.get("price_basis_date") or SIG.get("rebalance_date"),
    "strategy_version": SIG.get("strategy_version", STRATEGY_VERSION),
    "release_version": SIG.get("release_version"),
    "review_only": os.environ.get("V5_REVIEW_MODE") == "1",
    "selection_rules": SIG.get("selection_rules", {}),
    "receivables": SIG.get("receivables", 0),
    "buy_fee": SIG.get("buy_fee", StrategyConfig().buy_fee),
    "minimum_commission": SIG.get("minimum_commission", StrategyConfig().minimum_commission),
    "pending_assets": SIG.get("pending_assets", []),
    "sig_date": SIG.get("sig_date"),
    "rebal_date": SIG.get("rebalance_date"),
    "sig_generated_at": SIG.get("generated_at"),
    "sig_commit_sha": SIG.get("commit_sha"),
    "px_date": px_date,
    "counts": {action:sum(row['action'] == action for row in holdings_a + holdings_b) + (len(sells) if action == 'SELL' else 0)
               for action in ['BUY', 'SELL', 'REBALANCE_BUY', 'REBALANCE_SELL', 'HOLD']},
    "buy_total": SIG.get("buy_total", 0), "sell_total": SIG.get("sell_total", 0),
    "cash": SIG.get("cash", 0), "buffer": SIG.get("buffer", 0),
    "portfolio_value": SIG.get("portfolio_value", 0),
    "warnings": [warning.replace("該通道權重", "配置權重") for warning in public_warnings(SIG) if not warning.startswith(("通道配置", "B 通道僅"))] + [f"{a['stock_code']} 沒有最新行情，沿用訊號基準價格" for a in SIG.get("actions", []) if a["action"] != "SELL" and a.get('allocation_eligible', True) and a["stock_code"] not in prices],
    "holdings_a": holdings_a, "holdings_b": holdings_b, "sells": sells,
    "restricted_holdings": restricted_holdings,
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
<title>V5.0.0｜天穹紅蓮三重脈衝時空追擊者 V5（RSI 單一選股策略）</title>
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
  .tgt{text-align:right; font-variant-numeric:tabular-nums; color:var(--dim);}
  .badge{display:inline-block; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600; white-space:nowrap;}
  .b-buy{background:rgba(255,77,79,.15); color:var(--up); border:1px solid rgba(255,77,79,.4);}
  .b-hold{background:rgba(128,140,160,.15); color:#9aa8bf; border:1px solid rgba(128,140,160,.35);}
  .b-sell{background:rgba(61,220,132,.12); color:var(--down); border:1px solid rgba(61,220,132,.4);}
  .b-rebup{background:rgba(245,185,66,.12); color:var(--warn); border:1px solid rgba(245,185,66,.4);}
  .b-rebdn{background:rgba(245,185,66,.12); color:var(--warn); border:1px solid rgba(245,185,66,.4);}
  .badge-25{background:rgba(255,255,255,.06); color:#ffd76a; border:1px solid rgba(245,185,66,.5); font-size:11px; padding:1px 7px; margin-left:6px;}
  .chart{width:100%; height:340px;}
  .chart.small{height:200px;}
  .note{color:var(--dim); font-size:12px; margin-top:10px; line-height:1.8;}
  .warnbox{background:rgba(245,185,66,.1); border:1px solid rgba(245,185,66,.4); color:var(--warn); padding:10px 14px; border-radius:10px; font-size:13px; margin-bottom:14px;}
  .freshnessbox{background:rgba(255,77,79,.12); border:1px solid rgba(255,77,79,.65); color:#ff8b8d; padding:12px 14px; border-radius:10px; font-size:14px; font-weight:700; margin-bottom:14px; line-height:1.6;}
  .weekly-ready{background:rgba(61,220,132,.10); border-color:var(--down); color:var(--down);}
  .weekly-waiting{background:rgba(245,185,66,.10); border-color:var(--warn); color:var(--warn);}
  .status-detail{display:block; font-weight:400; font-size:13px; margin-top:4px;}
  .empty{color:var(--dim); padding:14px 0; font-size:13px;}
  footer{color:var(--dim); font-size:11px; text-align:center; padding:16px 0 8px; line-height:1.8;}
  select{background:var(--card2); border:1px solid var(--line); color:var(--txt); padding:8px 12px; border-radius:8px; font-size:14px;}
  .opblock{margin-bottom:12px;}
  .opblock:last-child{margin-bottom:0;}
  .op-title{display:flex; align-items:center; gap:8px; font-size:14px; font-weight:700; padding:8px 12px; border-radius:10px 10px 0 0; border:1px solid var(--line); border-bottom:none;}
  .op-title .cnt{font-size:12px; color:var(--dim); font-weight:600;}
  .op-list{display:flex; flex-direction:column; gap:6px; padding:10px 12px; border:1px solid var(--line); border-radius:0 0 10px 10px; background:var(--card2);}
  .op-row{display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:13.5px; line-height:1.6;}
  .op-title.sell{background:rgba(61,220,132,.08); color:var(--down);}
  .op-title.buy{background:rgba(255,77,79,.08); color:var(--up);}
  .op-title.reb{background:rgba(245,185,66,.08); color:var(--warn);}
  .op-title.hold{background:rgba(128,140,160,.08); color:#9aa8bf;}
  .order-input{background:var(--card2);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:9px;font:inherit;max-width:100%;} .order-price{width:105px;background:var(--card2);color:var(--txt);border:1px solid var(--line);border-radius:5px;padding:6px;} #order-holdings{width:100%;min-height:130px;margin:10px 0;} .order-controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0;}
  @media (max-width:900px){ .cards{grid-template-columns:repeat(2,1fr);} .capbar input{width:100%;} }

  #monthly-returns-section{background:radial-gradient(ellipse at 0 0,rgba(92,128,186,.10),transparent 60%),linear-gradient(145deg,#1b2538,#141d2d);border:1px solid #34425b;padding:24px 18px 20px;box-shadow:0 18px 40px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.055);border-radius:18px;}
  #monthly-returns-section h2{font-size:19px;letter-spacing:.6px;margin-bottom:8px;}
  #monthly-returns-section h2 .tag{background:#142032;border-color:#35455e;padding:5px 10px;}
  .monthly-scroll{overflow-x:auto;max-width:100%;padding:2px 2px 12px;scrollbar-color:#40536f #141d2d;scrollbar-width:thin;}
  .monthly-returns{min-width:1100px;border-collapse:separate;border-spacing:7px 10px;table-layout:fixed;text-align:center;font-variant-numeric:tabular-nums;}
  .monthly-returns td,.monthly-returns th{text-align:center;border-bottom:0;padding:13px 3px;}
  .monthly-returns thead th{color:#8497b5;font-size:11px;font-weight:500;padding:8px 0;letter-spacing:.5px;}
  .monthly-returns th:first-child{width:52px;position:sticky;left:0;background:#192437;z-index:2;border-radius:9px;}
  .monthly-returns tbody th{color:#c4d2e6;font-size:13px;font-weight:700;}
  .monthly-returns td{height:55px;border:1px solid transparent;border-radius:10px;font-size:12px;}
  .monthly-returns td.month-tile{--tile-rgb:140,160,190;--tile-strength:.04;color:#c1cce0;background:linear-gradient(155deg,rgba(var(--tile-rgb),var(--tile-strength)),rgba(var(--tile-rgb),.025)),linear-gradient(180deg,#263249,#1a2436);border-color:rgba(var(--tile-rgb),.19);box-shadow:inset 0 1px 0 rgba(255,255,255,.095),inset 0 -2px 0 rgba(0,0,0,.19),0 4px 8px rgba(0,0,0,.20);font-weight:650;transition:transform .16s ease,box-shadow .16s ease;}
  .monthly-returns td.up{color:#ff8e98;}
  .monthly-returns td.down{color:#77e3b7;}
  .monthly-returns tr:hover td.month-tile{background:linear-gradient(155deg,rgba(var(--tile-rgb),var(--tile-strength)),rgba(var(--tile-rgb),.025)),linear-gradient(180deg,#263249,#1a2436);}
  .monthly-returns td.month-empty,.monthly-returns tr:hover td.month-empty{color:#4e607b;background:rgba(10,17,28,.18);border:1px dashed rgba(104,126,161,.14);box-shadow:none;}
  .monthly-returns small{display:block;color:#a6b5cb;font-size:9px;font-weight:400;margin-top:4px;}
  .monthly-returns td.month-current{outline:1px solid #7e9dcc;outline-offset:2px;}
  .monthly-returns th:last-child{width:115px;}
  .monthly-returns td.monthly-annual{font-size:14px;font-weight:750;border-color:rgba(var(--tile-rgb),.36);box-shadow:inset 0 1px 0 rgba(255,255,255,.12),inset 0 -2px 0 rgba(0,0,0,.18),0 4px 12px rgba(0,0,0,.25);}
  @media(hover:hover){.monthly-returns td.month-tile:hover{transform:translateY(-3px);box-shadow:inset 0 1px 0 rgba(255,255,255,.14),0 8px 16px rgba(0,0,0,.30);}}
  @media(prefers-reduced-motion:reduce){.monthly-returns td.month-tile{transition:none;}.monthly-returns td.month-tile:hover{transform:none;}}
  @media(max-width:600px){#monthly-returns-section{padding:18px 10px;}.monthly-returns{border-spacing:6px 9px;}#monthly-returns-section h2{font-size:17px;}}

  /* Compact monthly return cards. */
  #monthly-returns-section{padding:16px 12px 12px;border-radius:14px;}
  #monthly-returns-section h2{font-size:16px;margin-bottom:5px;}
  #monthly-returns-section h2 .tag{padding:3px 8px;font-size:10px;}
  #monthly-returns-section>.note{font-size:11px;line-height:1.6;}
  .monthly-scroll{padding:0 0 6px;}
  .monthly-returns{min-width:820px;border-spacing:4px 5px;}
  .monthly-returns td,.monthly-returns th{padding:7px 2px;}
  .monthly-returns td{height:36px;font-size:11px;border-radius:7px;}
  .monthly-returns thead th{font-size:10px;padding:4px 0;letter-spacing:0;}
  .monthly-returns th:first-child{width:40px;border-radius:7px;}
  .monthly-returns tbody th{font-size:11px;}
  .monthly-returns th:last-child{width:88px;}
  .monthly-returns td.monthly-annual{font-size:12px;}
  .monthly-returns small{font-size:8px;margin-top:1px;}
  .monthly-returns td.month-tile{box-shadow:inset 0 1px 0 rgba(255,255,255,.09),inset 0 -1px 0 rgba(0,0,0,.18),0 2px 4px rgba(0,0,0,.18);}
  .monthly-returns td.month-current{outline-offset:1px;}
  @media(max-width:600px){#monthly-returns-section{padding:12px 8px;}.monthly-returns{border-spacing:3px 4px;}#monthly-returns-section h2{font-size:15px;}}

  /* Shared compact depth, matching the monthly return cards. */
  section{background:radial-gradient(ellipse at 0 0,rgba(92,128,186,.075),transparent 65%),linear-gradient(145deg,#1b2538,#151e2e);border-color:#34425b;border-radius:14px;padding:14px;box-shadow:0 8px 22px rgba(0,0,0,.17),inset 0 1px 0 rgba(255,255,255,.055);}
  section h2{font-size:15px;margin-bottom:10px;}
  .cards{gap:9px;margin-bottom:14px;}
  .card{background:linear-gradient(155deg,#253249,#1a2436);border-color:#36455f;border-radius:10px;padding:12px 14px;box-shadow:inset 0 1px 0 rgba(255,255,255,.075),inset 0 -1px 0 rgba(0,0,0,.18),0 3px 8px rgba(0,0,0,.18);}
  .card .v{font-size:20px;}.card .k{font-size:11px;}.card .s{line-height:1.5;}
  .capbar input,.order-input,.order-price,.order-holdings,.order-controls input,.order-controls textarea,select,textarea.order-input,#order-holdings{background:linear-gradient(180deg,#141d2e,#1a263a);border:1px solid #3a4b67;border-radius:7px;box-shadow:inset 0 2px 4px rgba(0,0,0,.22),0 1px 0 rgba(255,255,255,.035);}
  input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible{outline:2px solid #8fbaff;outline-offset:2px;}
  button{background:linear-gradient(180deg,#72b5ff,#438ee0);border:1px solid #7dbaff;border-radius:7px;padding:8px 14px;box-shadow:inset 0 1px 0 rgba(255,255,255,.28),0 3px 7px rgba(0,0,0,.2);}
  button.ghost{background:linear-gradient(160deg,#2a3750,#1c293e);border-color:#3c4d68;color:#bdcbe0;box-shadow:inset 0 1px 0 rgba(255,255,255,.055),0 2px 5px rgba(0,0,0,.15);}
  .op-title{border-radius:9px 9px 0 0;box-shadow:inset 0 1px 0 rgba(255,255,255,.065);padding:7px 11px;}
  .op-list{background:linear-gradient(150deg,#202d43,#1b2639);border-radius:0 0 9px 9px;box-shadow:inset 0 -1px 0 rgba(0,0,0,.18),0 3px 8px rgba(0,0,0,.13);padding:9px 11px;}
  .badge{border-radius:7px;box-shadow:inset 0 1px 0 rgba(255,255,255,.07),0 2px 4px rgba(0,0,0,.12);}
  section h2 .tag{background:linear-gradient(180deg,#243148,#19263a);border-color:#3a4963;border-radius:7px;box-shadow:inset 0 1px 0 rgba(255,255,255,.04);}
  .warnbox,.freshnessbox{border-radius:10px;box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 3px 9px rgba(0,0,0,.13);}
  section table:not(.monthly-returns) thead{background:rgba(42,57,82,.4);}
  section table:not(.monthly-returns) th{font-size:12px;}
  section table:not(.monthly-returns) td{padding-top:9px;padding-bottom:9px;}
  @media(max-width:600px){body{padding:12px;}section{padding:12px;}.card{padding:10px 11px;}.card .v{font-size:19px;}h1{font-size:19px;}header{gap:8px;}}
__DASHBOARD_CSS__
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🌩️ 天穹紅蓮三重脈衝時空追擊者<small>V5.0.0 模擬 Dashboard｜RSI 單一選股策略</small></h1>
    <div class="meta">
      策略版本：<b>V5.0.0</b>｜最近調倉日：<b id="m-rebal"></b><br>
      股價更新：<b id="m-px"></b>（每日收盤）
    </div>
  </header>
<div class="version-panel"><div><div class="version-title">本週調倉 <span class="version-chip" id="preview-version"></span></div><div class="version-sub"><span class="ready-label" id="preview-ready-label">本週計畫已完成</span> · 核對版本與股數</div></div><div class="version-date">調倉日 <strong id="preview-rebalance"></strong><br>資料基準 <strong id="preview-basis"></strong></div></div>
<div class="version-update" id="preview-update-notice" hidden role="alert"><strong id="preview-update-title"></strong><p id="preview-update-text"></p><button id="preview-update-dismiss" type="button">收起提醒</button></div>


  <div id="warnbox" class="warnbox" style="display:none"></div>
  <div id="freshnessbox" class="freshnessbox" role="status" aria-live="polite"><b id="weekly-title">正在確認本週狀態</b><span id="weekly-detail" class="status-detail"></span></div>


<section class="result-overview"><h2>本次調倉摘要 <small>依你輸入的兩週總資金計算</small></h2><div class="result-metrics"><div class="result-metric buy"><label>買進／加碼</label><strong id="preview-buy-count">—</strong></div><div class="result-metric sell"><label>賣出／減碼</label><strong id="preview-sell-count">—</strong></div><div class="result-metric"><label>調整後股票倉位</label><strong id="preview-exposure">—</strong></div><div class="result-metric cash"><label>預估剩餘資金</label><strong id="preview-cash">—</strong></div></div><p id="preview-result-note" class="result-note">輸入兩週總資金後，查看這次需要買賣的股數。</p></section>
<section class="capital-panel">
    <h2>💰 總資金（持股市值＋現金）</h2>
    <div class="capbar">
      <label for="cap-previous">上週總資金</label><input type="number" id="cap-previous" min="10000" max="1000000000000" step="1" placeholder="手動輸入上週總資金">
      <label for="cap">本週總資金</label><input type="number" id="cap" min="10000" step="10000" placeholder="手動輸入本週總資金">
      <button id="cap-apply">計算本週買賣</button>
      <button id="cap-reset" class="ghost">重新設定</button>
    </div>
    <details class="capital-explanation"><summary>資金怎麼填？調倉規則說明</summary><div class="note">兩週金額都由你手動輸入，均指「股票市值＋現金」，不是累計投入。續抱股原則上不動；退出、新買、100%差距門檻及90～95%倉位修復才調整。不需輸入交割款。回測已採用相同防買回調倉規則；績效以固定初始資金複利計算，不包含你個人的加減資金。<br>下方「買賣清單」才是兩週資金試算結果；展開的模型資料是參考，不是個人下單清單。</div></details>
    <div id="cap-storage-note" class="note" role="status"></div>

  </section>

  <section class="orders-panel"><div class="orders-heading"><h2>本週買賣清單</h2><span>粗體數字＝這次下單的股數</span></div><div id="weekly-capital-error" class="note" role="status">請輸入兩週總資金，再按「計算本週買賣」。</div><div id="weekly-capital-results" hidden><div id="weekly-capital-summary" class="note"></div><div id="weekly-capital-basis" class="note basis-note"></div><div class="orders-actions"><button id="weekly-capital-copy" class="ghost">複製買賣清單</button><span id="weekly-capital-copy-note" class="note"></span></div><div id="weekly-capital-table"></div></div><button id="weekly-capital-clear" class="ghost clear-records">清除理論股數紀錄</button></section>
  <section>
    <details id="order-planner"><summary style="cursor:pointer;font-weight:700">🧾 算這次要買賣幾股／檢查委託資金（選用）</summary>
      <div class="note">每行輸入「股票代號 股數」，例如 2033 100；單位是股，不是張。目前沒有持股請填「空手」。<b>請輸入完整、已可交易的持股；待領配股不要算入。</b>內容不會上傳或寫回模擬持倉。</div>
      <label for="order-holdings">當次持股</label><textarea id="order-holdings" class="order-input" placeholder="2330 10&#10;2317 20"></textarea>
      <div class="order-controls">
        <label for="order-mode">委託價格</label><select id="order-mode"><option value="stress">買進＋10%／賣出－10%估算</option><option value="reference">依頁面基準行情</option></select>
        <button id="order-calc">計算買賣差額</button><button id="order-clear" class="ghost">清除當次資料</button>
      </div>
      <div class="note">目標股數沿用上方總資金試算；下表的委託價可逐檔修改。±10%只是資金壓力試算，<b>不是當日官方漲跌停價，也不保證成交</b>；除權息等情況會改變基準。掛單請以券商顯示的有效價格為準。<br>行情及交易狀態都有各自基準日，委託前請再確認當日狀態。只依上方輸入的總資金核對整份調倉是否超支，不需另填現金。請先完成上方兩週總資金計算；此處只核對同一份目標與實際持股，不另外產生另一組配置。資金檢查以清單全數成交為前提。</div>
      <div id="order-error" class="note" role="alert"></div>
      <div id="order-results" hidden><div id="order-summary" class="note" role="status"></div><div class="order-controls"><button id="order-copy" class="ghost">複製買賣清單</button><span id="order-copy-note" class="note"></span></div><div style="overflow-x:auto"><table id="order-table"></table></div></div>
    </details>
    <div id="delivery-check-note" class="note"></div>
  </section>

  <details id="model-reference"><summary style="cursor:pointer;font-weight:700">📚 策略模型參考（不是本次個人下單清單）</summary>
      <span class="cap-info">試算資金：<b id="cap-now"></b>｜配置後預估現金（<span id="cap-bufpct"></span>）：<b id="cap-cash"></b>｜整股預估投入：<b id="cap-invest"></b></span>
    <div id="selectionnote" class="note"></div>
    <div id="allocationnote" class="note"></div>
    <details id="allocationdetails" class="note"><summary>查看配置差額：哪些股票少了多少</summary><div id="allocationrows" style="overflow-x:auto"></div></details>
  <div class="cards" id="cards"></div>

  <section>
    <h2><span id="ops-title">📋 最近一期模擬清單</span> <span class="tag">操作順序：先賣 → 減碼 → 買 → 加碼 → 持有</span></h2>
    <div id="ops"></div>
    <div id="restrictednote" class="note"></div>
    <details id="pendingbox" class="note" style="display:none"><summary id="pendingsummary"></summary><div id="pendingnote"></div></details>
    <div class="note">🔸 先賣出／減碼釋出現金，再買入／加碼。每檔顯示 V5 模擬調倉後建議權重＋照目前本金算出的「目標股數」（目標金額 ÷ 現價，取整股）；<b>可展開上方當次持股試算，換算這次買賣差額</b>。</div>
  </section>

  <section>
    <h2>💼 持股與買賣明細 <span class="tag">V5 策略係數（入場排名分檔，整股權重依本金調整）</span></h2>
    <div style="overflow-x:auto">
    <table id="holdtbl">
      <thead><tr>
        <th>動作</th><th>代號</th><th>名稱</th><th>Rank</th>
        <th>目標權重</th><th>目前權重</th><th>差</th>
        <th>目標金額</th><th>目標股數</th><th>現價</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    </div>
    <div id="sellnote" class="note"></div>
    <div class="note">🔸 動作說明：BUY=買入｜SELL=賣出｜REBALANCE_BUY=加碼｜REBALANCE_SELL=減碼｜HOLD=不動（買紅賣綠＝台股習慣）。<br>
    🔸 目標金額＝整股股數×顯示價格；目標權重使用扣除模型費用後的資產計算。低於90%時依可買標的補足整股差額，上限95%。<br>
    🔸 <b style="color:var(--warn)">🟡 加碼／減碼（REBALANCE）= 該檔持倉權重偏離目標超過 V5 的一般 100% 差距門檻，或整體倉位需要修復，才需要調</b>。舊持股減少一般加減碼；退出、新股加入、90～95% 倉位修復仍執行。<br>
    🔸 系統不記錄你的實際持股；「目標股數」是你照本金應該持有的量，可展開上方當次持股試算，換算這次買賣差額。</div>
  </section>

  <section id="histsec" style="display:none">
    <h2>🕘 歷史調倉週 <span class="tag">金額按目前本金估算</span></h2>
    <div class="capbar">
      <label style="color:var(--dim); font-size:13px;">切換查看歷史週（僅顯示，不影響本週操作）：</label>
      <select id="histsel"></select>
    </div>
    <div id="histview"></div>
  </section>

  </details>

  <section>
    <h2>📊 V5 歷史績效互動計算器 <span class="tag">策略 V5｜全期 CAGR <span id="p-cagr" class="up"></span>｜全期 MDD <span id="p-mdd" class="down"></span></span></h2>
    <div class="capbar">
      <input type="number" id="pc-cap" min="10000" step="10000" placeholder="投入本金（例：1000000）">
      <span style="color:var(--dim); font-size:13px;">起始</span>
      <select id="pc-y0"></select>
      <span style="color:var(--dim); font-size:13px;">→</span>
      <select id="pc-y1"></select>
      <button id="pc-apply">計算</button>
      <button id="pc-demo" class="ghost">範例：2018 投入 100 萬到 2024</button>
    </div>
    <div class="cards" style="grid-template-columns:repeat(3,1fr);">
      <div class="card"><div class="k">期初本金（投入）</div><div class="v" id="pc-start">—</div><div class="s" id="pc-start-date"></div></div>
      <div class="card"><div class="k">累積報酬率</div><div class="v" id="pc-ret">—</div><div class="s" id="pc-years"></div></div>
      <div class="card"><div class="k">總獲利</div><div class="v" id="pc-profit">—</div><div class="s">期末資產 − 期初本金</div></div>
      <div class="card"><div class="k">期末資產</div><div class="v" id="pc-end">—</div><div class="s" id="pc-end-date"></div></div>
      <div class="card"><div class="k">CAGR（年化報酬）</div><div class="v" id="pc-cagr">—</div><div class="s">NAV 端點年化，非年度報酬相加</div></div>
      <div class="card"><div class="k">區間最大回撤 MDD</div><div class="v down" id="pc-mdd">—</div><div class="s">區間內 NAV 重算</div></div>
    </div>
    <div id="chart-nav" class="chart"></div>
    <div id="chart-dd" class="chart small"></div>
    <div class="note">
      計算方式：直接讀 V5 歷史 NAV，取「起始年年初 NAV」與「結束年年末 NAV」端點相除，不重新跑回測。<span id="pc-range"></span><br>
      回測基準：初始本金 <span id="p-initial"></span>｜已完成期間 <span id="p-period"></span>｜最終資產 <span id="p-nav"></span>。<br>
      <b style="color:var(--warn)">⚠️ 純 V5 歷史回測，不代表未來收益。V5 上線時接續既有 V4 模擬持倉，與2015年起的純 V5 回測分開。</b>
    </div>
  </section>

  <section id="monthly-returns-section">
    <h2>每月盈虧 <span class="tag" id="monthly-returns-period"></span></h2>
    <div class="note" style="margin:0 0 12px">策略回測 · 點月份看最賺／最虧股票｜<span class="up">紅色賺錢</span>・<span class="down">綠色虧錢</span>｜手機可左右滑動</div>
    <div class="monthly-scroll" id="monthly-returns-table" tabindex="0" role="region" aria-label="可左右捲動的月報酬表"></div>
    <div class="note">每月報酬＝月底總資產 ÷ 上月底總資產 − 1；第一個月以回測起始本金計算。包含持股漲跌、股利權益與交易費用。最新月份只計算到標示日期，未有資料以「—」表示。全年報酬以複利計算，不是把每月相加。此為策略模擬，與個人帳戶損益可能不同。</div>
  </section>

  <footer>
    資料由 V5 引擎（v5_canonical.py）計算，Web / TG / Replay 讀同一份 canonical 結果；本頁只顯示與依本金估算，不重算策略。<br>股利先列應收、發放後才可買股；未知付款日持續列應收。配股在除權日取得權益，核實可交易日期後才列可賣股數；畸零權益暫列應收；不含個人股利稅負。<br>
    本頁僅供資訊展示，不構成投資建議。
  </footer>
</div>

<script>
__ORDER_PLANNER_JS__
__ORDER_PLANNER_UI_JS__
const DATA = __DATA_JSON__;
__WEEKLY_STATUS_JS__
__CAPITAL_PREVIEW_JS__
__WEEKLY_CAPITAL_JS__
__WEEKLY_CAPITAL_UI_JS__

const $ = (id) => document.getElementById(id);
const F = (n, d) => (n==null || isNaN(n)) ? "—" : Number(n).toLocaleString("zh-TW", {minimumFractionDigits: d||0, maximumFractionDigits: d||0});
const P = (n, d) => (n==null || isNaN(n)) ? "—" : (n*100).toFixed(d||1) + "%";
const M = (n) => (n==null || isNaN(n)) ? "—" : "NT$" + Number(n).toLocaleString("zh-TW", {maximumFractionDigits: 0});

const ACT_LABEL = {BUY:"🟢 買進", SELL:"🔴 賣出", REBALANCE_BUY:"🟡 加碼", REBALANCE_SELL:"🟡 減碼", HOLD:"⚪ 持有"};
const ACT_CLS = {BUY:"b-buy", SELL:"b-sell", REBALANCE_BUY:"b-rebup", REBALANCE_SELL:"b-rebdn", HOLD:"b-hold"};
const ORDER = ["SELL", "REBALANCE_SELL", "BUY", "REBALANCE_BUY", "HOLD"];

/* ================= 唯一資金基準 ================= */
function capitalStorage(action, value) {
  try {
    if (action === 'getItem') return localStorage.getItem('v4_user_capital');
    if (action === 'setItem') localStorage.setItem('v4_user_capital', value);
    if (action === 'removeItem') localStorage.removeItem('v4_user_capital');
  } catch (_) {
    const note = $('cap-storage-note');
    if (note) note.textContent = '瀏覽器無法記住本金，本次試算仍有效；重新整理後請再確認輸入。';
  }
  return null;
}
let USER_CAPITAL = parseFloat(capitalStorage('getItem') || "100000") || 100000;
if (!Number.isFinite(USER_CAPITAL) || USER_CAPITAL < 10000 || USER_CAPITAL > 1e12) USER_CAPITAL = 100000;
let CAPITAL_PREVIEW = null;
let ORDER_PLANNER_UI = null;
let WEEKLY_CAPITAL_UI = null;
function updateCapitalPreview() {
  CAPITAL_PREVIEW = previewAllocation(allHolds().map(row => ({code:row.code, target_weight:row.target_weight, price:row.px || row.price, channel:row.channel})), USER_CAPITAL, DATA.buy_fee, DATA.minimum_commission);
}

const AMT = (w) => USER_CAPITAL * (w || 0);            // 權重 → 金額

// 目標股數 = 目標金額 ÷ 現價（整股；SELL→0）
function tgtShares(h) {
  if (h.action === "SELL") return 0;
  if (!CAPITAL_PREVIEW) updateCapitalPreview();
  return CAPITAL_PREVIEW.quantities[h.code] || 0;
}
function previewWeight(h) { return h.action === 'SELL' ? 0 : (CAPITAL_PREVIEW?.weights?.[h.code] || 0); }
function tgtAmount(h) { return h.action === "SELL" ? 0 : tgtShares(h) * (h.px || h.price || 0); }
function is25(h) { return (h.action === "REBALANCE_BUY" || h.action === "REBALANCE_SELL") && !(h.reason || "").startsWith("PORTFOLIO_BAND_"); }

/* ================= 排序（V5 權重不變，僅排列） ================= */
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
// BUY：目標權重 ↓、Rank ↑、代號 ↑
// SELL：目前權重 ↓、Rank ↑、代號 ↑
// REBALANCE_BUY：權重差 ↓、Rank ↑、代號 ↑
// REBALANCE_SELL：|權重差| ↓、Rank ↑、代號 ↑
// HOLD：目標權重 ↓、Rank ↑、代號 ↑
const SORTER = {
  "BUY":            (r) => sortList(r, "target_weight", -1, "rank", 1),
  "SELL":           (r) => sortList(r, "current_weight", -1, "rank", 1),
  "REBALANCE_BUY":  (r) => sortList(r, "weight_diff", -1, "rank", 1),
  "REBALANCE_SELL": (r) => sortList(r, "weight_diff", 1, "rank", 1).sort((x, y) => Math.abs(y.weight_diff||0) - Math.abs(x.weight_diff||0) || cmp(x.rank, y.rank) || String(x.code).localeCompare(String(y.code))),
  "HOLD":           (r) => sortList(r, "target_weight", -1, "rank", 1),
};

/* ================= meta / 警告 ================= */
$("m-rebal").textContent = DATA.rebal_date ? DATA.rebal_date + "（訊號基準 " + DATA.sig_date + "）｜版本 " + (DATA.sig_commit_sha || "—") + (DATA.signal_mode === "PRE_CLOSE_PLAN" ? "｜收盤調倉計畫，股數按基準日價格試算" : "｜收盤模擬結果") : "—";
const selectionRules = DATA.selection_rules || {};
$("selectionnote").textContent = selectionRules.a_rank_days ? '選股規則：最近' + selectionRules.a_rank_days + '個已收盤交易日的平均RSI排名；續抱股票符合資格且排名前' + selectionRules.a_retention_rank + '可保留，最多' + selectionRules.a_entry_rank + '檔。' : '';
$("m-px").textContent = DATA.px_date || "—";

if (DATA.review_only) {
  const banner = document.createElement('div');
  banner.className = 'warnbox'; banner.style.display = 'block';
  banner.textContent = '候選研究版，尚未上線。請先確認回測結果，再決定是否採用。';
  document.querySelector('.wrap').prepend(banner);
}
const warns = DATA.warnings || [];
if (warns.length) {
  const w = $("warnbox");
  w.style.display = "block";
  w.innerHTML = warns.map(x => "⚠️ " + x).join("<br>");
}

// Recompute from today's Taipei date, even if this HTML was built last week.
let publicWeeklyStatus = DATA.weekly_status || {};
function renderFreshnessStatus() {
  const result = weeklyReadiness(DATA, taipeiDate(), publicWeeklyStatus);
  if(WEEKLY_CAPITAL_UI)WEEKLY_CAPITAL_UI.validateFreshness();
  const box = $("freshnessbox");
  box.className = 'freshnessbox weekly-' + result.state;
  $("weekly-title").textContent = result.title; if(document.getElementById("preview-ready-label"))document.getElementById("preview-ready-label").textContent=result.title;
  $("weekly-detail").textContent = result.detail;
  $("ops-title").textContent = result.state === 'ready' ? '📋 本週我要做什麼' : '📋 最近一期模擬清單（本週尚未確認）';
  if ($('cards').childElementCount) {
    renderCards();
    renderSellNote();
  }
}
renderFreshnessStatus();
async function refreshWeeklyStatus() {
  renderFreshnessStatus();
  if (!/^https?:$/.test(location.protocol)) return;
  try {
    const response = await fetch('output/weekly_status.json?t=' + Date.now(), {cache: 'no-store', signal: AbortSignal.timeout(5000)});
    if (!response.ok) return;
    const status = await response.json();
    if (status && typeof status === 'object') {
      publicWeeklyStatus = status;
      renderFreshnessStatus();
      if(window.renderRevisionStatus)window.renderRevisionStatus(status);
    }
  } catch (_) { /* Keep the offline calendar/date check when the server is unavailable. */ }
}
refreshWeeklyStatus();
setInterval(refreshWeeklyStatus, 60000);

/* ================= 本金輸入 ================= */
function persistCap() {
  const v = parseFloat($("cap").value);
  if (Number.isFinite(v) && v >= 10000 && v <= 1e12) { $('cap-storage-note').textContent=''; USER_CAPITAL = v; capitalStorage('setItem', String(v)); }
  else { $('cap-storage-note').textContent='請輸入10,000元至1兆元的有效本金；本次未計算。'; return; }
  $("cap").value = USER_CAPITAL;
  renderAll();
}
$("cap").addEventListener("input", (e) => {
  const v = parseFloat(e.target.value);
  if (Number.isFinite(v) && v >= 10000 && v <= 1e12) { if ($('cap-storage-note').textContent.startsWith('請輸入')) $('cap-storage-note').textContent=''; USER_CAPITAL = v; renderAll(); }   // 即時預覽
});
$("cap-apply").addEventListener("click", persistCap);
$("cap-reset").addEventListener("click", () => {
  $('cap-storage-note').textContent='';
  capitalStorage('removeItem');
  USER_CAPITAL = 100000;
  $("cap").value = "";
  renderAll();
});

/* ================= 資料彙整 ================= */
function allHolds() {
  return DATA.holdings_a || [];
}
function groupByAction(rows) {
  const g = {BUY:[], SELL:[], REBALANCE_BUY:[], REBALANCE_SELL:[], HOLD:[]};
  rows.forEach(r => { if (g[r.action]) g[r.action].push(r); });
  return g;
}

// 全部需要顯示的列（含 SELL：目標 0 股）
const HOLD_ROWS = allHolds().concat((DATA.sells||[]).map(s => ({
  code: s.code, name: s.name, channel: s.pool || "A", rank: s.rank, action: "SELL",
  target_weight: 0, current_weight: s.current_weight || 0, weight_diff: 0,
  target_shares: 0, current_shares: s.shares || 0, buy_shares: 0, sell_shares: s.shares || 0,
  px: s.price, price: s.price
})));

/* ================= 頂部卡片（金額全依本金） ================= */
function renderCards() {
  updateCapitalPreview();
  const period = weeklyReadiness(DATA, taipeiDate(), publicWeeklyStatus).state === 'ready' ? '本週' : '最近一期';
  const c = {BUY:0,SELL:(DATA.sells||[]).length,REBALANCE_BUY:0,REBALANCE_SELL:0,HOLD:0};
  allHolds().filter(row => tgtShares(row)>0).forEach(row => { c[row.action] = (c[row.action]||0)+1; });
  const bufAmt = CAPITAL_PREVIEW.cash;
  const investAmt = CAPITAL_PREVIEW.invested;
  const cards = [
    ["資金試算基準", M(USER_CAPITAL), "= 你輸入的資金", ""],
    ["配置後預估現金", M(bufAmt), "依目標整股與模型費用估算", ""],
    ["整股預估投入", M(investAmt), "目標股數 × 顯示價格", ""],
    ["CAGR(回測)", P(DATA.perf.cagr, 2), "截至 " + (DATA.perf.completed_through || DATA.perf.nav?.at(-1)?.[0] || '—'), "up"],
    ["MDD(回測)", P(DATA.perf.mdd, 2), "每日資產估值的最大回撤", "down"],
    [period + "買進", (c.BUY||0) + " 檔", "🟢 BUY", "up"],
    [period + "賣出", (c.SELL||0) + " 檔", "🔴 SELL", "down"],
    ["加碼/減碼", (c.REBALANCE_BUY||0) + "/" + (c.REBALANCE_SELL||0), "🟡 減少加減碼", ""],
    ["持有不動", (c.HOLD||0) + " 檔", "⚪ HOLD", ""],
  ];
  $("cards").innerHTML = cards.map(x =>
    '<div class="card"><div class="k">' + x[0] + '</div><div class="v ' + x[3] + '">' + x[1] + '</div><div class="s">' + x[2] + '</div></div>'
  ).join("");
  $("cap-now").textContent = M(USER_CAPITAL);
  $("cap-bufpct").textContent = P(bufAmt / CAPITAL_PREVIEW.nav, 1);
  $("cap-cash").textContent = M(bufAmt);
  $("cap-invest").textContent = M(investAmt);
  const preview = CAPITAL_PREVIEW;
  $("allocationnote").textContent = '整股前配置目標 ' + P(preview.target_weight_total, 1) + '｜整股後配置 ' + P(preview.exposure, 1) + '｜整組建倉手續費估算 ' + M(preview.fees) + '（最低每檔1元）。這是整組目標配置的試算費用。' +
    ' 相較目標少投入 ' + M(preview.shortfall) + '（占輸入資金 ' + P(preview.shortfall / USER_CAPITAL, 2) + '）。' +
    (preview.below_band_amount > 0 ? ' 距離90%配置下緣約差 ' + M(preview.below_band_amount) + '。' : '') +
    ((preview.exposure < .90 || preview.exposure > .95) ? ' 目前價格與整股限制下無法達到90～95%，差額保留現金。' : '') +
    (preview.unaffordable.length ? ' 目標金額不足一股：' + preview.unaffordable.join('、') + '。' : '') +
    (preview.missing.length ? ' 缺少價格：' + preview.missing.join('、') + '。' : '') +
    ' 已先在可買標的間重分配並依整股修補；剩餘差額保留為現金。';
  renderAllocationDetails(preview);
}

function renderAllocationDetails(preview) {
  const container = $("allocationrows");
  container.replaceChildren();
  const table = document.createElement('table');
  const head = table.createTHead().insertRow();
  for (const title of ['股票', '目標金額', '試算股數', '預估投入', '配置差額', '原因']) {
    const cell = document.createElement('th'); cell.textContent = title; head.appendChild(cell);
  }
  const body = table.createTBody();
  const names = new Map(allHolds().map(row => [String(row.code), row.name]));
  for (const row of preview.details.filter(row => row.shortfall > .005)) {
    const tr = body.insertRow();
    const reason = {missing_price:'缺少可用價格', restricted:'不可交易，移出試算', under_one_share:'不足一股，額度分配其他可買標的', exposure_limit:'保留現金以維持95%倉位上限', cash_limit:'可用資金需預留費用', rounding:'股數取整', rounding_repair:'補足整股差額，維持90～95%配置', redistributed:'承接其他無法配置的額度'}[row.reason] || '—';
    for (const value of [row.code + ' ' + (names.get(row.code) || ''), M(row.target_amount), F(row.quantity, 0), M(row.invested), M(row.shortfall), reason]) {
      tr.insertCell().textContent = value;
    }
  }
  container.appendChild(table);
}

/* ================= 本週操作（四區塊＋排序＋目標股數） ================= */
function renderOps() {
  const rows = allHolds().filter(row => tgtShares(row) > 0);
  const g = groupByAction(rows);
  const sells = (DATA.sells||[]).slice();
  sells.sort((x, y) => (y.current_weight||0) - (x.current_weight||0) || cmp(x.rank, y.rank) || String(x.code).localeCompare(String(y.code)));

  const blocks = [];
  if (sells.length) blocks.push({key:"SELL", title:"🔴 先賣出", rows: sells.map(s => ({
    code: s.code, name: s.name, rank: s.rank, px: s.price,
    weight: s.current_weight || 0, reason: s.reason, cls:"sell",
    line: s.name + "｜Rank " + (s.rank||"—") + "｜目標股數 0（賣出原有股票）｜原因 " + (s.reason||"—")
  }))});

  const rebSell = SORTER["REBALANCE_SELL"](g.REBALANCE_SELL||[]);
  if (rebSell.length) blocks.push({key:"REBALANCE_SELL", title:"🟡 再減碼（權重／總持倉調整）", rows: rebSell.map(r => ({
    code: r.code, name: r.name, rank: r.rank, px: r.px,
    weight: Math.abs(r.weight_diff||0), cls:"reb",
    line: r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(previewWeight(r),2) + "｜目標股數 " + F(tgtShares(r),0) + " 股（照本金 " + M(USER_CAPITAL) + "）"
  }))});

  const buys = SORTER["BUY"](g.BUY||[]);
  if (buys.length) blocks.push({key:"BUY", title:"🟢 再買入", rows: buys.map(r => ({
    code: r.code, name: r.name, rank: r.rank, px: r.px,
    weight: r.target_weight || 0, cls:"buy",
    line: r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(previewWeight(r),2) + "｜目標股數 " + F(tgtShares(r),0) + " 股（照本金 " + M(USER_CAPITAL) + "）"
  }))});

  const rebBuy = SORTER["REBALANCE_BUY"](g.REBALANCE_BUY||[]);
  if (rebBuy.length) blocks.push({key:"REBALANCE_BUY", title:"🟡 再加碼（權重／總持倉調整）", rows: rebBuy.map(r => ({
    code: r.code, name: r.name, rank: r.rank, px: r.px,
    weight: r.weight_diff || 0, cls:"reb",
    line: r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(previewWeight(r),2) + "｜目標股數 " + F(tgtShares(r),0) + " 股（照本金 " + M(USER_CAPITAL) + "）"
  }))});

  const holds = SORTER["HOLD"](g.HOLD||[]);
  if (holds.length) blocks.push({key:"HOLD", title:"⚪ 策略續抱（股數依本金試算）", rows: holds.map(r => ({
    code: r.code, name: r.name, rank: r.rank, px: r.px,
    weight: r.target_weight || 0, cls:"hold",
    line: r.name + "｜Rank " + (r.rank||"—") + "｜目標權重 " + P(previewWeight(r),2) + "｜目標股數 " + F(tgtShares(r),0) + " 股"
  }))});

  blocks.forEach(block => block.rows.forEach(row => {
    const original = rows.find(item => item.code === row.code);
    if (original?.price_kind === 'EX_RIGHTS_REFERENCE') row.line += '｜除權息參考價';
    if (original?.recommendation_note) row.line += '｜⚠️ ' + original.recommendation_note;
  }));
  const box = $("ops");
  if (!blocks.length) { box.innerHTML = '<div class="empty">本週無任何買賣動作</div>'; return; }
  box.innerHTML = blocks.map(b =>
    '<div class="opblock"><div class="op-title ' + b.cls + '">' + b.title + ' <span class="cnt">' + b.rows.length + ' 檔</span></div>' +
    '<div class="op-list">' + b.rows.map(r =>
      '<div class="op-row"><span class="badge ' + r.cls + '">' + r.code + '</span><span>' + r.line + '</span></div>'
    ).join("") + '</div></div>'
  ).join("");
}

/* ================= 持股明細（純目標清單，無輸入） ================= */
function renderHoldings() {
  const tbody = document.querySelector("#holdtbl tbody");
  const rows = HOLD_ROWS;                    // 含 SELL（目標 0 股）
  const g = groupByAction(rows);
  const ordered = []
    .concat(SORTER["SELL"](g.SELL||[]))
    .concat(SORTER["REBALANCE_SELL"](g.REBALANCE_SELL||[]))
    .concat(SORTER["BUY"](g.BUY||[]))
    .concat(SORTER["REBALANCE_BUY"](g.REBALANCE_BUY||[]))
    .concat(SORTER["HOLD"](g.HOLD||[]));
  if (!ordered.length) { tbody.innerHTML = '<tr><td colspan="10" class="empty">本週無持股</td></tr>'; return; }
  tbody.innerHTML = ordered.map(h => {
    const tgtVal = tgtAmount(h);
    const tgtSh = tgtShares(h);
    const pxNow = h.px || h.price || 0;
    const tgtCls = (h.action === "SELL") ? "num tgt" : "num";
    const actBadge = (tgtSh === 0 && h.action !== "SELL" ? "不下單（整股／本金限制）" : ACT_LABEL[h.action]) + (is25(h) ? '<span class="badge-25">100%差距門檻</span>' : ((h.reason || "").startsWith("PORTFOLIO_BAND_") ? '<span class="badge-25">90–95%配置調整</span>' : ""));
    return '<tr>' +
      '<td><span class="badge ' + ACT_CLS[h.action] + '">' + actBadge + '</span></td>' +
      '<td>' + h.code + '</td><td>' + h.name + (h.recommendation_note ? '<div class="note">⚠️ ' + h.recommendation_note + '</div>' : '') + '</td>' +
      '<td>' + (h.rank || "—") + '</td>' +
      '<td class="num">' + P(previewWeight(h), 2) + '</td>' +
      '<td class="num">' + P(h.current_weight, 2) + '</td>' +
      '<td class="num tgt">' + P(previewWeight(h) - (h.current_weight || 0), 2) + '</td>' +
      '<td class="num">' + M(tgtVal) + '</td>' +
      '<td class="' + tgtCls + '">' + F(tgtSh, 0) + '</td>' +
      '<td class="num">' + F(pxNow, 2) + (h.price_kind === 'EX_RIGHTS_REFERENCE' ? ' <span class="tag">除權息參考價</span>' : '') + ' <span style="color:var(--dim);font-size:11px">' + (h.px_date || DATA.price_basis_date || DATA.px_date || "") + '</span></td>' +
    '</tr>';
  }).join("");
}

function renderSellNote() {
  const period = weeklyReadiness(DATA, taipeiDate(), publicWeeklyStatus).state === 'ready' ? '本週' : '最近一期';
  $("sellnote").innerHTML = (DATA.sells||[]).length
    ? "🔴 " + period + "賣出 " + (DATA.sells||[]).length + " 檔：" + (DATA.sells||[]).map(s => s.name + "（" + (s.reason||"") + "）").join("、")
    : period + "無賣出。";
}

function renderRestrictedHoldings() {
  const pending = DATA.pending_assets || [];
  $("pendingbox").style.display = pending.length ? '' : 'none';
  $("pendingsummary").textContent = '模擬待領配股 ' + pending.length + ' 筆：目前不用另外下單（點此查看）';
  $("pendingnote").textContent = pending.length ? '這是策略模擬紀錄，實際配股以券商入帳為準。原有股票照原清單操作，應領配股會保留。新增配股可交易後，由後續調倉處理。明細：' + pending.map(row =>
    row.code + ' ' + row.shares + ' 股｜除權日 ' + row.ex_date + '｜' + (row.tradable_date ? row.tradable_date + ' 起可交易' : '日期待確認')
  ).join('；') : '';
  const rows = DATA.restricted_holdings || [];
  const box = $("restrictednote");
  box.innerHTML = rows.length ? '<b>歷史受限資產（不列入下單或本金試算）</b><br>' + rows.map(row =>
    row.code + ' ' + row.name + '｜' + row.recommendation_note + '｜最後報價 ' + F(row.px, 2) + '（' + (row.px_date || '日期未知') + '）'
  ).join('<br>') + '<br>已下櫃部位採保守零估值；原股數保留紀錄，未假設可以賣出換成現金。' : '';
}

/* ================= 績效圖（回測口徑，不隨本金） ================= */
function renderMonthlyReturns() {
  const box=document.getElementById('monthly-returns-table');
  const perf=DATA.perf||{}, nav=perf.nav||[];
  if(!nav.length){box.textContent='尚無月報酬資料。';return;}
  const months=new Map(), years=new Map();
  let basis=Number(perf.start_capital);
  if(!(basis>0)){box.textContent='缺少有效期初本金，暫不顯示月報酬。';return;}
  for(const [date,value] of nav){
    if(!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(date)||!(Number(value)>0)){box.textContent='資產資料不完整，暫不顯示月報酬。';return;}
    const month=date.slice(0,7),year=date.slice(0,4);
    if(!months.has(month))months.set(month,{basis,end:Number(value),date});
    else Object.assign(months.get(month),{end:Number(value),date});
    if(!years.has(year))years.set(year,{basis,end:Number(value)});
    else years.get(year).end=Number(value);
    basis=Number(value);
  }
  const last=nav[nav.length-1][0];
  document.getElementById('monthly-returns-period').textContent=(perf.release_version||'策略')+'｜截至 '+last;
  const table=document.createElement('table');table.className='monthly-returns';
  table.setAttribute('aria-label','策略回測每月報酬率百分比');
  const head=table.createTHead().insertRow();
  for(const name of ['年份',...Array.from({length:12},(_,i)=>(i+1)+'月'),'全年／今年累計']){
    const th=document.createElement('th');th.scope='col';th.textContent=name;head.appendChild(th);
  }
  const body=table.createTBody();
  function paint(cell,ret){
    const value=ret*100,rounded=Number(value.toFixed(2));
    cell.textContent=(rounded>0?'+':'')+rounded.toFixed(2)+'%';
    cell.classList.add('month-tile');
    if(rounded){cell.classList.add(rounded>0?'up':'down');cell.style.setProperty('--tile-rgb',rounded>0?'255,99,112':'65,211,159');cell.style.setProperty('--tile-strength',(.09+Math.min(Math.abs(value)/20,1)*.19).toFixed(3));}
  }
  for(const [year,annual] of [...years].reverse()){
    const row=body.insertRow(),label=document.createElement('th');label.scope='row';label.textContent=year;row.appendChild(label);
    for(let n=1;n<=12;n++){
      const key=year+'-'+String(n).padStart(2,'0'),m=months.get(key),cell=row.insertCell();
      if(!m){cell.textContent='—';cell.title='尚無資料';cell.classList.add('month-empty');continue;}
      paint(cell,m.end/m.basis-1);cell.title=key+'｜截止 '+m.date;
      if(key===last.slice(0,7)){cell.classList.add('month-current');const mark=document.createElement('small');mark.textContent='截至 '+last.slice(5);cell.appendChild(mark);}
      if(DATA.perf?.monthly_contributions?.months?.[key]){
      const trigger=document.createElement('button');trigger.type='button';trigger.className='month-details-trigger';
      trigger.setAttribute('aria-label',year+'年'+n+'月，報酬'+Number(((m.end/m.basis-1)*100).toFixed(2)).toFixed(2)+'%，查看最賺錢與最虧錢股票');
      while(cell.firstChild)trigger.appendChild(cell.firstChild);cell.appendChild(trigger);
      trigger.addEventListener('click',()=>openMonthlyDetails(key));
      }
    }
    const cell=row.insertCell();paint(cell,annual.end/annual.basis-1);cell.classList.add('monthly-annual');
  }
  box.replaceChildren(table);
}

function renderCharts() {
  $("p-cagr").textContent = P(DATA.perf.cagr, 2);
  $("p-mdd").textContent = P(DATA.perf.mdd, 2);
  $("p-nav").textContent = M(DATA.perf.final_nav);
  $("p-initial").textContent = F(DATA.perf.start_capital || 100000, 0);
  $("p-period").textContent = (DATA.perf.nav?.[0]?.[0] || '—') + ' ～ ' + (DATA.perf.completed_through || DATA.perf.nav?.at(-1)?.[0] || '—');
  function unavailable() {
    for (const id of ['chart-nav','chart-dd']) $(id).textContent='圖表暫時無法載入；買賣清單與股數試算仍可使用。';
  }
  if (typeof echarts === 'undefined') { unavailable(); return; }
  try {
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

  } catch (_) { unavailable(); }
}

/* ================= 歷史週（金額按目前本金估算） ================= */
function renderHist() {
  if (!DATA.history || !DATA.history.length) return;
  $("histsec").style.display = "block";
  const sel = $("histsel");
  DATA.history.forEach((h, i) => {
    const opt = document.createElement("option");
    opt.value = i; opt.textContent = h.rebalance_date + '｜' + (h.release_version || h.strategy_version || '歷史版本');
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => {
    const h = DATA.history[parseInt(sel.value)];
    if (!h) return;
    const historyPreview = previewAllocation((h.actions || []).filter(a => a.action !== 'SELL').map(a => ({
      code:a.stock_code,target_weight:a.target_weight,price:a.price,channel:a.channel,allocation_eligible:a.allocation_eligible
    })), USER_CAPITAL, h.buy_fee ?? DATA.buy_fee, h.minimum_commission ?? DATA.minimum_commission);
    const g = groupByAction((h.actions || []).filter(a => a.action === 'SELL' || a.allocation_eligible !== false));
    const rows = []
      .concat(SORTER["SELL"](g.SELL||[]))
      .concat(SORTER["REBALANCE_SELL"](g.REBALANCE_SELL||[]))
      .concat(SORTER["BUY"](g.BUY||[]))
      .concat(SORTER["REBALANCE_BUY"](g.REBALANCE_BUY||[]))
      .concat(SORTER["HOLD"](g.HOLD||[]))
      .map(a => {
        const w = a.action === "REBALANCE_SELL" ? Math.abs(a.weight_diff||0) : (a.action === "BUY" || a.action === "REBALANCE_BUY" || a.action === "SELL" ? (a.action === "SELL" ? a.current_weight||0 : a.target_weight||0) : a.target_weight||0);
        return '<div class="action-row"><span class="tag ' + ACT_CLS[a.action] + '">' + ACT_LABEL[a.action] + '</span><span>' +
          a.stock_name + '｜整股試算權重 ' + P(historyPreview.weights[a.stock_code] || 0,2) + '｜目標股數 ' + F(historyPreview.quantities[a.stock_code] || 0,0) + ' 股</span></div>';
      }).join("");
    $("histview").innerHTML = '<div class="note">調倉日：' + h.rebalance_date + '｜訊號基準：' + h.sig_date + '｜' +
      '版本：' + (h.release_version || h.strategy_version || '歷史版本') + '｜BUY ' + (h.counts.BUY||0) + ' / SELL ' + (h.counts.SELL||0) + ' / REBAL ' + ((h.counts.REBALANCE_BUY||0)+(h.counts.REBALANCE_SELL||0)) + ' / HOLD ' + (h.counts.HOLD||0) + '</div>' +
      (rows || '<div class="empty">該週無明細</div>') +
      ((h.actions || []).some(a => a.action !== 'SELL' && a.allocation_eligible === false) ? '<div class="note">另有歷史受限資產，未列入可下單明細。</div>' : '');
  });
  sel.value = DATA.history.length - 1;
  sel.dispatchEvent(new Event("change"));
}

/* ================= V5 歷史績效互動計算器（只讀 DATA.perf NAV，不重跑回測） ================= */
function initPerfCalc() {
  const YEARS = [...new Set(((DATA.perf && DATA.perf.nav) || []).map(row => Number(row[0].slice(0,4))))].sort();
  const y0 = $("pc-y0"), y1 = $("pc-y1");
  YEARS.forEach(y => { y0.appendChild(new Option(y, y)); y1.appendChild(new Option(y, y)); });
  $("pc-apply").addEventListener("click", runPerfCalc);
  $("pc-demo").addEventListener("click", () => { $("pc-cap").value = 1000000; y0.value = 2018; y1.value = 2024; runPerfCalc(); });
  ["change","input"].forEach(ev => {
    $("pc-cap").addEventListener(ev, runPerfCalc);
    y0.addEventListener(ev, runPerfCalc);
    y1.addEventListener(ev, runPerfCalc);
  });
  y0.value = 2018; y1.value = 2024; $("pc-cap").value = 1000000;
  runPerfCalc();
}

function runPerfCalc() {
  const nav = (DATA.perf && DATA.perf.nav) || [];
  const capital = Math.max(0, parseFloat($("pc-cap").value)) || 0;
  const y0 = parseInt($("pc-y0").value), y1 = parseInt($("pc-y1").value);
  if (!nav.length || !y0 || !y1 || y1 < y0) { $("pc-range").textContent = "請選擇有效的起始／結束年份（起始 ≤ 結束）"; return; }
  let i0 = 0;
  for (let i = 0; i < nav.length; i++) { if (nav[i][0] <= y0 + "-01-01") i0 = i; else break; }
  let i1 = nav.length - 1;
  for (let i = 0; i < nav.length; i++) { if (nav[i][0] <= y1 + "-12-31") i1 = i; else break; }
  const s = nav[i0][1], e = nav[i1][1];
  const days = (new Date(nav[i1][0]) - new Date(nav[i0][0])) / 86400000;
  const years = days / 365.25;
  const growth = e / s;
  const ret = growth - 1;
  const cagr = years > 0 ? Math.pow(growth, 1 / years) - 1 : 0;
  let peak = -Infinity, mdd = 0;
  for (let i = i0; i <= i1; i++) { if (nav[i][1] > peak) peak = nav[i][1]; const d = nav[i][1] / peak - 1; if (d < mdd) mdd = d; }
  const endAsset = capital * growth;
  const setV = (id, val) => { const el = $(id); el.textContent = val; return el; };
  setV("pc-start", M(capital));
  setV("pc-start-date", "基準日 " + nav[i0][0] + "｜NAV " + F(s, 0));
  const elRet = setV("pc-ret", P(ret, 2)); elRet.className = "v " + (ret >= 0 ? "up" : "down");
  setV("pc-years", "持有 " + years.toFixed(1) + " 年（" + days.toFixed(0) + " 天）");
  const elProfit = setV("pc-profit", M(endAsset - capital)); elProfit.className = "v " + (ret >= 0 ? "up" : "down");
  setV("pc-end", M(endAsset));
  setV("pc-end-date", "基準日 " + nav[i1][0] + "｜NAV " + F(e, 0));
  const elCagr = setV("pc-cagr", P(cagr, 2)); elCagr.className = "v " + (cagr >= 0 ? "up" : "down");
  setV("pc-mdd", P(mdd, 2));
  $("pc-range").textContent = "目前區間：" + y0 + " 年（基準 " + nav[i0][0] + "，NAV " + F(s,0) + "）→ " + y1 + " 年（" + nav[i1][0] + "，NAV " + F(e,0) + "），NAV 成長 " + growth.toFixed(2) + " 倍";
}

/* ================= 全部渲染 ================= */
function renderAll() {
  renderCards();
  renderOps();
  renderHoldings();
  renderRestrictedHoldings();
  if (ORDER_PLANNER_UI) ORDER_PLANNER_UI.render();
  if ($("histsel").options.length) $("histsel").dispatchEvent(new Event("change"));
}
$("cap").value = "";
renderAll();
renderSellNote();
renderMonthlyReturns();
renderCharts();
renderHist();
initPerfCalc();
ORDER_PLANNER_UI=initOrderPlanner({data:DATA,money:M,getCapital:()=>USER_CAPITAL,getTargets:()=>{const targets=WEEKLY_CAPITAL_UI?.getTargets();if(!targets)throw Error('請先完成上方兩週總資金計算，再核對實際持股。');return targets;}});
WEEKLY_CAPITAL_UI=initWeeklyCapital({data:DATA,money:M,isReady:()=>weeklyCapitalReady(DATA,taipeiDate(),publicWeeklyStatus),onChange:()=>{if(ORDER_PLANNER_UI)ORDER_PLANNER_UI.render();}});
const deliveryCheck=DATA.share_delivery_check||{};
$("delivery-check-note").textContent=deliveryCheck.as_of?'配股公告核對基準 '+deliveryCheck.as_of+'｜'+(deliveryCheck.status==='PASS'?'官方來源核對完成':'部分來源或日期待確認，未核實配股仍鎖定'):'配股公告將於下次策略計算時自動核對；未確認日期仍保持鎖定。';
</script>
<script>__DASHBOARD_UI_JS__</script>
<dialog id="month-detail-dialog" class="month-dialog" aria-labelledby="month-detail-title"><div class="month-dialog-head"><div><div class="month-eyebrow">每月盈虧 · 個股貢獻</div><h2 id="month-detail-title"></h2><p id="month-detail-period"></p></div><button type="button" id="month-detail-close" aria-label="關閉月份明細">×</button></div><div class="month-stock-pair"><article class="month-stock winner"><div class="month-stock-label">最賺錢</div><div class="month-stock-code" id="month-winner-code"></div><h3 id="month-winner-name"></h3><div class="month-stock-points" id="month-winner-points"></div><p id="month-winner-contribution"></p></article><article class="month-stock loser"><div class="month-stock-label">最虧錢</div><div class="month-stock-code" id="month-loser-code"></div><h3 id="month-loser-name"></h3><div class="month-stock-points" id="month-loser-points"></div><p id="month-loser-contribution"></p></article></div><div class="month-dialog-foot"><p>顯示報酬貢獻百分點，不隨上方個人本金改變。</p><p>按對策略當月報酬的貢獻排名，包含未賣出持股漲跌、股利與費稅；不是股票本身的月漲跌幅。</p></div></dialog>
<script>__MONTHLY_DETAILS_JS__</script></body>
</html>
"""

HTML = HTML.replace("V5.0.0", RELEASE_VERSION)
HTML = HTML.replace("__DATA_JSON__", DATA_JSON)
for _name,_placeholder in [('weekly_status.js','__WEEKLY_STATUS_JS__'),('capital_preview.js','__CAPITAL_PREVIEW_JS__'),('dashboard_ui.css','__DASHBOARD_CSS__'),('dashboard_ui.js','__DASHBOARD_UI_JS__'),('monthly_details.js','__MONTHLY_DETAILS_JS__'),('weekly_capital.js','__WEEKLY_CAPITAL_JS__'),('weekly_capital_ui.js','__WEEKLY_CAPITAL_UI_JS__'),('order_planner.js','__ORDER_PLANNER_JS__'),('order_planner_ui.js','__ORDER_PLANNER_UI_JS__')]:
    with open(os.path.join(HERE,_name),encoding='utf-8') as _source:
        HTML=HTML.replace(_placeholder,_source.read())

# 統一僅輸出 index.html（GitHub Pages 唯一入口）；RSI選股器.html 為舊名冗餘檔，不再生成
for _name in ("index.html",):
    _tmp = _name + ".tmp"
    try:
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write(HTML)
        os.replace(_tmp, _name)  # 原子替換：中斷時仍保留上一份完整網頁
    except Exception:
        if os.path.exists(_tmp):
            os.remove(_tmp)
        raise
    print(f"[OK] 已生成 {_name}（{len(HTML):,} bytes | rebal {DATA.get('rebal_date')} | px {DATA.get('px_date')} | counts {DATA.get('counts')}）")
print("完成。")
