# -*- coding: utf-8 -*-
"""
天穹紅蓮三重脈衝時空追擊者 B3 — GitHub Actions 版 TG 推播（canonical 版）
- 策略不再自行計算：唯一來源 = B3 引擎（b3_canonical.py）產出的 output/latest_signals.json
- 本檔職責：讀 canonical → 依 /CASH 資產縮放建議股數 → 組 TG 訊息 → 發送
- 每週最後交易日 06:00(台北) 由 GitHub Actions 觸發（check_and_run 判斷本週最後交易日）
- 保留：/CASH 資產指令、AI 紅利三指標（ai_signals.json）、09:00 開盤掛單提醒
"""
import os, sys, json, ssl, traceback, urllib.request, subprocess, datetime, time, math

from zoneinfo import ZoneInfo

TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = str(os.environ["TG_CHAT_ID"])
STATE_FILE = "state.json"
AI_FILE = "ai_signals.json"
SIG_FILE = "output/latest_signals.json"
PERF_FILE = "output/perf.json"
ADJUST_THRESH = 0.25   # 與 B3 引擎一致：偏離目標 25% 才動作
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


def is_trading_day(d: datetime.date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in HOLIDAYS_2026


def prev_trading_day(today: datetime.date) -> datetime.date:
    d = today - datetime.timedelta(days=1)
    while not is_trading_day(d):
        d -= datetime.timedelta(days=1)
    return d


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
        # [B3] 只在「本週最後交易日」推播（與回測 w5_close 一致）；週五休市→提前週四
        if is_trading_day(today + datetime.timedelta(days=1)):
            if changed:
                save_state(old_codes, old_codes_b, prev_qty, prev_qty_b, asset, last_update_id)
            print(f"{today} 非本週最後交易日（明天開市），僅處理指令")
            return

    # ---------- 讀 canonical（B3 唯一策略結果）----------
    if not os.path.exists(SIG_FILE):
        tg_send("❌ output/latest_signals.json 不存在 — 請先執行 b3_canonical.py")
        sys.exit(1)
    SIG = json.load(open(SIG_FILE, encoding="utf-8"))
    PERF = {}
    if os.path.exists(PERF_FILE):
        try:
            PERF = json.load(open(PERF_FILE, encoding="utf-8"))
        except Exception:
            PERF = {}

    actions = SIG.get("actions", [])
    name_map = {a["stock_code"]: a["stock_name"] for a in actions}
    counts = SIG.get("counts", {})
    if asset is None:
        asset = 100_000  # 未設定 /CASH 時預設 10 萬

    buys = [a for a in actions if a["action"] == "BUY"]
    sells = [a for a in actions if a["action"] == "SELL"]
    reb_buy = [a for a in actions if a["action"] == "REBALANCE_BUY"]
    reb_sell = [a for a in actions if a["action"] == "REBALANCE_SELL"]
    holds = [a for a in actions if a["action"] == "HOLD"]
    codes = [a["stock_code"] for a in actions if a["channel"] == "A" and a["action"] != "SELL"]
    codes_b = [a["stock_code"] for a in actions if a["channel"] == "B" and a["action"] != "SELL"]

    def qty_of(a):
        px = a.get("price")
        if px and px > 0:
            return int(asset * a.get("target_weight", 0) // px)
        return None

    def amt_of(a):
        return asset * a.get("target_weight", 0)

    sig_day = SIG.get("sig_date", "—")
    rebal_day = SIG.get("rebalance_date", "—")
    sw_txt = "🟢 ON（動態A）" if SIG.get("tsm_on") else "⚪ OFF（純 A 通道）"
    dyn_txt = "\nB 空手 → 額度併入 A 通道（A 吃 95%）" if (SIG.get("tsm_on") and not codes_b) else ""

    def qty_txt(a):
        """依資產縮放的建議股數；買不起（<1 股）時給說明"""
        q = qty_of(a)
        if q is None or q <= 0:
            need = amt_of(a)
            return f"買不起（此檔需約 ${need:,.0f}，低於 1 股）"
        return f"{q:,} 股 約 ${amt_of(a):,.0f}"

    lines = [f"<b>🌩️ 天穹紅蓮三重脈衝時空追擊者 B3</b>",
             f"訊號基準：{sig_day}｜調倉日：{rebal_day}（今日 09:00 開盤掛單，以開盤價成交）",
             f"資產基準：{asset:,.0f}　權值通道：{sw_txt}",
             f"A 分檔權重：Rank1-5×1.5 / 6-10×1.0 / 11-15×0.5｜B 等權{dyn_txt}\n"]

    # 🟢 BUY
    lines.append(f"<b>🟢 買進（{len(buys)}）</b>")
    if buys:
        for a in sorted(buys, key=lambda x: x.get("rank") or 99):
            lines.append(f"• <code>{a['stock_code']}</code> {a['stock_name']}（{a['channel']}｜Rank {a['rank']}）"
                         f"目標權重 {a['target_weight']*100:.1f}%｜建議 {qty_txt(a)}")
    else:
        lines.append("（無）")
    lines.append("")

    # 🔴 SELL
    lines.append(f"<b>🔴 賣出（{len(sells)}）</b>")
    if sells:
        for a in sorted(sells, key=lambda x: x.get("stock_code") or ""):
            lines.append(f"• <code>{a['stock_code']}</code> {a['stock_name']}｜原因：{a['reason']}｜"
                         f"賣出 {a['sell_shares']:,} 股（回測） 約 ${a['estimated_sell_amount']:,.0f}")
    else:
        lines.append("（無）")
    lines.append("")

    # 🟡 REBALANCE
    reb = reb_buy + reb_sell
    lines.append(f"<b>🟡 調整持倉（{len(reb)}）</b>")
    if reb:
        for a in sorted(reb, key=lambda x: -abs(x.get("weight_diff") or 0)):
            q = qty_of(a)
            mark = "加碼 +" if a["action"] == "REBALANCE_BUY" else "減碼 -"
            lines.append(f"• <code>{a['stock_code']}</code> {a['stock_name']}（{a['channel']}）"
                         f"目前 {a['current_weight']*100:.1f}% → 目標 {a['target_weight']*100:.1f}%｜{mark}{q:,} 股")
    else:
        lines.append("（無）")
    lines.append("")

    # ⚪ HOLD
    lines.append(f"<b>⚪ 持有不動（{len(holds)}）</b>")
    if holds:
        for a in sorted(holds, key=lambda x: x.get("rank") or 99):
            q = qty_of(a)
            q_str = qty_txt(a) if q and q > 0 else f"買不起（此檔需約 ${amt_of(a):,.0f}，低於 1 股）"
            lines.append(f"• <code>{a['stock_code']}</code> {a['stock_name']}（{a['channel']}｜Rank {a['rank']}）"
                         f"目標 {a['target_weight']*100:.1f}%｜建議 {q_str}")
    else:
        lines.append("（無）")
    lines.append("")

    # 💰 資金狀態
    lines.append("💰 資金狀態")
    lines.append(f"我的資產：{asset:,.0f} | 回測總資產：{SIG.get('portfolio_value', 0):,.0f} | "
                 f"回測現金：{SIG.get('cash', 0):,.0f}（Buffer {SIG.get('buffer', 0)*100:.1f}%）")
    lines.append(f"本週買進約 ${SIG.get('buy_total', 0):,.0f}（回測）｜賣出約 ${SIG.get('sell_total', 0):,.0f}（回測）")

    # ⚠️ B 通道集中警告
    for w in SIG.get("warnings", []):
        lines.append(f"⚠️ {w}")

    # 🤖 AI 紅利監控
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

    lines.append("\nℹ️ 09:00 開盤若跳空鎖漲/跌停（±9.9%）可能掛不到，沒成交下週再試、不追單")
    lines.append("ℹ️ 建議股數依昨日收盤價試算，開盤跳空時請以 09:00 開盤價重新計算股數")
    lines.append("ℹ️ 回覆 <code>/CASH 金額</code> 更新資產後，建議股數會自動重算")

    tg_send("\n".join(lines))
    print(f"[{rebal_day}] sw={SIG.get('tsm_on')} "
          f"BUY{len(buys)}/SELL{len(sells)}/REBAL{len(reb)}/HOLD{len(holds)} "
          f"A={len(codes)} B={len(codes_b)}")

    # 存 state：asset + 依資產縮放的目標股數（供下週對比加/減）
    new_qty = {a["stock_code"]: qty_of(a) for a in actions
               if a["action"] != "SELL" and qty_of(a)}
    new_qty_b = {}
    for a in actions:
        if a["channel"] == "B" and a["action"] != "SELL" and a["stock_code"] in new_qty:
            new_qty_b[a["stock_code"]] = new_qty.pop(a["stock_code"])
    save_state(codes, codes_b, new_qty, new_qty_b, asset, last_update_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        try:
            tg_send(f"❌ TG 推播執行失敗：{str(e)[:200]}")
        except Exception:
            pass
        sys.exit(1)
