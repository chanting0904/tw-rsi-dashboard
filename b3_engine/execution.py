"""Execute one simulated rebalance: exits, trims, buys, then exposure repair."""
import numpy as np
import pandas as pd
from .selection import select_universe


def execute_week(ctx, positions, cash, day, mode, coefs, limit_up, limit_dn, record=True):
    pos, wk = positions, day
    close, open_, ma60 = ctx.close, ctx.open_, ctx.ma60
    cfg = ctx.config
    SELL_FEE, BUY_FEE = cfg.sell_fee, cfg.buy_fee
    THRESH, BUFFER = cfg.threshold, cfg.buffer
    EXPOSURE_MIN, EXPOSURE_MAX, EXPOSURE_AIM = cfg.exposure_min, cfg.exposure_max, cfg.exposure_aim
    _c1, _c2, _c3 = coefs
    def coef_of(rank):
        return _c1 if rank <= 5 else (_c2 if rank <= 10 else _c3)
    px = open_.loc[wk] if mode == 'open' else close.loc[wk]
    want, want_rank, want_set, a_w, b_w = select_universe(ctx, pos, wk)
    trades, weekly_plans = [], []
    # ---- 賣出：完全掉出兩通道者（原 Exit）----
    blocked_exit_codes = []
    for c in list(pos.keys()):
        if c not in want_set:
            p_ = float(px[c])
            if np.isnan(p_) or p_ <= 0:
                trades.append({"date": wk, "code": c, "pool": pos[c]["pool"], "side": "SELL",
                               "price": 0.0, "shares": pos[c]["shares"], "amount": 0.0,
                               "reason": "DELISTING", "full": True, "is_new": False})
                del pos[c]
                continue
            if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                blocked_exit_codes.append(str(c))
                continue
            reason = "FALL_OUT_TOP_POOL"
            if c in ma60.columns and c in close.columns:
                _m = ma60[c].get(wk); _cl = close[c].get(wk)
                if pd.notna(_m) and pd.notna(_cl) and _cl < _m:
                    reason = "BREAK_MA60"
            _ed = pos[c].get("entry_d")
            if _ed is not None and _ed in close.index and wk in close.index:
                nd_ = len(close.loc[_ed:wk]) - 1
                if nd_ >= 80:
                    reason = "MAX_HOLDING_80D"
            sh = pos[c]["shares"]
            cash += sh * p_ * (1 - SELL_FEE)
            trades.append({"date": wk, "code": c, "pool": pos[c]["pool"], "side": "SELL",
                           "price": p_, "shares": sh, "amount": sh * p_,
                           "reason": reason, "full": True, "is_new": False})
            del pos[c]

    def mv_at(p):
        return sum(pos[c]["shares"] * float(p[c])
                   for c in pos if pd.notna(p[c]) and float(p[c]) > 0)
    pre_sh = {c: pos[c]["shares"] for c in pos}
    pre_total = cash + mv_at(px)
    total = pre_total
    target_lists = {pool: list(want.get(pool, [])) for pool in ("A", "B")}
    available_lists = {
        pool: [c for c in codes if c in px.index and pd.notna(px[c]) and float(px[c]) > 0
               and (c in pos or c not in limit_up.columns or not bool(limit_up[c].get(wk, False)))]
        for pool, codes in target_lists.items()
    }
    missing_price_targets = sum(1 for codes in target_lists.values() for c in codes
                                if c not in px.index or pd.isna(px[c]) or float(px[c]) <= 0)
    blocked_limitup_targets = sum(1 for pool, codes in target_lists.items() for c in codes
                                  if c in px.index and pd.notna(px[c]) and float(px[c]) > 0
                                  and c not in pos and c in limit_up.columns
                                  and bool(limit_up[c].get(wk, False)))
    alloc_weights = {"A": a_w, "B": b_w}
    if alloc_weights["A"] > 0 and not available_lists["A"] and available_lists["B"]:
        alloc_weights["B"] += alloc_weights["A"]
        alloc_weights["A"] = 0.0
    if alloc_weights["B"] > 0 and not available_lists["B"] and available_lists["A"]:
        alloc_weights["A"] += alloc_weights["B"]
        alloc_weights["B"] = 0.0
    last_tv_pool = {}
    rebalance_plans = []
    for pool, wgt in [("A", alloc_weights["A"]), ("B", alloc_weights["B"])]:
        lst = available_lists[pool]
        if not lst or wgt <= 0:
            continue
        if pool == "B" or (_c1 == 1.0 and _c3 == 1.0):
            tv_list = {c: total * wgt * (1 - BUFFER) / len(lst) for c in lst}
        else:
            ccoef = {}
            for code2 in lst:
                if code2 in pos and pos[code2].get("wc"):
                    ccoef[code2] = pos[code2]["wc"]
                else:
                    ccoef[code2] = coef_of(want_rank.get(code2, 8))
            sumc = sum(ccoef.values())
            pb = total * wgt * (1 - BUFFER)
            tv_list = {c: pb * ccoef[c] / sumc for c in lst}
        last_tv_pool[pool] = tv_list
        for c in lst:
            tv_ = tv_list[c]
            p_ = float(px[c])
            if np.isnan(p_) or p_ <= 0:
                continue
            cur = pos.get(c)
            cur_sh = cur["shares"] if cur else 0
            diff = tv_ - cur_sh * p_
            if cur:
                cur["pool"] = pool
            rebalance_plans.append((pool, c, tv_, p_, diff))

    # Execute all overweight trims before any additions. This makes sale proceeds
    # available to the buy pass on the same rebalance date.
    for pool, c, tv_, p_, diff in rebalance_plans:
        cur = pos.get(c)
        if not cur or diff >= -p_ or abs(diff) <= tv_ * THRESH:
            continue
        if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
            if str(c) not in blocked_exit_codes:
                blocked_exit_codes.append(str(c))
            continue
        sh = min(int((-diff) // p_), cur["shares"])
        if sh <= 0:
            continue
        cash += sh * p_ * (1 - SELL_FEE)
        cur["shares"] -= sh
        full = cur["shares"] == 0
        if full:
            del pos[c]
        trades.append({"date": wk, "code": c, "pool": pool, "side": "SELL",
                       "price": p_, "shares": sh, "amount": sh * p_,
                       "reason": "REBALANCE_DOWN", "full": full, "is_new": False})

    # Predict exposure after ordinary target buys. If that would exceed 95%,
    # trim overweight holdings to their assigned target before executing buys.
    projected_cash = cash
    projected_mv = mv_at(px)
    for pool, c, tv_, p_, _ in rebalance_plans:
        cur = pos.get(c)
        cur_sh = cur["shares"] if cur else 0
        diff = tv_ - cur_sh * p_
        if diff <= p_ or (cur and abs(diff) <= tv_ * THRESH):
            continue
        if c in limit_up.columns and bool(limit_up[c].get(wk, False)):
            continue
        sh = min(int(diff // p_), int(projected_cash // (p_ * (1 + BUY_FEE))))
        projected_cash -= sh * p_ * (1 + BUY_FEE)
        projected_mv += sh * p_
    projected_nav = projected_cash + projected_mv
    if projected_nav > 0 and projected_mv / projected_nav > EXPOSURE_MAX:
        for pool, c, tv_, p_, _ in rebalance_plans:
            if c not in pos or p_ <= 0:
                continue
            if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                if str(c) not in blocked_exit_codes:
                    blocked_exit_codes.append(str(c))
                continue
            target_sh = int(tv_ // p_)
            excess_sh = max(0, pos[c]["shares"] - target_sh)
            if excess_sh <= 0:
                continue
            projected_nav = projected_cash + projected_mv
            if projected_nav <= 0 or projected_mv / projected_nav <= EXPOSURE_MAX:
                break
            need = max(0.0, (projected_mv - EXPOSURE_MAX * projected_nav) /
                       (1 - EXPOSURE_MAX * SELL_FEE))
            sh = min(excess_sh, max(1, int(np.ceil(need / p_))))
            cash += sh * p_ * (1 - SELL_FEE)
            projected_cash += sh * p_ * (1 - SELL_FEE)
            pos[c]["shares"] -= sh
            projected_mv -= sh * p_
            full = pos[c]["shares"] == 0
            if full:
                del pos[c]
            trades.append({"date": wk, "code": c, "pool": pool, "side": "SELL",
                           "price": p_, "shares": sh, "amount": sh * p_,
                           "reason": "PORTFOLIO_BAND_TRIM", "full": full, "is_new": False})

    # Buy to target only when the existing 25% drift rule says to trade.
    # Account for the declared buy commission both in cash availability and NAV.
    for pool, c, tv_, p_, _ in rebalance_plans:
        cur = pos.get(c)
        cur_sh = cur["shares"] if cur else 0
        diff = tv_ - cur_sh * p_
        if diff <= p_ or (cur and abs(diff) <= tv_ * THRESH):
            continue
        if c in limit_up.columns and bool(limit_up[c].get(wk, False)):
            continue
        sh = min(int(diff // p_), int(cash // (p_ * (1 + BUY_FEE))))
        if sh <= 0:
            continue
        cash -= sh * p_ * (1 + BUY_FEE)
        if cur:
            cur["shares"] = cur_sh + sh
            cur["avg"] = (cur_sh * cur["avg"] + sh * p_ * (1 + BUY_FEE)) / (cur_sh + sh)
        else:
            pos[c] = {"shares": sh, "avg": p_ * (1 + BUY_FEE), "pool": pool,
                      "wc": coef_of(want_rank.get(c, 8)), "rank": want_rank.get(c, 8),
                      "entry_d": wk}
        trades.append({"date": wk, "code": c, "pool": pool, "side": "BUY",
                       "price": p_, "shares": sh, "amount": sh * p_,
                       "rank": want_rank.get(c, 8), "is_new": not bool(cur),
                       "reason": ""})

    # The per-stock 25% drift rule can leave too much cash across many names.
    # Only when aggregate exposure falls below 90%, top up eligible targets in
    # strategy order toward 92.5%, never beyond their assigned target weights.
    mv_now = mv_at(px)
    nav_now = cash + mv_now
    if nav_now > 0 and mv_now / nav_now < EXPOSURE_MIN:
        for pool, c, tv_, p_, _ in rebalance_plans:
            if p_ <= 0:
                continue
            if c in limit_up.columns and bool(limit_up[c].get(wk, False)):
                continue
            cur = pos.get(c)
            cur_sh = cur["shares"] if cur else 0
            target_sh = int(tv_ // p_)
            gap_sh = max(0, target_sh - cur_sh)
            if gap_sh <= 0:
                continue
            mv_now = mv_at(px)
            nav_now = cash + mv_now
            need = max(0.0, (EXPOSURE_AIM * nav_now - mv_now) /
                       (1 + EXPOSURE_AIM * BUY_FEE))
            sh = min(gap_sh, int(need // p_), int(cash // (p_ * (1 + BUY_FEE))))
            if sh <= 0:
                continue
            cash -= sh * p_ * (1 + BUY_FEE)
            if cur:
                cur["shares"] = cur_sh + sh
                cur["avg"] = (cur_sh * cur["avg"] + sh * p_ * (1 + BUY_FEE)) / (cur_sh + sh)
            else:
                pos[c] = {"shares": sh, "avg": p_ * (1 + BUY_FEE), "pool": pool,
                          "wc": coef_of(want_rank.get(c, 8)), "rank": want_rank.get(c, 8),
                          "entry_d": wk}
            trades.append({"date": wk, "code": c, "pool": pool, "side": "BUY",
                           "price": p_, "shares": sh, "amount": sh * p_,
                           "rank": want_rank.get(c, 8), "is_new": not bool(cur),
                           "reason": "PORTFOLIO_BAND_TOPUP"})
            mv_now = mv_at(px)
            nav_now = cash + mv_now
            if mv_now / nav_now >= EXPOSURE_AIM:
                break

    # ---- 每週調倉決策明細（實盤信號輸出用）----
    if record:
        total2 = cash + mv_at(px)
        plan_recs = []
        for pool, wgt in [("A", alloc_weights["A"]), ("B", alloc_weights["B"])]:
            lst = available_lists[pool]
            if not lst or wgt <= 0:
                continue
            if pool == "B" or (_c1 == 1.0 and _c3 == 1.0):
                tv_map = {c: total2 * wgt * (1 - BUFFER) / len(lst) for c in lst}
            else:
                ccoef2 = {}
                for code2 in lst:
                    if code2 in pos and pos[code2].get("wc"):
                        ccoef2[code2] = pos[code2]["wc"]
                    else:
                        ccoef2[code2] = coef_of(want_rank.get(code2, 8))
                sumc2 = sum(ccoef2.values())
                pb2 = total2 * wgt * (1 - BUFFER)
                tv_map = {c: pb2 * ccoef2[c] / sumc2 for c in lst}
            if pool in last_tv_pool:
                tv_map = last_tv_pool[pool]
            for c in lst:
                p_ = float(px[c])
                if np.isnan(p_) or p_ <= 0:
                    continue
                cur = pos.get(c)
                cur_sh = cur["shares"] if cur else 0
                pre_sh_ = pre_sh.get(c, 0)
                plan_recs.append({"date": wk, "pool": pool, "code": c,
                                  "rank": want_rank.get(c, None) if pool == "A" else None,
                                  "cur_sh": cur_sh, "target_sh": int(tv_map[c] // p_),
                                  "cur_val": cur_sh * p_, "target_val": tv_map[c],
                                  "pre_sh": pre_sh_, "pre_val": pre_sh_ * p_,
                                  "pre_total": pre_total, "nav": total2,
                                  "price": p_})
        exposure = mv_at(px) / total2 if total2 > 0 else 0.0
        planned_codes = {c for _, c, _, _, _ in rebalance_plans}
        unplanned_holdings = [
            {"code": str(c), "market_value": round(pos[c]["shares"] * float(px[c]), 0)}
            for c in pos if c not in planned_codes and c in px.index and pd.notna(px[c]) and float(px[c]) > 0
        ]
        for holding in unplanned_holdings:
            code = holding["code"]
            shares = pos[code]["shares"]
            price = float(px[code])
            before = pre_sh.get(code, shares)
            plan_recs.append({"date": wk, "pool": pos[code]["pool"], "code": code,
                              "rank": None, "cur_sh": shares, "target_sh": 0,
                              "cur_val": shares * price, "target_val": 0,
                              "pre_sh": before, "pre_val": before * price,
                              "pre_total": pre_total, "nav": total2, "price": price})
        weekly_plans.append({"date": wk, "cash": cash, "nav": total2, "recs": plan_recs,
                             "stock_exposure": exposure,
                             "exposure_in_band": EXPOSURE_MIN <= exposure <= EXPOSURE_MAX,
                             "missing_price_targets": missing_price_targets,
                             "blocked_limitup_targets": blocked_limitup_targets,
                             "blocked_exit_codes": blocked_exit_codes,
                             "unplanned_holdings": unplanned_holdings,
                             "wa_eff": alloc_weights["A"], "wb_eff": alloc_weights["B"]})


    if cash < -1e-7 or not np.isfinite(cash):
        raise ValueError('模擬現金超支或不是有效數字')
    if any(p['shares'] < 0 or int(p['shares']) != p['shares'] for p in pos.values()):
        raise ValueError('模擬持股數量無效')
    return cash, trades, weekly_plans[0] if weekly_plans else None
