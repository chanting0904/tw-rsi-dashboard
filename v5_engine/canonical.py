from version import STRATEGY_VERSION, RELEASE_VERSION
"""Serialize verified simulated positions and fills for Web/TG."""
import datetime
import pandas as pd
from zoneinfo import ZoneInfo
from .recommendations import recommendation_metadata
from .security_status import snapshot_hash

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
    offset = close.index.searchsorted(d, side='left') - 1
    while offset >= 0:
        if close.iloc[offset].notna().any():
            return close.index[offset]
        offset -= 1
    return d


def canonical_for(ctx, wp, trades_df, commit_sha):
    cfg = ctx.config
    EXPOSURE_MIN, EXPOSURE_MAX = cfg.exposure_min, cfg.exposure_max
    LIVE_DAY, SIGNAL_DAY = ctx.live_day, ctx.signal_day
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
        if 'planner_target_weight' in o:
            acts[-1]['planner_target_weight'] = o['planner_target_weight']
            acts[-1]['strategy_target_weight'] = o['strategy_target_weight']
        acts[-1]['target_weight'] = acts[-1]['post_weight']
        acts[-1]['target_shares'] = acts[-1]['post_shares']
        acts[-1]['weight_diff'] = round(acts[-1]['target_weight']-acts[-1]['current_weight'],5)
        metadata = recommendation_metadata(ctx, wp, o)
        known_name = metadata.pop('known_name')
        if acts[-1]['stock_name'] == o['code'] and known_name:
            acts[-1]['stock_name'] = known_name
        acts[-1].update(metadata)
        from .security_status import can_trade
        frame = ctx.adjusted_close if ctx.adjusted_close is not None else ctx.close
        index = frame.index.get_loc(wp['date'])
        prior_price = frame.iloc[index-1].get(o['code']) if index else None
        current_price = frame.iloc[index].get(o['code'])
        change = current_price/prior_price-1 if prior_price is not None and prior_price>0 else 0.
        allowed = can_trade(ctx,o['code'],wp['date'])
        acts[-1]['buy_allowed'] = bool(allowed and not change>=cfg.limit)
        acts[-1]['sell_allowed'] = bool(allowed and not change<=-cfg.limit)
        if not metadata['allocation_eligible']:
            acts[-1]['target_weight'] = 0.0
            acts[-1]['target_shares'] = 0
            # A restriction is not a simulated fill or a recommendation to sell.
            acts[-1]['weight_diff'] = 0.0 if o['action'] == 'HOLD' else acts[-1]['weight_diff']
    counts = {"BUY": 0, "SELL": 0, "REBALANCE_BUY": 0, "REBALANCE_SELL": 0, "HOLD": 0}
    for a in acts:
        counts[a["action"]] += 1
    buy_total = sum(a["estimated_buy_amount"] for a in acts)
    sell_total = sum(a["estimated_sell_amount"] for a in acts)
    warnings = []
    delivery_status=dict(getattr(ctx,'share_delivery_status',None) or {})
    if delivery_status:
        pending_keys={(a['code'],a['ex_date']) for a in wp.get('pending_assets',[])}
        conflicts=delivery_status.get('conflicts',[])
        relevant=[a for a in conflicts if (a['code'],a['ex_date']) in pending_keys]
        delivery_status['unrelated_conflicts']=len(conflicts)-len(relevant)
        delivery_status['conflicts']=relevant
        delivery_status['status']='PASS' if all(s['ok'] for s in delivery_status.get('sources',[])) and not relevant else 'PARTIAL'
    if delivery_status.get('status')=='PARTIAL':
        warnings.append('配股公告自動核對有來源缺漏或日期衝突，未核實配股保持鎖定；詳見配股核對狀態。')
    exposure = float(wp.get("stock_exposure", 0.0))
    if not (EXPOSURE_MIN <= exposure <= EXPOSURE_MAX):
        warnings.append(f"調倉後持倉 {exposure*100:.1f}% 未落在 90–95% 目標區間；請檢查交易限制或可買目標股數")
    if wp.get("missing_price_targets", 0):
        warnings.append(f"有 {wp['missing_price_targets']} 檔目標股缺少有效價格，已把配置重新分配給可計價標的")
    blocked_new = wp.get('blocked_limitup_targets',0)
    if wp.get('execution_policy')=='guarded_weekly_capital_v1':
        blocked_new = wp.get('reference_blocked_limitup_targets',0)+sum(1 for a in acts if a.get('allocation_eligible') and not a.get('buy_allowed',True) and not a['current_shares'])
    if blocked_new:
        warnings.append(f"有 {blocked_new} 檔新目標股漲停無法買進，其配置已分給其他可買標的")
    if wp.get("blocked_exit_codes"):
        blocked = sorted(set(wp["blocked_exit_codes"]))
        warnings.append(f"有 {len(blocked)} 檔持股因停牌、跌停、缺報價或待入帳限制無法全部賣出，仍暫留模擬紀錄：{'、'.join(blocked)}")
    if wp.get('exposure_cap_blocked_codes'):
        codes = '、'.join(wp['exposure_cap_blocked_codes'])
        warnings.append(f"股票倉位暫時超過95%上限，受限持股無法成交：{codes}；已停止超額買進，待可成交時再降倉")
    if wp.get("unplanned_holdings"):
        codes = '、'.join(h['code'] for h in wp['unplanned_holdings'])
        warnings.append(f"有未列入本週目標的持股仍留在模擬帳戶：{codes}")
    if counts["BUY"] + counts["REBALANCE_BUY"] == 0 and counts["SELL"] + counts["REBALANCE_SELL"] == 0:
        warnings.append("本週無買賣動作，僅 HOLD")
    if wp.get('pending_assets'):
        unknown = sum(asset.get('tradable_date') is None for asset in wp['pending_assets'])
        warnings.append(f"待領配股共 {len(wp['pending_assets'])} 筆，{unknown} 筆日期待確認；目前不用另外下單，原有股票仍照策略操作。")
    if LIVE_DAY is not None and wp['date']==LIVE_DAY and getattr(ctx,'live_action_prices',None):
        warnings.append('本週部分股票遇除權息，已標示「除權息參考價」；實際成交價格仍以券商為準。')
    return {
        "strategy_version": STRATEGY_VERSION, "release_version": RELEASE_VERSION,
        "share_delivery_check": {k:delivery_status[k] for k in ['as_of','status','discovered','registered','conflicts'] if k in delivery_status},
        "restricted_assets": wp.get('restricted_assets', []),
        "pending_assets": [{**asset,'grant_ratio':(ctx.corporate_events.get(pd.Timestamp(asset['ex_date']),{}).get(asset['code'],{}).get('ratio'))} for asset in wp.get('pending_assets',[])],
        "stock_deliveries": [{"code":str(d['code']),"ex_date":str(d['ex_date'])[:10],"tradable_date":str(d['tradable_date'])[:10]} for d in (getattr(ctx,'share_delivery_events',[]) or [])
            if str(d.get('announced_on') or d.get('confirmed_on') or '')[:10]<=str(wp['date'].date()) and str((wp['date']-pd.Timedelta(days=7)).date())<str(d['tradable_date'])[:10]<=str(wp['date'].date())],
        "security_status_checked_for": (getattr(ctx,'security_status',None) or {}).get('checked_for'),
        "security_status_hash": snapshot_hash(ctx.security_status) if getattr(ctx,'security_status',None) else None,
        "execution_policy": wp.get("execution_policy", "legacy_v4"),
        "selection_mode": "single_ranked_pool",
        "selection_rules": {"a_rank_days": cfg.a_rank_days, "a_entry_rank": cfg.max_hold_a,
                            "a_retention_rank": cfg.a_retention_rank},
        "start_capital": cfg.start_capital, "buy_fee": cfg.buy_fee, "sell_fee": cfg.sell_fee,
        "minimum_commission": cfg.minimum_commission,
        "fee_model": {"commission_rate": cfg.buy_fee, "minimum_commission": cfg.minimum_commission,
                      "stock_sale_tax": cfg.sell_fee-cfg.buy_fee, "etf_sale_tax": .001,
                      "order_policy": "one_minimum_per_code_side_rebalance"},
        "signal_mode": "PRE_CLOSE_PLAN" if LIVE_DAY is not None and wp["date"] == LIVE_DAY else "CLOSE_SIMULATION",
        "price_basis_date": (SIGNAL_DAY if LIVE_DAY is not None and wp["date"] == LIVE_DAY else wp["date"]).strftime("%Y-%m-%d"),
        "execution_rule": "last_trading_day_close_then_next_week_first_session_remainder",
        "calculation_date": wp["date"].strftime("%Y-%m-%d"),
        "rebalance_date": wp["date"].strftime("%Y-%m-%d"),
        "sig_date": prev_trading_day(ctx, wp["date"]).strftime("%Y-%m-%d"),
        "generated_at": datetime.datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "commit_sha": commit_sha,
        "receivables": round(float(wp.get("receivables", 0)), 2),
        "cash": round(float(wp["cash"]), 0),
        "buffer": round(float(wp["cash"]) / float(wp["nav"]), 4),
        "stock_exposure": round(exposure, 4),
        "allocation_exposure": round(sum(a['post_weight'] for a in acts if a['allocation_eligible']), 5),
        "restricted_exposure": round(sum(a['post_weight'] for a in acts if not a['allocation_eligible']), 5),
        "exposure_cap_blocked_codes": list(wp.get('exposure_cap_blocked_codes', [])),
        "portfolio_value": round(float(wp["nav"]), 0),
        "capital_basis": wp.get("capital_basis",wp["nav"]),
        "buy_total": round(buy_total, 0),
        "sell_total": round(sell_total, 0),
        "counts": counts,
        "actions": acts,
        "warnings": warnings,
    }
