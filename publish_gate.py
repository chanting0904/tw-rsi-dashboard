# -*- coding: utf-8 -*-
"""B3 發布閘門（管線安全層；不計算策略、不修改 b3_canonical.py）。

在「產生網頁 (update_data.py)」與「發送 TG (tg_push_b3.py)」之前執行，
確保只會發布「本週最新、validation PASS、結構正確」的同一份 canonical：

  1) output/validation_log.json：verdict == PASS、strategy_version == B3、
     且 validation 的 rebalance_date 與 canonical 一致。
  2) 新鮮度（非 FORCE）：canonical.rebalance_date 必須等於「本週最後交易日」。
     → 引擎沒重跑（canonical 還是上週）時直接擋下，絕不發舊訊號。
  3) 結構：counts 合計 == actions 筆數、無重複 code、action 合法、
     target_weight 合計 <= 0.953、非 SELL 持股不得缺 price。

FAIL：回傳 ok=False，並在有 TG env 時發一筆警報；呼叫端應中止，
不更新 Web、不發送一般調倉訊息。

環境變數：
  FORCE_FULL=1  手動 full：放寬「新鮮度」（補跑/測試用），但仍要求 validation PASS 與結構正確。
  DRY_RUN=1     不實際發送 TG（本地測試用）。
"""
import os
import sys
import json
import ssl
import hashlib
import datetime
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SIG_FILE = os.path.join(HERE, "output", "latest_signals.json")
VAL_FILE = os.path.join(HERE, "output", "validation_log.json")
HOL_FILE = os.path.join(HERE, "holidays.json")
TW = ZoneInfo("Asia/Taipei")

VALID_ACTIONS = {"BUY", "SELL", "REBALANCE_BUY", "REBALANCE_SELL", "HOLD"}
WEIGHT_SUM_CAP = 0.953


def load_holidays():
    try:
        h = json.load(open(HOL_FILE, encoding="utf-8"))
        s = set()
        for v in h.values():
            s.update(v)
        return s
    except Exception:
        return set()


def is_trading(d, hols):
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in hols


def last_trading_day_of_week(today, hols):
    if is_trading(today, hols) and not is_trading(today + datetime.timedelta(days=1), hols):
        return today
    d = today
    while d.weekday() < 6:
        d += datetime.timedelta(days=1)
        if is_trading(d, hols) and not is_trading(d + datetime.timedelta(days=1), hols):
            return d
    d = today
    for _ in range(10):
        d -= datetime.timedelta(days=1)
        if is_trading(d, hols) and not is_trading(d + datetime.timedelta(days=1), hols):
            return d
    return today


def fingerprint(sig):
    rows = []
    for a in sorted(sig.get("actions", []),
                    key=lambda x: (str(x.get("channel", "")), str(x.get("stock_code", "")))):
        rows.append("|".join(str(x) for x in [
            a.get("stock_code"), a.get("action"), a.get("channel"), a.get("rank"),
            round(float(a.get("target_weight", 0) or 0), 6),
            round(float(a.get("current_weight", 0) or 0), 6),
        ]))
    raw = str(sig.get("rebalance_date", "?")) + "#" + ";".join(rows)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _tg_alert(text):
    tok = os.environ.get("TG_TOKEN")
    cid = os.environ.get("TG_CHAT_ID")
    if not tok or not cid:
        print("[gate] 無 TG env，僅記錄：", text)
        return
    if os.environ.get("DRY_RUN") == "1":
        print("[gate][DRY_RUN] TG 警報：\n", text)
        return
    try:
        payload = {"chat_id": str(cid), "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context())
    except Exception as e:
        print("[gate] TG 警報發送失敗：", e)


def evaluate(force=False, today=None):
    today = today or datetime.datetime.now(TW).date()
    hols = load_holidays()
    expect = last_trading_day_of_week(today, hols).strftime("%Y-%m-%d")
    reasons = []
    sig = None
    val = None

    if not os.path.exists(SIG_FILE):
        reasons.append("output/latest_signals.json 不存在（B3 引擎尚未產生結果）")
    else:
        try:
            sig = json.load(open(SIG_FILE, encoding="utf-8"))
        except Exception as e:
            reasons.append(f"latest_signals.json 無法解析：{e}")
    if not os.path.exists(VAL_FILE):
        reasons.append("output/validation_log.json 不存在")
    else:
        try:
            val = json.load(open(VAL_FILE, encoding="utf-8"))
        except Exception as e:
            reasons.append(f"validation_log.json 無法解析：{e}")

    if sig is not None:
        if val is not None:
            if val.get("verdict") != "PASS":
                reasons.append(f"validation verdict={val.get('verdict')}（非 PASS）")
            if val.get("strategy_version") != "B3":
                reasons.append("validation strategy_version 非 B3")
            vr = val.get("rebalance_date")
            sr = sig.get("rebalance_date")
            if vr and sr and vr != sr:
                reasons.append(f"validation rebalance_date={vr} 與 canonical={sr} 不一致")
        if sig.get("strategy_version") not in (None, "B3"):
            reasons.append(f"canonical strategy_version={sig.get('strategy_version')} 非 B3")

        rebal = sig.get("rebalance_date")
        if not force and rebal != expect:
            reasons.append(f"canonical 非本週最新（rebalance_date={rebal}，本次最後交易日應為 {expect}）")

        acts = sig.get("actions", [])
        codes = [str(a.get("stock_code")) for a in acts]
        if len(codes) != len(set(codes)):
            reasons.append("actions 有重複 stock_code")
        bad = sorted({str(a.get("action")) for a in acts if a.get("action") not in VALID_ACTIONS})
        if bad:
            reasons.append("出現非法 action：" + ",".join(bad))
        total_counts = sum(int((sig.get("counts", {}) or {}).get(k, 0) or 0)
                           for k in VALID_ACTIONS)
        if total_counts != len(acts):
            reasons.append(f"counts 合計 {total_counts} 與 actions 筆數 {len(acts)} 不符")
        tw = sum(float(a.get("target_weight", 0) or 0) for a in acts)
        if tw > WEIGHT_SUM_CAP:
            reasons.append(f"target_weight 合計 {tw:.4f} 超過 {WEIGHT_SUM_CAP}")
        miss = [str(a.get("stock_code")) for a in acts
                if a.get("action") != "SELL" and not (float(a.get("price", 0) or 0) > 0)]
        if miss:
            reasons.append("持股缺 price：" + ",".join(sorted(set(miss))[:10]))

    ok = not reasons
    result = {
        "ok": ok,
        "reason": "PASS" if ok else "；".join(reasons),
        "rebalance_date": (sig or {}).get("rebalance_date"),
        "canonical_hash": fingerprint(sig) if sig is not None else None,
        "expected_rebalance_date": expect,
        "forced": bool(force),
    }
    if not ok:
        _tg_alert(
            "❌ <b>B3 調倉資料未通過發布檢查</b>\n"
            + result["reason"]
            + "\n\n本次<b>不更新網頁、不發送一般調倉訊號</b>，請人工確認後再以 full 補跑。")
    return result


if __name__ == "__main__":
    force = os.environ.get("FORCE_FULL") == "1"
    r = evaluate(force=force)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(0 if r["ok"] else 1)
