# -*- coding: utf-8 -*-
"""
天穹紅蓮三重脈衝時空追擊者 B3 — GitHub Actions 版 TG 推播（canonical 版，純顯示）
- 策略不再自行計算：唯一來源 = B3 引擎（b3_canonical.py）產出的 output/latest_signals.json
- 本檔職責：讀 canonical → 依固定本金縮放建議股數 → 組 TG 訊息 → 發送
- 純顯示「買 / 賣 / 持倉% / 調倉%」：不再接受 /CASH 等指令、不顯示回測現金/買賣金額
- 每週最後交易日 06:00(台北) 由 GitHub Actions 觸發（check_and_run 判斷本週最後交易日）
- 保留：AI 紅利三指標（ai_signals.json）、B 通道集中警告、S5 執行文案
- 執行語氣為 S5：最後交易日 14:30 盤後零股、未成交次日 09:00 開盤市價補單
- 發布前先過 publish_gate（validation/新鮮度/結構），同一週同一組合以指紋去重只發一次
"""
import os, sys, json, ssl, traceback, urllib.request, datetime, math

from zoneinfo import ZoneInfo

from publish_gate import evaluate as gate_evaluate, fingerprint as gate_fp, load_holidays

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = str(os.environ.get("TG_CHAT_ID", ""))
STATE_FILE = "state.json"
AI_FILE = "ai_signals.json"
SIG_FILE = "output/latest_signals.json"
START_ASSET = 100_000   # 固定本金基準：股數 = 本金 × 目標權重 ÷ 價格；不再接受 /CASH 指令
FORCE = os.environ.get("FORCE_FULL") == "1" or os.environ.get("FORCE") == "1"
DRY_RUN = os.environ.get("DRY_RUN") == "1"
TW = ZoneInfo("Asia/Taipei")

HOLIDAYS = load_holidays()
CTX = ssl.create_default_context()


def tg_send(text: str):
    if DRY_RUN:
        print("=" * 70)
        print(text)
        print("=" * 70)
        return True
    if not TG_TOKEN or not TG_CHAT_ID:
        raise RuntimeError("缺少 TG_TOKEN / TG_CHAT_ID（且非 DRY_RUN）")
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.loads(r.read())["ok"]


def is_trading_day(d: datetime.date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in HOLIDAYS


def load_state():
    empty = {"codes": [], "codes_b": [], "qty": {}, "qty_b": {}, "last_pushed_key": None}
    if not os.path.exists(STATE_FILE):
        return empty
    try:
        s = json.load(open(STATE_FILE, encoding="utf-8"))
        s.setdefault("codes", [])
        s.setdefault("codes_b", [])
        s.setdefault("qty", {})
        s.setdefault("qty_b", {})
        s.setdefault("last_pushed_key", None)
        return s
    except Exception:
        return empty


def save_state(codes, codes_b, qty, qty_b, last_pushed_key=None):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"codes": codes, "codes_b": codes_b, "qty": qty, "qty_b": qty_b,
                   "last_pushed_key": last_pushed_key}, f, ensure_ascii=False)
    print("state.json 已寫入（CI 中由 workflow 統一提交）" if os.environ.get("GITHUB_ACTIONS") == "true"
          else "state.json 已寫入（非 CI，未自動 git push）")


def load_ai_signals():
    default = {"capex": "待更新", "monet": "待更新", "cowos": "待更新", "updated": "—", "note": "請每季更新 ai_signals.json"}
    if not os.path.exists(AI_FILE):
        return default
    try:
        s = json.load(open(AI_FILE, encoding="utf-8"))
        s.setdefault("capex", "待更新"); s.setdefault("monet", "待更新"); s.setdefault("cowos", "待更新")
        s.setdefault("updated", "—"); s.setdefault("note", "")
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
    last_pushed_key = state.get("last_pushed_key")

    if not FORCE:
        today = datetime.datetime.now(TW).date()
        if not is_trading_day(today):
            print(f"{today} 非交易日，不推播"); return
        if is_trading_day(today + datetime.timedelta(days=1)):
            print(f"{today} 非本週最後交易日（明天開市），不推播"); return

    gate = gate_evaluate(force=FORCE)
    if not gate["ok"]:
        print("[gate] 中止推播：", gate["reason"]); return

    if not os.path.exists(SIG_FILE):
        tg_send("❌ output/latest_signals.json 不存在 — 請先執行 b3_canonical.py"); sys.exit(1)
    SIG = json.load(open(SIG_FILE, encoding="utf-8"))

    push_key = gate_fp(SIG)
    if not FORCE and push_key == last_pushed_key:
        print(f"rebalance={SIG.get('rebalance_date')} key={push_key} 已推播過，跳過（避免重複發送）"); return

    actions = SIG.get("actions", [])
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
            return int(START_ASSET * a.get("target_weight", 0) // px)
        return None

    def cur_of(a):
        code = a["stock_code"]
        if a.get("channel") == "B":
            return int(prev_qty_b.get(code) or 0)
        return int(prev_qty.get(code) or 0)

    def delta_of(a):
        cur = cur_of(a)
        tgt = qty_of(a)
        if tgt is None:
            return None, cur, None
        return tgt, cur, tgt - cur

    sig_day = SIG.get("sig_date", "—")
    rebal_day = SIG.get("rebalance_date", "—")
    sw_txt = "🟢 ON（動態A）" if SIG.get("tsm_on") else "⚪ OFF（純 A 通道）"
    dyn_txt = "\nB 空手 → 額度併入 A 通道" if (SIG.get("tsm_on") and not codes_b) else ""

    lines = [f"<b>🌩️ 天穹紅蓮三重脈衝時空追擊者 B3</b>",
             f"訊號基準：{sig_day}｜調倉日：{rebal_day}",
             f"權值通道：{sw_txt}",
             "⏰ <b>S5 執行</b>：今日 14:30 前掛『盤後零股』；未成交部份於下一交易日 09:00 開盤市價補單",
             f"A 分檔權重：Rank1-5×1.5 / 6-10×1.0 / 11-15×0.5｜B 等權{dyn_txt}\n"]

    lines.append(f"<b>🟢 買進（{len(buys)}）</b>")
    if buys:
        for a in sorted(buys, key=lambda x: x.get("rank") or 99):
            tgt, cur, dlt = delta_of(a)
            if tgt is None:
                tail = "價格不足，無法估股數"
            elif dlt > 0:
                tail = f"買進 {dlt:,} 股（目標 {tgt:,}／目前 {cur:,}）"
            elif dlt == 0:
                tail = f"已達目標 {tgt:,} 股，無需買進"
            else:
                tail = f"目前 {cur:,} 股已超過目標 {tgt:,}，無需買進（請核對持股）"
            lines.append(f"• <code>{a['stock_code']}</code> {a['stock_name']}（{a['channel']}｜Rank {a['rank']}）目標權重 {a['target_weight']*100:.1f}%｜持倉 {a['current_weight']*100:.1f}%｜{tail}")
    else:
        lines.append("（無）")
    lines.append("")

    lines.append(f"<b>🔴 賣出（{len(sells)}）</b>")
    if sells:
        for a in sorted(sells, key=lambda x: x.get("stock_code") or ""):
            cur = cur_of(a)
            if cur > 0:
                tail = f"賣出（清倉）{cur:,} 股（上週策略持股，請以券商實際持有為準）"
            else:
                tail = "賣出全部持股（無持股存檔，請以券商實際持有股數為準）"
            lines.append(f"• <code>{a['stock_code']}</code> {a['stock_name']}｜原因：{a['reason']}｜{tail}")
    else:
        lines.append("（無）")
    lines.append("")

    reb = reb_buy + reb_sell
    lines.append(f"<b>🟡 調整持倉（{len(reb)}）</b>")
    if reb:
        for a in sorted(reb, key=lambda x: -abs(x.get("weight_diff") or 0)):
            tgt, cur, dlt = delta_of(a)
            w_txt = f"{a['current_weight']*100:.1f}% → {a['target_weight']*100:.1f}%"
            if tgt is None:
                tail = "價格資料不足，無法估股數"
            elif a["action"] == "REBALANCE_BUY":
                tail = (f"加碼買進 {dlt:,} 股（目標 {tgt:,}／目前 {cur:,}）" if dlt > 0
                        else f"目前 {cur:,} 股已達/超過目標 {tgt:,}，無需加碼")
            else:
                tail = (f"減碼賣出 {-dlt:,} 股（目前 {cur:,}／目標 {tgt:,}）" if dlt < 0
                        else f"目前 {cur:,} 股已達/低於目標 {tgt:,}，無需減碼")
            lines.append(f"• <code>{a['stock_code']}</code> {a['stock_name']}（{a['channel']}）{w_txt}｜{tail}")
    else:
        lines.append("（無）")
    lines.append("")

    lines.append(f"<b>⚪ 持有不動（{len(holds)}）</b>")
    if holds:
        for a in sorted(holds, key=lambda x: x.get("rank") or 99):
            tgt, cur, dlt = delta_of(a)
            if tgt is None:
                tail = "價格資料不足"
            elif dlt == 0:
                tail = f"持有 {cur:,} 股，不動"
            else:
                tail = f"目前 {cur:,}／目標 {tgt:,}（差 {dlt:+,}，未達 25% 門檻，不動）"
            lines.append(f"• <code>{a['stock_code']}</code> {a['stock_name']}（{a['channel']}｜Rank {a['rank']}）目標 {a['target_weight']*100:.1f}%｜持倉 {a['current_weight']*100:.1f}%｜{tail}")
    else:
        lines.append("（無）")
    lines.append("")

    for w in SIG.get("warnings", []):
        lines.append(f"⚠️ {w}")

    ai = load_ai_signals()
    lines.append(f"\n<b>🤖 AI 紅利監控（{ai.get('updated', '—')}）</b>")
    lines.append(f"▫️ Hyperscaler capex：{ai_emoji(ai.get('capex'))} {ai.get('capex')}")
    lines.append(f"▫️ AI 變現率：{ai_emoji(ai.get('monet'))} {ai.get('monet')}")
    lines.append(f"▫️ CoWoS 產能：{ai_emoji(ai.get('cowos'))} {ai.get('cowos')}")
    weak = any(str(ai.get(k)).find(x) >= 0 for k in ["capex", "monet", "cowos"] for x in ["轉負", "落後", "鬆動", "下降", "減速"])
    if weak:
        lines.append("⚠️ 訊號轉弱：建議將權值通道降為 80/20 或全關！")
    if ai.get("note"):
        lines.append(f"ℹ️ {ai.get('note')}")

    lines.append("\nℹ️ <b>S5</b>：最後交易日 14:30 前掛盤後零股；未成交 remainder 於下一交易日 09:00 開盤市價補單，勿自行挑價或延後")
    lines.append("ℹ️ 股數依訊號基準日收盤價試算（本金固定 10 萬），僅供估算；實際請以盤後/開盤即時價與整股（零股 1 股）成交為準")
    lines.append("ℹ️ SELL 為清倉、股數以你券商實際持有為準")

    tg_send("\n".join(lines))
    print(f"[{rebal_day}] sw={SIG.get('tsm_on')} BUY{len(buys)}/SELL{len(sells)}/REBAL{len(reb)}/HOLD{len(holds)} A={len(codes)} B={len(codes_b)}")

    new_qty = {a["stock_code"]: qty_of(a) for a in actions if a["action"] != "SELL" and qty_of(a)}
    new_qty_b = {}
    for a in actions:
        if a["channel"] == "B" and a["action"] != "SELL" and a["stock_code"] in new_qty:
            new_qty_b[a["stock_code"]] = new_qty.pop(a["stock_code"])
    save_state(codes, codes_b, new_qty, new_qty_b, push_key)


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
