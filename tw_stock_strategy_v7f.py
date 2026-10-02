# -*- coding: utf-8 -*-
"""
台股三頻率 RSI 策略 - GitHub Actions 版（v7h 動態A·雙通道 60/40）
策略邏輯（與回測 v7e_dual_v2 60/40 一致）：
  A 通道（60%）：v7e 原案（RSI120>55、RSI60<75、RSI20三日漲>2%、RSI20>75連3日、
                 ROE>0、近20日成交金額前60%、一般股票過濾），15 檔
  B 通道（40%）：權值龍頭動能（成交金額前15 + 收盤>MA60 + RSI120>60 + RSI20<88 + ROE>0），4 檔
  開關 tsm_long：台積電 MA20≥MA60 且收盤≥MA60（昨日資料）→ ON 時 60/40，OFF 時全 A
  出場：持有滿80交易日 或 收盤<MA60
  執行：每週最後交易日 12:00(台北) 推播，昨日訊號→今日盤後收盤價成交
  AI 紅利監控：讀 ai_signals.json 三指標（Hyperscaler capex / AI變現率 / CoWoS產能），
              任一轉弱即提示降 B 通道
v7f 官方績效（60/40）：CAGR 46.5%、MDD -27.8%（2015-01 ~ 2026-09）
  AI 年 2023-2026：CAGR 59.6%、MDD -26.5%
"""
import os, sys, json, ssl, traceback, urllib.request, subprocess, datetime, time, math

import pandas as pd
from zoneinfo import ZoneInfo

TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = str(os.environ["TG_CHAT_ID"])
FINLAB_TOKEN = os.environ["FINLAB_TOKEN"]
MAX_HOLD_A = 15
MAX_HOLD_B = 4
STATE_FILE = "state.json"
AI_FILE = "ai_signals.json"
W_A = 0.60          # A 通道權重（開關 ON 時）
W_B = 0.40          # B 通道權重
TARGET_W_A = W_A * 0.95 / MAX_HOLD_A  # 3.8%
TARGET_W_B = W_B * 0.95 / MAX_HOLD_B  # 9.5%
ADJUST_THRESH = 0.25   # 與回測引擎/網頁一致：偏離目標市值 25% 才調倉（原 0.05 會過度交易）
FORCE = os.environ.get("FORCE") == "1"
TW = ZoneInfo("Asia/Taipei")

# TWSE 官方 2026 休市日（非週末部分；每年需更新）
HOLIDAYS_2026 = {
    "2026-01-01", "2026-02-12", "2026-02-13",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-27", "2026-04-03", "2026-04-06", "2026-05-01", "2026-06-19",
    "2026-09-25", "2026-09-28", "2026-10-09", "2026-10-26", "2026-12-25",
}

CTX = ssl.create_default_context()


def tg_send(text: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.loads(r.read())["ok"]


def is_quota_error(e):
    m = str(e).lower()
    return any(k in m for k in ["usage exceed", "2000 mb", "5000 mb", "quota",
                                "429", "too many requests", "用量", "vip program"])


def rsi(close, n):
    d = close.diff()
    g = d.clip(lower=0); l = -d.clip(upper=0)
    ag = g.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    al = l.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    return 100 - 100/(1 + ag/al)


def is_trading_day(d: datetime.date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in HOLIDAYS_2026


def is_common_stock(code) -> bool:
    s = str(code)
    return len(s) == 4 and s.isdigit() and not s.startswith("0")


def prev_trading_day(today: datetime.date) -> datetime.date:
    d = today - datetime.timedelta(days=1)
    while not is_trading_day(d):
        d -= datetime.timedelta(days=1)
    return d


def must_get(data, key):
    last_e = None
    for i in range(3):
        try:
            df = data.get(key)
            if df is not None and not getattr(df, "empty", False):
                return df
        except Exception as e:
            if is_quota_error(e):
                raise
            last_e = e
        time.sleep(2)
    raise RuntimeError(f"必要資料 {key} 取得失敗（重試3次）：{last_e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"codes": [], "codes_b": [], "qty": {}, "qty_b": {}, "asset": None, "last_update_id": 0}
    try:
        s = json.load(open(STATE_FILE, encoding="utf-8"))
        s.setdefault("codes", [])
        s.setdefault("codes_b", [])
        s.setdefault("qty", {})
        s.setdefault("qty_b", {})
        s.setdefault("asset", None)
        s.setdefault("last_update_id", 0)
        return s
    except Exception:
        return {"codes": [], "codes_b": [], "qty": {}, "qty_b": {}, "asset": None, "last_update_id": 0}


def save_state(codes, codes_b, qty, qty_b, asset, last_update_id):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"codes": codes, "codes_b": codes_b, "qty": qty, "qty_b": qty_b,
                   "asset": asset, "last_update_id": last_update_id},
                  f, ensure_ascii=False)
    try:
        subprocess.run(["git", "config", "user.name", "rsi-bot"], check=False)
        subprocess.run(["git", "config", "user.email", "bot@rsi.local"], check=False)
        subprocess.run(["git", "add", STATE_FILE], check=True)
        subprocess.run(["git", "commit", "-m", "update state"], check=True)
    except Exception:
        pass
    for attempt in range(2):
        if subprocess.run(["git", "push"]).returncode == 0:
            print("state.json committed")
            return
        subprocess.run(["git", "pull", "--rebase"], check=False)
    tg_send("⚠️ 持股狀態存檔失敗（git push），下次推播可能重複提示，請手動確認。")
    print("git push failed after retry")


def fetch_cash_command(state):
    offset = state.get("last_update_id", 0)
    asset = state.get("asset")
    changed = False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset + 1}&timeout=5"
    try:
        with urllib.request.urlopen(url, timeout=30, context=CTX) as r:
            data = json.loads(r.read())
        max_id = offset
        for upd in data.get("result", []):
            max_id = max(max_id, upd.get("update_id", 0))
            msg = upd.get("message", {}) or {}
            if str((msg.get("chat", {}) or {}).get("id", "")) != TG_CHAT_ID:
                continue
            text = (msg.get("text") or "").strip()
            if text.upper().startswith("/CASH"):
                parts = text.split()
                if len(parts) >= 2:
                    try:
                        val = float(parts[1])
                        if not math.isfinite(val) or val <= 0:
                            raise ValueError
                        asset = val
                        changed = True
                        tg_send(f"✅ 總資產已更新為 {asset:,.0f} 元")
                    except Exception:
                        tg_send("❌ /CASH 格式錯誤，請輸入正數，例如 /CASH 100000")
        return asset, max_id, changed or max_id != offset
    except Exception as e:
        print("getUpdates failed:", e)
        return asset, offset, False


def load_ai_signals():
    """AI 紅利三指標（repo 內 ai_signals.json，每季由使用者更新；缺檔給預設）。"""
    default = {"capex": "待更新", "monet": "待更新", "cowos": "待更新",
               "updated": "—", "note": "請每季更新 ai_signals.json"}
    if not os.path.exists(AI_FILE):
        return default
    try:
        s = json.load(open(AI_FILE, encoding="utf-8"))
        s.setdefault("capex", "待更新")
        s.setdefault("monet", "待更新")
        s.setdefault("cowos", "待更新")
        s.setdefault("updated", "—")
        s.setdefault("note", "")
        return s
    except Exception:
        return default


def ai_emoji(v):
    v = str(v)
    if "正" in v or "跟上" in v or "滿載" in v or "成長" in v:
        return "🟢"
    if "轉弱" in v or "落後" in v or "鬆動" in v or "轉負" in v or "下降" in v:
        return "🔴"
    if "持平" in v or "觀察" in v or "減速" in v:
        return "🟡"
    return "⚪"


def main():
    state = load_state()
    old_codes = [str(c) for c in state.get("codes", [])]
    old_codes_b = [str(c) for c in state.get("codes_b", [])]
    prev_qty = state.get("qty", {}) or {}
    prev_qty_b = state.get("qty_b", {}) or {}
    asset, last_update_id, changed = fetch_cash_command(state)

    if not FORCE:
        today = datetime.datetime.now(TW).date()
        if not is_trading_day(today):
            if changed:
                save_state(old_codes, old_codes_b, prev_qty, prev_qty_b, asset, last_update_id)
            print(f"{today} 非交易日，僅處理指令")
            return
        if is_trading_day(today + datetime.timedelta(days=1)):
            if changed:
                save_state(old_codes, old_codes_b, prev_qty, prev_qty_b, asset, last_update_id)
            print(f"{today} 非本週最後交易日，僅處理指令")
            return

    import finlab
    from finlab import data
    finlab.login(api_token=FINLAB_TOKEN)

    close = must_get(data, "price:收盤價").apply(pd.to_numeric, errors="coerce")
    raw_close = close
    roe = must_get(data, "fundamental_features:ROE稅後").apply(pd.to_numeric, errors="coerce")
    tv = must_get(data, "price:成交金額")

    tv20 = tv.rolling(20).mean()
    liq = tv20.gt(tv20.quantile(0.40, axis=1), axis=0)  # A 通道：前60%
    ma60 = close.rolling(60).mean()
    r20 = rsi(close, 20); r60 = rsi(close, 60); r120 = rsi(close, 120)
    r20s = r20.shift(1)

    # ---------- A 通道（v7e，60%） ----------
    long_up = (r120 > 55).shift(1)
    mid_ok  = (r60 < 75).shift(1)
    rally   = (r20.pct_change(3, fill_method=None) > 0.02).shift(1)
    stuck   = ((r20 > 75).rolling(3).sum() == 3).shift(1)
    roe_ok  = (roe > 0).shift(1).fillna(True)
    buyA = long_up & mid_ok & rally & stuck & roe_ok & liq.shift(1).fillna(True)
    sellA = buyA.shift(80).fillna(False) | (close < ma60)
    posA = buyA.hold_until(sellA)

    # ---------- B 通道（權值龍頭動能，40%） ----------
    rank_tv = tv20.rank(axis=1, ascending=False)
    big = rank_tv.shift(1) <= 15
    bull = close.shift(1) > ma60.shift(1)
    lt_up = (r120 > 60).shift(1)
    not_hot = r20s < 88
    buyB = big & bull & lt_up & not_hot & roe_ok
    sellB = buyB.shift(80).fillna(False) | (close < ma60)
    posB = buyB.hold_until(sellB)

    # ---------- tsm_long 開關（台積電 MA20≥MA60 且站上 MA60） ----------
    tsm_on = False
    try:
        tsm = close["2330"]
        tsm_ma20 = tsm.rolling(20).mean().shift(1)
        tsm_ma60 = tsm.rolling(60).mean().shift(1)
        tsm_c = tsm.shift(1)
        if pd.notna(tsm_ma20.iloc[-1]) and pd.notna(tsm_ma60.iloc[-1]) and pd.notna(tsm_c.iloc[-1]):
            tsm_on = bool(tsm_ma20.iloc[-1] >= tsm_ma60.iloc[-1] and tsm_c.iloc[-1] >= tsm_ma60.iloc[-1])
    except Exception:
        tsm_on = False

    last_day = close.dropna(how="all").index[-1]

    if not FORCE:
        expected = pd.Timestamp(prev_trading_day(datetime.datetime.now(TW).date()))
        if pd.Timestamp(last_day).normalize() != expected:
            tg_send(f"⚠️ FinLab 資料未更新完成（預期 {expected:%Y-%m-%d}，實際 {pd.Timestamp(last_day):%Y-%m-%d}），本次不產生訊號。")
            print("data not fresh, abort")
            return

    name_map = {}
    try:
        info = data.get("company_basic_info")
        if info is not None and "公司簡稱" in info.columns:
            key = "stock_id" if "stock_id" in info.columns else None
            name_map = info.set_index(key)["公司簡稱"].to_dict() if key else info["公司簡稱"].to_dict()
    except Exception:
        pass

    # A 通道：RSI20 排序取前 15（一般股票過濾）
    curA_pos = posA.loc[last_day]
    codesA = [str(c) for c in curA_pos[curA_pos].index]
    scoredA = []
    for c in codesA:
        try:
            v = float(r20[c].loc[last_day])
        except Exception:
            v = -1
        scoredA.append((c, v))
    scoredA.sort(key=lambda x: x[1], reverse=True)
    cur_codes = [c for c, _ in scoredA if is_common_stock(c)][:MAX_HOLD_A]

    # B 通道：成交金額排序取前 4
    curB_pos = posB.loc[last_day]
    codesB = [str(c) for c in curB_pos[curB_pos].index]
    scoredB = []
    for c in codesB:
        try:
            v = float(tv20[c].loc[last_day]) if pd.notna(tv20[c].loc[last_day]) else -1
        except Exception:
            v = -1
        scoredB.append((c, v))
    scoredB.sort(key=lambda x: x[1], reverse=True)
    cur_codes_b = [c for c, _ in scoredB if is_common_stock(c)][:MAX_HOLD_B]

    # ===== [v7h-fix] 清單以回測引擎實際持股快照為權威（與網頁同源）=====
    # 修復：posA/posB 是理想訊號部位（2033/2887 殘留、6226 漏列、1303 通道錯位）
    _new_buys = set()
    try:
        _snap = pd.read_csv("my_holdings_weekly.csv")
        _snap["date"] = pd.to_datetime(_snap["date"])
        _snap_last = _snap["date"].max()
        _wk = _snap[_snap["date"] == _snap_last]
        _sa = [str(int(c)).zfill(4) for c in _wk.loc[_wk["pool"] == "A", "code"]]
        _sb = [str(int(c)).zfill(4) for c in _wk.loc[_wk["pool"] == "B", "code"]]
        _sa = [c for c in _sa if c in close.columns]
        _sb = [c for c in _sb if c in close.columns]
        if _sa or _sb:
            cur_codes, cur_codes_b = _sa, _sb
            try:
                _tr = pd.read_csv("my_trades_v7f.csv")
                _tr["date"] = pd.to_datetime(_tr["date"])
                _trl = _tr[_tr["date"] == _snap_last]
                _new_buys = {str(int(c)).zfill(4) for c in
                             _trl.loc[_trl["side"] == "BUY", "code"]}
            except Exception:
                _new_buys = set()
            print(f"[引擎快照] {_snap_last.date()} A={len(_sa)} B={len(_sb)} 新買={len(_new_buys)}")
    except Exception as e:
        print("快照讀取失敗，沿用訊號清單:", e)

    # ===== [v7h] 通道唯一歸屬（與引擎/網頁同源）=====
    # 已持有→保留原通道；新進→A 優先；跨通道→淨額調倉（不賣+買矛盾）
    _pp = {c: "A" for c in old_codes}
    _pp.update({c: "B" for c in old_codes_b})
    _pa_all = set(old_codes) | set(old_codes_b)
    _fa, _fb = [], []
    for c in cur_codes:
        if _pp.get(c) == "B" and c in cur_codes_b:
            _fb.append(c)
        else:
            _fa.append(c)
    for c in cur_codes_b:
        if c not in _fa and c not in _fb:
            _fb.append(c)
    cur_codes, cur_codes_b = _fa, _fb

    # ===== [v7h-fix] 賣出以「回測引擎本週實際 SELL 事件」為權威（與網頁同源）=====
    # 修復：_gone 依賴 state.json 比對，但 state 被覆寫後賣出永遠是空
    _sold_pool = {}
    try:
        _trl_sell = _trl.loc[_trl["side"] == "SELL"]
        for _r in _trl_sell.itertuples():
            _c = str(int(_r.code)).zfill(4)
            _sold_pool[_c] = str(_r.pool)
    except Exception:
        _sold_pool = {}
    switch_codes = [c for c in cur_codes if _pp.get(c) == "B"]
    switch_codes += [c for c in cur_codes_b if _pp.get(c) == "A"]
    buys  = [c for c in cur_codes if c in _new_buys]
    _cur_all = set(cur_codes) | set(cur_codes_b)
    # 賣出＝本週 SELL 事件且已完全出清（不在目前快照）；部分減持(1709)仍算持有
    sells = [c for c in sorted(_sold_pool) if _sold_pool[c] == "A" and c not in _cur_all]
    holds = [c for c in cur_codes if c not in buys]
    buys_b  = [c for c in cur_codes_b if c in _new_buys]
    sells_b = [c for c in sorted(_sold_pool) if _sold_pool[c] == "B" and c not in _cur_all]
    holds_b = [c for c in cur_codes_b if c not in buys_b]

    sig_day = pd.Timestamp(last_day).strftime("%Y-%m-%d")

    def price_of(c):
        try:
            return float(raw_close[c].loc[last_day])
        except Exception:
            return None

    def line(c, w_target, show_qty=False):
        px = price_of(c)
        px_str = f"{px:.1f}" if px else "—"
        qty_str = ""
        if show_qty and asset and px and px > 0:
            qty = int(asset * w_target // px)
            qty_str = f"｜建議 {qty} 股"
        return f"• <code>{c}</code> {name_map.get(c, c)}　{px_str}{qty_str}"

    def calc_target(c, w_target):
        px = price_of(c)
        if asset and px and px > 0:
            return int(asset * w_target // px)
        return None

    # [v7h 動態A] 權重：B 空手 → A 吃 95%；B 有候選 → A60/B40（各 ×0.95）
    nA_, nB_ = len(cur_codes), len(cur_codes_b)
    if tsm_on and nB_ == 0:
        wa_eff, wb_eff = 1.0, 0.0
    else:
        wa_eff, wb_eff = (W_A, W_B) if tsm_on else (1.0, 0.0)
    twA = (wa_eff * 0.95 / nA_) if nA_ else TARGET_W_A
    twB = (wb_eff * 0.95 / nB_) if (nB_ and wb_eff > 0) else TARGET_W_B

    target_qty = {c: calc_target(c, twA) for c in cur_codes}
    target_qty_b = {c: calc_target(c, twB) for c in cur_codes_b}

    # 試算所有已持有股（含跨通道 switch）；p 依其「原通道」qty 表、t 依「新通道」目標
    def _chk(c, cur_pool):
        t = (target_qty if cur_pool == "A" else target_qty_b).get(c)
        if cur_pool == "A":
            p = prev_qty.get(str(c), prev_qty_b.get(str(c)))
        else:
            p = prev_qty_b.get(str(c), prev_qty.get(str(c)))
        if t is None or p is None:
            return
        if abs(t - p) >= max(1, t * ADJUST_THRESH):   # 基準用目標股數 t（等價於網頁市值 25% 門檻）
            if cur_pool == "A":
                adjust.append((c, p, t))
            else:
                adjust_b.append((c, p, t))
    adjust, adjust_b = [], []
    for c in cur_codes:
        if c in _pa_all:
            _chk(c, "A")
    for c in cur_codes_b:
        if c in _pa_all:
            _chk(c, "B")

    asset_str = f"{asset:,.0f}" if asset else "未設定"
    sw_txt = "🟢 ON（動態A）" if tsm_on else "⚪ OFF（純 A 通道）"
    dyn_txt = "\nB 空手 → 額度併入 A 通道（A 吃 95%）" if (tsm_on and not cur_codes_b) else ""
    lines = [f"<b>📊 三頻率RSI 雙通道通知（v7h）</b>",
             f"訊號基準：{sig_day}（今日盤後成交）",
             f"資產基準：{asset_str}　權值通道：{sw_txt}",
             f"A 每檔目標 {twA*100:.1f}%｜B 每檔 {twB*100:.1f}%{dyn_txt}\n"]

    # A 通道區塊（買賣持已涵蓋全部，不再重複列完整清單）
    if old_codes:
        lines.append(f"<b>🟢 A買進（{len(buys)}）</b>")
        lines += [line(c, twA, True) for c in buys] or ["（無）"]
        lines.append(f"\n<b>🔴 A賣出（{len(sells)}）</b>")
        lines += [line(c, twA) for c in sells] or ["（無）"]
        lines.append(f"\n<b>⚪ A繼續持有（{len(holds)}）</b>")
        lines += [line(c, twA, True) for c in holds] or ["（無）"]
    else:
        lines.append("<b>📋 A通道完整持股清單（首次執行）</b>")
        lines += [line(c, twA, True) for c in cur_codes]

    # B 通道區塊（開關 ON 才顯示）
    if tsm_on:
        lines.append(f"\n<b>📌 B通道權值（40%）（{len(cur_codes_b)}）</b>")
        if old_codes_b:
            lines.append(f"　買進 {len(buys_b)}｜賣出 {len(sells_b)}｜持有 {len(holds_b)}")
        lines += [line(c, twB, True) for c in holds_b] or ["　（無權值訊號）"]
    else:
        lines.append("\n<b>📌 B通道：關閉</b>（台積電未站上季線，資金 100% 於 A 通道）")

    # 🟡 跨通道轉換（不整筆賣買，淨額調整即可）
    if switch_codes:
        lines.append(f"\n<b>🟡 通道轉換（{len(switch_codes)}）</b>")
        for c in switch_codes:
            tp_ = "B" if c in cur_codes_b else "A"
            lines.append(f"• <code>{c}</code> {name_map.get(c, c)} → 轉入 {tp_} 通道，請依目標股數淨額增減（勿賣掉再買）")

    # 需調倉
    if adjust or adjust_b:
        lines.append(f"\n<b>🔄 需調倉（{len(adjust)+len(adjust_b)}）</b>")
        for c, p, t in adjust:
            px = price_of(c)
            px_str = f"{px:.1f}" if px else "—"
            mark = "加" if t > p else "減"
            lines.append(f"• <code>{c}</code> {name_map.get(c, c)}　{px_str}｜目前 {p} → 目標 {t} 股（{mark}{abs(t-p)}）")
        for c, p, t in adjust_b:
            px = price_of(c)
            px_str = f"{px:.1f}" if px else "—"
            mark = "加" if t > p else "減"
            lines.append(f"• <code>{c}</code> {name_map.get(c, c)}（B）　{px_str}｜目前 {p} → 目標 {t} 股（{mark}{abs(t-p)}）")
    elif asset and (prev_qty or prev_qty_b):
        lines.append("\n✅ 持股皆在目標股數，本週無需調倉")
    elif not asset:
        lines.append("\n⚠️ 回覆 /CASH 設定總資產後，才會顯示需調倉股數")

    # AI 紅利監控
    ai = load_ai_signals()
    lines.append(f"\n<b>🤖 AI 紅利監控（{ai.get('updated', '—')}）</b>")
    lines.append(f"▫️ Hyperscaler capex：{ai_emoji(ai.get('capex'))} {ai.get('capex')}")
    lines.append(f"▫️ AI 變現率：{ai_emoji(ai.get('monet'))} {ai.get('monet')}")
    lines.append(f"▫️ CoWoS 產能：{ai_emoji(ai.get('cowos'))} {ai.get('cowos')}")
    weak = any(str(ai.get(k)).find(x) >= 0 for k in ["capex", "monet", "cowos"]
               for x in ["轉負", "落後", "鬆動", "下降", "減速"])
    if weak:
        lines.append("⚠️ 訊號轉弱：建議將權值通道降為 80/20 或全關！")
    if ai.get("note"):
        lines.append(f"ℹ️ {ai.get('note')}")

    lines.append("\nℹ️ 若收盤鎖漲/跌停可能排不到，沒成交下週再試、不追單")
    if not asset:
        lines.append("⚠️ 回覆 <code>/CASH 金額</code> 設定總資產以顯示建議股數")

    tg_send("\n".join(lines))
    print(f"[{sig_day}] sw={tsm_on} A={len(cur_codes)}(buy{len(buys)}/sell{len(sells)}) B={len(cur_codes_b)}(buy{len(buys_b)}/sell{len(sells_b)}) adjust={len(adjust)+len(adjust_b)}")

    new_qty = {c: q for c, q in target_qty.items() if q}
    new_qty_b = {c: q for c, q in target_qty_b.items() if q}
    save_state(cur_codes, cur_codes_b, new_qty, new_qty_b, asset, last_update_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        if is_quota_error(e):
            tg_send("⚠️ 今日 FinLab 流量已達上限，已停止更新。")
            print("QUOTA:", e)
        else:
            traceback.print_exc()
            try:
                tg_send(f"❌ 策略執行失敗：{str(e)[:200]}")
            except Exception:
                pass
        sys.exit(1)
