from version import STRATEGY_VERSION, RELEASE_VERSION
"""Serialize verified simulated positions and fills for Web/TG."""
import datetime
from zoneinfo import ZoneInfo

def classify_day(wp, trades_df):
    tdate = wp["date"]
    day_trades = trades_df[(trades_df["date"] == tdate) & trades_df["side"].isin(["BUY", "SELL"])]
    tm = {}
    for _, t in day_trades.iterrows():
        code = t["code"]
        if code in tm and tm[code]["side"] == t["side"]:
            merged = tm[code].copy()
            merged["shares"] = int(merged["shares"]) + int(t["shares"])
            merged["amount"] = float(merged["amount"]) + float(t["amount"])
            merged["full"] = bool(t.get("full", merged.get("full", False)))
            merged["is_new"] = bool(merged.get("is_new", False) or t.get("is_new", False))
            if t.get("reason"):
                merged["reason"] = t["reason"]
            tm[code] = merged
        else:
            tm[code] = t
    ops = []
    for rec in wp["recs"]:
        t = tm.get(rec["code"])
        pre_w = rec["pre_val"] / rec["pre_total"] if rec["pre_total"] else 0
        base = {**rec, "target_weight": rec["target_val"] / rec["nav"],
                "current_weight": pre_w, "weight_diff": rec["target_val"] / rec["nav"] - pre_w}
        if t is None:
            if rec["cur_sh"] > 0:
                ops.append({**base, "action": "HOLD", "trade_sh": 0, "amount": 0,
                            "reason": "交易受限，模擬持倉暫留" if rec["target_sh"] == 0 else ""})
        elif t["side"] == "BUY":
            ops.append({**base, "action": "BUY" if t["is_new"] else "REBALANCE_BUY",
                        "trade_sh": int(t["shares"]), "amount": float(t["amount"]),
                        "reason": t.get("reason", "")})
        else:
            ops.append({**base, "action": "SELL" if t["full"] else "REBALANCE_SELL",
                        "trade_sh": int(t["shares"]), "amount": float(t["amount"]),
                        "reason": t.get("reason", "")})
    for code, t in tm.items():
        if t["side"] == "SELL" and code not in {o["code"] for o in ops}:
            ops.append({"date": tdate, "pool": t["pool"], "code": code, "rank": None,
                        "cur_sh": 0, "target_sh": 0, "cur_val": 0, "target_val": 0,
                        "pre_sh": int(t["shares"]), "pre_val": float(t["amount"]),
                        "pre_total": wp["nav"], "nav": wp["nav"],
                        "price": float(t["price"]), "action": "SELL", "trade_sh": int(t["shares"]),
                        "amount": float(t["amount"]), "reason": t.get("reason", ""),
                        "target_weight": 0, "current_weight": 0, "weight_diff": 0})
    return ops


def prev_trading_day(ctx, d):
    close = ctx.close
    idx = close.dropna(how="all").index
    idx = idx[idx < d]
    return idx[-1] if len(idx) else d


def canonical_for(ctx, wp, trades_df, commit_sha):
    cfg = ctx.config
    EXPOSURE_MIN, EXPOSURE_MAX = cfg.exposure_min, cfg.exposure_max
    sw_tsm, LIVE_DAY, SIGNAL_DAY = ctx.sw_tsm, ctx.live_day, ctx.signal_day
    name_map = ctx.name_map or {}
    def nm(code):
        return name_map.get(code, code)
    ops = classify_day(wp, trades_df)
    acts = []
    for o in ops:
        buy_sh = sell_sh = 0
        buy_amt = sell_amt = 0
        a = o["action"]
        if a in ("BUY", "REBALANCE_BUY"):
            buy_sh, buy_amt = int(o["trade_sh"]), round(float(o["amount"]), 0)
        elif a in ("SELL", "REBALANCE_SELL"):
            sell_sh, sell_amt = int(o["trade_sh"]), round(float(o["amount"]), 0)
        acts.append({
            "stock_code": o["code"],
            "stock_name": nm(o["code"]),
            "channel": o["pool"],
            "rank": o.get("rank") if o.get("rank") is not None else None,
            "action": a,
            "target_weight": round(float(o["target_weight"]), 5),
            "strategy_target_weight": round(float(o["target_weight"]), 5),
            "current_weight": round(float(o["current_weight"]), 5),
            "weight_diff": round(float(o["weight_diff"]), 5),
            "target_shares": int(o["target_sh"]),
            "strategy_target_shares": int(o["target_sh"]),
            "current_shares": int(o["pre_sh"]),
            "post_shares": int(o["cur_sh"]),
            "post_weight": round(float(o["cur_val"]) / float(wp["nav"]), 5),
            "buy_shares": buy_sh,
            "sell_shares": sell_sh,
            "estimated_buy_amount": buy_amt,
            "estimated_sell_amount": sell_amt,
            "price": round(float(o["price"]), 2) if o["price"] and o["price"] > 0 else None,
            "reason": o.get("reason", ""),
        })
        # Web/TG recommend the executed model position, including HOLD drift.
        # Recommending raw ranking targets would undo the reduced-rebalance rule.
        acts[-1]['target_weight'] = acts[-1]['post_weight']
        acts[-1]['target_shares'] = acts[-1]['post_shares']
        acts[-1]['weight_diff'] = round(acts[-1]['target_weight']-acts[-1]['current_weight'],5)
    counts = {"BUY": 0, "SELL": 0, "REBALANCE_BUY": 0, "REBALANCE_SELL": 0, "HOLD": 0}
    for a in acts:
        counts[a["action"]] += 1
    buy_total = sum(a["estimated_buy_amount"] for a in acts)
    sell_total = sum(a["estimated_sell_amount"] for a in acts)
    nb_b = len({a["stock_code"] for a in acts if a["channel"] == "B"})
    warnings = []
    base_a_w = 0.60 if sw_tsm.get(wp["date"], False) else 1.0
    base_b_w = 0.40 if sw_tsm.get(wp["date"], False) else 0.0
    exposure = float(wp.get("stock_exposure", 0.0))
    if not (EXPOSURE_MIN <= exposure <= EXPOSURE_MAX):
        warnings.append(f"調倉後持倉 {exposure*100:.1f}% 未落在 90–95% 目標區間；請檢查交易限制或可買目標股數")
    if wp.get("missing_price_targets", 0):
        warnings.append(f"有 {wp['missing_price_targets']} 檔目標股缺少有效價格，已把該通道權重重新分配給可計價標的")
    if wp.get("blocked_limitup_targets", 0):
        warnings.append(f"有 {wp['blocked_limitup_targets']} 檔新目標股漲停無法買進，其配置已分給其他可買標的")
    if wp.get("blocked_exit_codes"):
        blocked = sorted(set(wp["blocked_exit_codes"]))
        warnings.append(f"有 {len(blocked)} 檔持股因跌停或缺有效成交價無法賣出，仍暫留模擬持倉：{'、'.join(blocked)}")
    if wp.get('exposure_cap_blocked_codes'):
        codes = '、'.join(wp['exposure_cap_blocked_codes'])
        warnings.append(f"股票倉位暫時超過95%上限，受限持股無法成交：{codes}；已停止超額買進，待可成交時再降倉")
    if wp.get("unplanned_holdings"):
        codes = '、'.join(h['code'] for h in wp['unplanned_holdings'])
        warnings.append(f"有未列入本週目標的持股仍留在模擬帳戶：{codes}")
    if abs(float(wp.get("wa_eff", base_a_w)) - base_a_w) > 1e-9 or abs(float(wp.get("wb_eff", base_b_w)) - base_b_w) > 1e-9:
        warnings.append(f"通道配置因另一通道沒有可計價標的而調整：A {float(wp.get('wa_eff', base_a_w))*100:.0f}% / B {float(wp.get('wb_eff', base_b_w))*100:.0f}%")
    if 0 < nb_b < 4:
        warnings.append(f"B 通道僅 {nb_b} 檔，單檔權重放大至 95%×40%/{nb_b} ≈ {0.95*0.4/nb_b*100:.1f}%，集中度較高")
    if counts["BUY"] + counts["REBALANCE_BUY"] == 0 and counts["SELL"] + counts["REBALANCE_SELL"] == 0:
        warnings.append("本週無買賣動作，僅 HOLD")
    return {
        "strategy_version": STRATEGY_VERSION, "release_version": RELEASE_VERSION,
        "selection_rules": {"a_rank_days": cfg.a_rank_days, "a_entry_rank": cfg.max_hold_a,
                            "a_retention_rank": cfg.a_retention_rank, "b_max_names": cfg.max_hold_b},
        "start_capital": cfg.start_capital, "buy_fee": cfg.buy_fee, "sell_fee": cfg.sell_fee,
        "signal_mode": "PRE_CLOSE_PLAN" if LIVE_DAY is not None and wp["date"] == LIVE_DAY else "CLOSE_SIMULATION",
        "price_basis_date": (SIGNAL_DAY if LIVE_DAY is not None and wp["date"] == LIVE_DAY else wp["date"]).strftime("%Y-%m-%d"),
        "execution_rule": "last_trading_day_close_then_next_week_first_session_remainder",
        "calculation_date": wp["date"].strftime("%Y-%m-%d"),
        "rebalance_date": wp["date"].strftime("%Y-%m-%d"),
        "sig_date": prev_trading_day(ctx, wp["date"]).strftime("%Y-%m-%d"),
        "generated_at": datetime.datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "commit_sha": commit_sha,
        "tsm_on": bool(sw_tsm.get(wp["date"], False)),
        "wa_eff": round(float(wp.get("wa_eff", 0.60 if sw_tsm.get(wp["date"], False) else 1.0)), 3),
        "wb_eff": round(float(wp.get("wb_eff", 0.40 if sw_tsm.get(wp["date"], False) else 0.0)), 3),
        "receivables": round(float(wp.get("receivables", 0)), 2),
        "cash": round(float(wp["cash"]), 0),
        "buffer": round(float(wp["cash"]) / float(wp["nav"]), 4),
        "stock_exposure": round(exposure, 4),
        "exposure_cap_blocked_codes": list(wp.get('exposure_cap_blocked_codes', [])),
        "portfolio_value": round(float(wp["nav"]), 0),
        "buy_total": round(buy_total, 0),
        "sell_total": round(sell_total, 0),
        "counts": counts,
        "actions": acts,
        "warnings": warnings,
    }
