# -*- coding: utf-8 -*-
"""V5 發布閘門（管線安全層；不計算策略、不修改 v5_canonical.py）。

在「產生網頁 (update_data.py)」與「發送 TG (tg_push_v5.py)」之前執行，
確保只會發布「本週最新、validation PASS、結構正確」的同一份 canonical：

  1) output/validation_log.json：verdict == PASS、strategy_version == V5、
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
import math
from types import SimpleNamespace
from collections import Counter
import urllib.request
from zoneinfo import ZoneInfo
from version import STRATEGY_VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
SIG_FILE = os.path.join(HERE, "output", "latest_signals.json")
VAL_FILE = os.path.join(HERE, "output", "validation_log.json")
HOL_FILE = os.path.join(HERE, "holidays.json")
TW = ZoneInfo("Asia/Taipei")

VALID_ACTIONS = {"BUY", "SELL", "REBALANCE_BUY", "REBALANCE_SELL", "HOLD"}
WEIGHT_SUM_CAP = 0.953   # 與 v5_canonical 容差一致：>0.953 才視為超額


def load_holidays():
    """單一假日來源 holidays.json（與 check_and_run 共用；不再於各程式寫死）。"""
    try:
        with open(HOL_FILE, encoding="utf-8") as source:
            h = json.load(source)
        year = str(datetime.datetime.now(TW).year)
        if year not in h:
            raise ValueError(f"缺少 {year} 年交易所休市表")
        s = set()
        for v in h.values():
            s.update(v)
        return s
    except Exception as error:
        raise RuntimeError("交易日設定無法載入，停止發布：" + str(error)) from error


def _load_json(p, default=None):
    try:
        with open(p, encoding="utf-8-sig") as source:
            d = json.load(source)
        return d if isinstance(d, dict) else (default or {})
    except Exception:
        return default or {}


def is_trading(d, hols):
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in hols


def last_trading_day_of_week(today, hols):
    """回傳 today 所屬那一週的最後交易日（週五休市→提前週四，連假逐日前移）。

    06:00 在最後交易日當天執行時，today 本身即最後交易日。
    """
    monday = today - datetime.timedelta(days=today.weekday())
    for offset in range(4, -1, -1):
        day = monday + datetime.timedelta(days=offset)
        if is_trading(day, hols):
            return day
    return None


def prev_trading_day_str(d, hols):
    """回傳 d（"YYYY-MM-DD"）的前一交易日（trading 判定，休市/假日往前掃）。

    資料新鮮度基準：調倉日（本週最後交易日）當天 06:00 跑，資料庫應已含「前一交易日」收盤。
    """
    dt = datetime.date.fromisoformat(d) if isinstance(d, str) else d
    for _ in range(20):
        dt -= datetime.timedelta(days=1)
        if is_trading(dt, hols):
            return dt.strftime("%Y-%m-%d")
    return d


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
            a.get("current_shares"), a.get("post_shares"), a.get("target_shares"),
            a.get("buy_shares"), a.get("sell_shares"), a.get("price"),
        ]))
    raw = str(sig.get("release_version", sig.get("strategy_version", "?"))) + "#" + str(sig.get("rebalance_date", "?")) + "#" + ";".join(rows)
    # Delivery changes must reach Web/TG even when this week's orders are unchanged.
    pending=sorted(sig.get('pending_assets',[]),key=lambda row:(str(row.get('code')),str(row.get('ex_date'))))
    raw += '#' + json.dumps(pending,ensure_ascii=False,sort_keys=True,separators=(',',':'))
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


def documented_upper_trade_block(signal, validation):
    """Only a verified, still-held trading block can explain excess exposure."""
    declared = {str(c) for c in signal.get('exposure_cap_blocked_codes', [])}
    held = {str(a.get('stock_code')) for a in signal.get('actions', []) if a.get('post_shares', 0) > 0}
    if not declared or not held or not held.issubset(declared):
        return False
    for detail in (validation or {}).get('checks', {}).get('exposure_upper_bound', {}).get('detail', []):
        if (detail.get('date') == signal.get('rebalance_date') and detail.get('justified') is True
                and {str(c) for c in detail.get('blocked_codes', [])} == declared
                and abs(float(detail.get('exposure', -1)) - float(signal.get('stock_exposure', 0))) <= .000050001):
            return True
    return False


def evaluate(force=False, today=None, output_dir=None, notify=True):
    today = today or datetime.datetime.now(TW).date()
    output_dir = output_dir or os.path.join(HERE, "output")
    sig_file = os.path.join(output_dir, "latest_signals.json")
    val_file = os.path.join(output_dir, "validation_log.json")
    hols = load_holidays()
    last_day = last_trading_day_of_week(today, hols)
    expect = last_day.isoformat() if last_day else None
    reasons = []
    if expect is None:
        reasons.append("本週沒有交易日，不發布調倉訊號")
    sig = None
    val = None

    if not os.path.exists(sig_file):
        reasons.append("output/latest_signals.json 不存在（V5 引擎尚未產生結果）")
    else:
        try:
            with open(sig_file, encoding="utf-8") as source:
                sig = json.load(source)
        except Exception as e:
            reasons.append(f"latest_signals.json 無法解析：{e}")
    if not os.path.exists(val_file):
        reasons.append("output/validation_log.json 不存在")
    else:
        try:
            with open(val_file, encoding="utf-8") as source:
                val = json.load(source)
        except Exception as e:
            reasons.append(f"validation_log.json 無法解析：{e}")

    if sig is not None and not isinstance(sig, dict):
        reasons.append("canonical 必須是 JSON 物件")
        sig = None
    if val is not None and not isinstance(val, dict):
        reasons.append("validation 必須是 JSON 物件")
        val = None
    if sig is not None:
        from v5_engine.security_status import snapshot_hash, can_trade
        security = _load_json(os.path.join(output_dir,'security_status.json'))
        if (not security or security.get('checked_for') != sig.get('rebalance_date')
                or sig.get('security_status_checked_for') != sig.get('rebalance_date')
                or sig.get('security_status_hash') != snapshot_hash(security)):
            reasons.append('缺少一致的本期官方交易狀態核對資料')
        else:
            status_ctx=SimpleNamespace(security_status=security)
            blocked=[str(a.get('stock_code')) for a in sig.get('actions',[])
                     if ((a.get('allocation_eligible',True) and a.get('action')!='SELL' or a.get('sell_shares',0)>0)
                         and not can_trade(status_ctx,a.get('stock_code'),sig['rebalance_date']))]
            if blocked:reasons.append('官方交易狀態不允許配置：'+','.join(blocked))
        # --- validation 把關 ---
        if val is not None:
            if val.get("verdict") != "PASS":
                reasons.append(f"validation verdict={val.get('verdict')}（非 PASS）")
            if val.get("strategy_version") != STRATEGY_VERSION:
                reasons.append("validation strategy_version 非 V5")
            vr = val.get("rebalance_date")
            sr = sig.get("rebalance_date")
            if not vr or not sr or vr != sr:
                reasons.append(f"validation rebalance_date={vr} 與 canonical={sr} 不一致")
        if sig.get("strategy_version") != STRATEGY_VERSION:
            reasons.append(f"canonical strategy_version={sig.get('strategy_version')} 非 V5")

        # 已發布的週計畫以產生時的版本為準；之後升版不重算本週，下次排程才用新版產生。
        if not sig.get('release_version') or sig.get('release_version') != (val or {}).get('release_version'):
            reasons.append('訊號與驗證程式版本不一致')
        # --- 資料新鮮度（不分 FORCE，永遠生效）---
        # 1) 資料必須已含「本週最後交易日」：rebal_date 必須 == 本週最後交易日
        #    （資料庫缺最後交易日收盤 → 擋，連 FORCE 補跑亦不發布，避免用過期資料出訊號）
        rebal = sig.get("rebalance_date")
        if rebal != expect:
            reasons.append(f"canonical 非本週最新（rebalance_date={rebal}，本次最後交易日應為 {expect}；缺資料，連 FORCE 亦不發布）")
        # 2) 資料庫連續：sig_date 必須 == rebal 的前一交易日（無缺日）
        sigd = sig.get("sig_date")
        if not sigd:
            reasons.append("canonical 缺訊號基準日")
        elif rebal:
            expect_sig = prev_trading_day_str(rebal, hols)
            if sigd != expect_sig:
                reasons.append(f"資料不連續：sig_date={sigd}，應 = {rebal} 的前一交易日 {expect_sig}")

        # --- 結構檢查 ---
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
        actual_counts = Counter(a.get("action") for a in acts)
        if any(int(sig.get("counts", {}).get(k, 0)) != actual_counts[k] for k in VALID_ACTIONS):
            reasons.append("counts 各動作筆數與 actions 不符")
        for a in acts:
            if a.get('allocation_eligible') is False and (float(a.get('target_weight', 0) or 0) > 0 or int(a.get('target_shares', 0) or 0) > 0):
                reasons.append(f"{a.get('stock_code')} 不可配置的歷史持股仍帶下單目標")
            if a.get('trading_status', 'TRADABLE') != 'TRADABLE' and a.get('allocation_eligible') is True:
                reasons.append(f"{a.get('stock_code')} 交易受限卻標記為可配置")
            for key in ("target_weight", "current_weight", "price"):
                value = a.get(key)
                if value is not None and not math.isfinite(float(value)):
                    reasons.append(f"{a.get('stock_code')} {key} 非有限數值")
        if sig.get("signal_mode") == "PRE_CLOSE_PLAN" and sig.get("price_basis_date") != sigd:
            reasons.append("收盤調倉計畫的參考價格日與訊號基準日不一致")
        tw = sum(float(a.get("target_weight", 0) or 0) for a in acts)
        blocked_cap = documented_upper_trade_block(sig, val)
        exposure = sig.get('stock_exposure')
        if exposure is not None and (not math.isfinite(float(exposure)) or (float(exposure) > .95 + 1e-10 and not blocked_cap)):
            reasons.append('股票倉位超過95%上限，且沒有已驗證的成交限制')
        if tw > WEIGHT_SUM_CAP and not blocked_cap:
            reasons.append(f"target_weight 合計 {tw:.4f} 超過 {WEIGHT_SUM_CAP}")
        if not sig.get("generated_at") or not sig.get("commit_sha"):
            reasons.append("canonical 缺 generated_at/commit_sha（版本資訊不完整，不發布）")
        miss = [str(a.get("stock_code")) for a in acts
                if a.get("action") != "SELL" and not (float(a.get("price", 0) or 0) > 0)]
        if miss:
            reasons.append("持股缺 price：" + ",".join(sorted(set(miss))[:10]))

        # --- 基準常駐化（第4關）：全歷史基準 + 當週回歸基準（讀/建/比對，隨週更新）---
        REG_PATH = os.path.join(output_dir, "regression_history.json")
        BASE_PATH = os.path.join(output_dir, "baseline.json")
        reg_hist = _load_json(REG_PATH)
        baseline = _load_json(BASE_PATH)
        rd = sig.get("rebalance_date")
        reg_key = (str(sig.get('release_version')) + '|' + rd + ('|PRE_CLOSE_PLAN' if sig.get('signal_mode') == 'PRE_CLOSE_PLAN' else '')) if rd else None

        # 全歷史基準：perf.json 的 cagr/mdd；漂移 >1.0pp → FAIL；0.1~1.0（資料演進）→ 自動更新；無檔 → 建立
        perf = _load_json(os.path.join(output_dir, "perf.json"))
        if not perf:
            reasons.append("缺少有效的歷史績效資料")
        from v5_engine.jsonio import validation_matches
        if not validation_matches(sig,val,perf):
            reasons.append('驗證檔未綁定本次完整訊號與績效，停止發布')
        if sig.get("signal_mode") == "PRE_CLOSE_PLAN" and (not perf.get("completed_through") or perf["completed_through"] >= rd):
            reasons.append("歷史績效必須截止於本週計畫之前的已完成交易週")
        if perf and perf.get('release_version') != sig.get('release_version'):
            reasons.append('績效與訊號版本不一致')
        if perf:
            anchor = perf.get("fixed_anchor") or perf
            cagr_now = float(anchor.get("cagr") or 0) * 100
            mdd_now = float(anchor.get("mdd") or 0) * 100
            anchor_date = anchor.get("date")
            if anchor_date and baseline.get("anchor_date") != anchor_date:
                baseline = {"cagr": cagr_now, "mdd": mdd_now, "anchor_date": anchor_date}
            if not math.isfinite(cagr_now) or not math.isfinite(mdd_now):
                reasons.append("歷史績效包含非有限數值")
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

        # 當週回歸基準：key=rebalance_date；首跑建立、同週重跑比對；10-02 使用新成本口徑鎖定值
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
            if reg_key in reg_hist:
                exp = reg_hist[reg_key]
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
                reg_hist[reg_key] = this_base   # 分開保存收盤前計畫與收盤模擬結果

    ok = not reasons
    result = {
        "ok": ok,
        "reason": "PASS" if ok else "；".join(reasons),
        "rebalance_date": (sig or {}).get("rebalance_date"),
        "canonical_hash": fingerprint(sig) if sig is not None else None,
        "expected_rebalance_date": expect,
        "forced": bool(force),
    }
    if not ok and notify:
        _tg_alert(
            "❌ <b>V5 調倉資料未通過發布檢查</b>\n"
            + result["reason"]
            + "\n\n本次<b>不更新網頁、不發送一般調倉訊號</b>，請人工確認後再以 full 補跑。")
    elif ok:
        # 全部通過 → 固化/更新基準（regression_history.json + baseline.json），供下次同週重跑比對
        try:
            os.makedirs(output_dir, exist_ok=True)
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
