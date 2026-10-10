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
  4) 基準常駐化：全歷史基準（baseline.json）+ 當週回歸基準（regression_history.json）
     ——首跑建立、同週重跑比對；漂移/不符 → FAIL，不發布。

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
WEIGHT_SUM_CAP = 0.953   # 與 b3_canonical 容差一致：>0.953 才視為超額


def load_holidays():
    """單一假日來源 holidays.json（與 check_and_run 共用；不再於各程式寫死）。"""
    try:
        h = json.load(open(HOL_FILE, encoding="utf-8"))
        s = set()
        for v in h.values():
            s.update(v)
        return s
    except Exception:
        return set()


def _load_json(p, default=None):
    try:
        d = json.load(open(p, encoding="utf-8-sig"))
        return d if isinstance(d, dict) else (default or {})
    except Exception:
        return default or {}


def is_trading(d, hols):
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in hols


def last_trading_day_of_week(today, hols):
    """回傳 today 所屬那一週的最後交易日（週五休市→提前週四，連假逐日前移）。

    06:00 在最後交易日當天執行時，today 本身即最後交易日。
    """
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
    """canonical 內容指紋：rebalance_date + 每檔 code/action/channel/rank/權重。

    用於 TG 推播去重；同一週同一組合只發一次。
    """
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

        # --- 基準常駐化（第4關）：全歷史基準 + 當週回歸基準（讀/建/比對，隨週更新）---
        REG_PATH = os.path.join(HERE, "output", "regression_history.json")
        BASE_PATH = os.path.join(HERE, "output", "baseline.json")
        reg_hist = _load_json(REG_PATH)
        baseline = _load_json(BASE_PATH)
        rd = sig.get("rebalance_date")

        perf = _load_json(os.path.join(HERE, "output", "perf.json"))
        if perf:
            cagr_now = float(perf.get("cagr") or 0) * 100
            mdd_now = float(perf.get("mdd") or 0) * 100
            if baseline:
                cd = abs(cagr_now - baseline.get("cagr", cagr_now))
                md = abs(mdd_now - baseline.get("mdd", mdd_now))
                if cd >= 1.0 or md >= 1.0:
                    reasons.append(f"全歷史基準漂移過大：CAGR 差 {cd:.2f}pp / MDD 差 {md:.2f}pp（>1.0pp，不發布）")
                elif cd > 0.1 or md > 0.1:
                    baseline["cagr"], baseline["mdd"] = cagr_now, mdd_now
                    baseline["updated"] = datetime.datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
            else:
                baseline = {"cagr": cagr_now, "mdd": mdd_now,
                            "updated": datetime.datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")}

        if rd:
            got = sig.get("counts", {})
            rank_w = {}
            for a in acts:
                if a.get("channel") == "A" and a.get("rank"):
                    rank_w[str(a["rank"])] = float(a.get("target_weight", 0) or 0)
            this_base = {
                "counts": {"BUY": got.get("BUY", 0), "SELL": got.get("SELL", 0),
                           "REBAL": got.get("REBALANCE_BUY", 0) + got.get("REBALANCE_SELL", 0),
                           "HOLD": got.get("HOLD", 0)},
                "buy_total": sig.get("buy_total", 0), "sell_total": sig.get("sell_total", 0),
                "cash": sig.get("cash", 0), "buffer": sig.get("buffer", 0),
                "rank_weights": rank_w,
            }
            if rd == "2026-10-02" and rd not in reg_hist:
                reg_hist[rd] = {"counts": {"BUY": 7, "SELL": 7, "REBAL": 3, "HOLD": 6},
                                "buy_total": 2266264, "sell_total": 2778669, "cash": 790409,
                                "buffer": 0.134, "rank_weights": {"2": 0.044, "6": 0.029, "15": 0.015}}
            if rd in reg_hist:
                exp = reg_hist[rd]
                ec = exp.get("counts", {})
                for k in ("BUY", "SELL", "REBAL", "HOLD"):
                    if k in ec and this_base["counts"][k] != ec[k]:
                        reasons.append(f"當週基準 {k} 期望 {ec[k]} 實際 {this_base['counts'][k]}（{rd}）")
                for nm_ in ("buy_total", "sell_total", "cash"):
                    if nm_ in exp and exp[nm_] and abs(this_base[nm_] - exp[nm_]) / exp[nm_] > 0.02:
                        reasons.append(f"當週基準 {nm_} 期望 {exp[nm_]:,.0f} 實際 {this_base[nm_]:,.0f}（{rd}）")
                if "buffer" in exp and abs(this_base["buffer"] - exp["buffer"]) > 0.006:
                    reasons.append(f"當週基準 buffer 期望 {exp['buffer']*100:.1f}% 實際 {this_base['buffer']*100:.1f}%")
                for rk, ev in exp.get("rank_weights", {}).items():
                    if rk in this_base["rank_weights"] and abs(this_base["rank_weights"][rk] - ev) > 0.003:
                        reasons.append(f"當週基準 Rank{rk} 權重期望 {ev*100:.1f}% 實際 {this_base['rank_weights'][rk]*100:.1f}%")
            else:
                reg_hist[rd] = this_base   # 首跑建立當週基準

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
    else:
        try:
            os.makedirs(os.path.join(HERE, "output"), exist_ok=True)
            with open(REG_PATH, "w", encoding="utf-8") as f:
                json.dump(reg_hist, f, ensure_ascii=False, indent=1)
            with open(BASE_PATH, "w", encoding="utf-8") as f:
                json.dump(baseline, f, ensure_ascii=False, indent=1)
            result["regression_baseline"] = "已固化"
        except Exception as e:
            print("[gate] 基準寫回失敗：", e)
    return result


if __name__ == "__main__":
    force = os.environ.get("FORCE_FULL") == "1"
    r = evaluate(force=force)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(0 if r["ok"] else 1)
