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
                blocked_exit_codes.append(str(c))
                continue
            if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                blocked_exit_codes.append(str(c))
                continue
            reason = "FALL_OUT_TOP_POOL"
            if c in ma60.columns and c in close.columns:
                _m = ma60[c].get(wk); _cl = (ctx.adjusted_close if ctx.adjusted_close is not None else close)[c].get(wk)
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
        marked = getattr(ctx, 'valuation_close', close).loc[wk]
        return sum(pos[c]["shares"] * float(marked[c])
                   for c in pos if pd.notna(marked[c]) and float(marked[c]) > 0)
    pre_sh = {c: pos[c]["shares"] for c in pos}
    receivable = float(getattr(ctx, "receivable_total", 0.))

    def maximum_buy_shares(price):
        # Buying reduces NAV by commission as well as increasing stock value:
        # (stocks + q*p) / (nav - q*p*fee) <= EXPOSURE_MAX.
        # Recompute after every fill; an earlier projection can become stale
        # when trims release more cash for the subsequent buy pass.
        stocks = mv_at(px)
        nav = cash + stocks + receivable
        headroom = max(0.0, EXPOSURE_MAX * nav - stocks)
        return max(0, int(np.floor(headroom / (price * (1 + EXPOSURE_MAX * BUY_FEE)))))

    pre_total = cash + mv_at(px) + receivable
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

    from .sizing import proportional_quantities, proportional_sales

    def pending_buys(band=False):
        requests = []
        for pool, c, target, price, _ in rebalance_plans:
            if price <= 0 or (c in limit_up.columns and bool(limit_up[c].get(wk, False))):
                continue
            cur = pos.get(c)
            current = cur['shares'] if cur else 0
            gap = target - current * price
            if gap < price and not band:
                continue
            if cur and not band and abs(gap) <= target * THRESH:
                continue
            qty = max(0, int(np.ceil(gap / price))) if band else max(0, int(np.floor(gap / price)))
            if qty:
                requests.append({'code': c, 'pool': pool, 'price': price, 'quantity': qty})
        return requests

    def buy_budget():
        stocks = mv_at(px)
        nav = cash + stocks + receivable
        headroom = max(0., EXPOSURE_MAX * nav - stocks) / (1 + EXPOSURE_MAX * BUY_FEE)
        return min(max(0., cash) / (1 + BUY_FEE), headroom)

    # Reserve the complete buy basket before any fill. Existing overweight
    # positions fund new targets proportionally, including the cost of sales.
    requests = pending_buys()
    demand = sum(row['quantity'] * row['price'] for row in requests)
    stocks = mv_at(px)
    nav = cash + stocks + receivable
    need_cap = max(0., (stocks + demand - EXPOSURE_MAX * (nav-demand*BUY_FEE)) / (1-EXPOSURE_MAX*SELL_FEE))
    need_cash = max(0., (demand*(1+BUY_FEE)-cash) / (1-SELL_FEE))
    required = max(need_cap, need_cash)
    if required > 0:
        excess = []
        for pool, c, target, price, _ in rebalance_plans:
            if c not in pos:
                continue
            if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                continue
            qty = max(0, int(np.floor((pos[c]['shares']*price-target)/price)))
            if qty:
                excess.append({'code':c, 'pool':pool, 'price':price, 'quantity':qty})
        quantities = proportional_sales(excess, required)
        for row in excess:
            c, price, qty = row['code'], row['price'], quantities[row['code']]
            if not qty:
                continue
            cash += qty*price*(1-SELL_FEE)
            pos[c]['shares'] -= qty
            full = pos[c]['shares'] == 0
            if full:
                del pos[c]
            trades.append({'date':wk,'code':c,'pool':row['pool'],'side':'SELL','price':price,
                'shares':qty,'amount':qty*price,'reason':'PORTFOLIO_BAND_FUNDING','full':full,'is_new':False})

    def fill_basket(requests, budget, reason):
        nonlocal cash
        quantities = proportional_quantities(requests, budget)
        count = 0
        for row in requests:
            c, price = row['code'], row['price']
            qty = quantities[c]
            if not qty:
                continue
            if qty > maximum_buy_shares(price):
                raise ValueError('Reserved buy basket exceeds actual fee-aware exposure budget')
            cur = pos.get(c)
            cash -= qty*price*(1+BUY_FEE)
            if cur:
                old = cur['shares']
                cur['shares'] += qty
                cur['avg'] = (old*cur['avg']+qty*price*(1+BUY_FEE))/(old+qty)
            else:
                pos[c] = {'shares':qty,'avg':price*(1+BUY_FEE),'pool':row['pool'],
                          'wc':coef_of(want_rank.get(c,8)),'rank':want_rank.get(c,8),'entry_d':wk}
            trades.append({'date':wk,'code':c,'pool':row['pool'],'side':'BUY','price':price,
                'shares':qty,'amount':qty*price,'rank':want_rank.get(c,8),'is_new':not bool(cur),'reason':reason})
            count += 1
        return count

    fill_basket(pending_buys(), buy_budget(), '')
    stocks = mv_at(px)
    nav = cash + stocks + receivable
    if nav > 0 and stocks/nav < EXPOSURE_MIN:
        # Round the whole basket together toward the existing 92.5% aim.
        # Never exceed a target by more than its next whole share.
        for _ in range(3):
            stocks = mv_at(px)
            nav = cash + stocks + receivable
            if nav <= 0 or stocks/nav >= EXPOSURE_AIM:
                break
            budget = min(buy_budget(), max(0., (EXPOSURE_AIM*nav-stocks)/(1+EXPOSURE_AIM*BUY_FEE)))
            if not fill_basket(pending_buys(band=True), budget, 'PORTFOLIO_BAND_TOPUP'):
                break

    # Reconcile actual filled positions, including holdings outside the target
    # universe. Prefer the largest excess over its assigned target. Never fake
    # a fill for a missing quote or a limit-down stock.
    assigned = {c: tv for pool, c, tv, price, diff in rebalance_plans}
    priority = sorted(list(pos), key=lambda c: (
        pos[c]["shares"] * float(px[c]) - assigned.get(c, 0.0)
        if c in px.index and pd.notna(px[c]) else -1e99), reverse=True)
    cap_blocked = []
    for c in priority:
        stocks = mv_at(px)
        nav = cash + stocks + receivable
        if nav <= 0 or stocks / nav <= EXPOSURE_MAX + 1e-12:
            break
        if c not in px.index or pd.isna(px[c]) or float(px[c]) <= 0:
            cap_blocked.append(c)
            continue
        if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
            cap_blocked.append(c)
            if str(c) not in blocked_exit_codes:
                blocked_exit_codes.append(str(c))
            continue
        price = float(px[c])
        # Selling also reduces NAV by sell fees, so proceeds alone are not the
        # amount needed to bring the stock fraction back under the ceiling.
        amount = max(0.0, (stocks - EXPOSURE_MAX * nav) / (1 - EXPOSURE_MAX * SELL_FEE))
        qty = min(pos[c]["shares"], max(1, int(np.ceil(amount / price))))
        cash += qty * price * (1 - SELL_FEE)
        pos[c]["shares"] -= qty
        full = pos[c]["shares"] == 0
        pool = pos[c]["pool"]
        if full:
            del pos[c]
        trades.append({"date": wk, "code": c, "pool": pool, "side": "SELL",
                       "price": price, "shares": qty, "amount": qty * price,
                       "reason": "PORTFOLIO_BAND_TRIM", "full": full, "is_new": False})
    stocks = mv_at(px)
    final_nav = cash + stocks + receivable
    cap_exceeded = final_nav > 0 and stocks / final_nav > EXPOSURE_MAX + 1e-10
    cap_blocked = sorted(set(cap_blocked)) if cap_exceeded else []
    if cap_exceeded and not cap_blocked:
        raise ValueError('調倉後股票倉位超過上限，且沒有可核對的成交限制')

    # ---- 每週調倉決策明細（實盤信號輸出用）----
    if record:
        total2 = cash + mv_at(px) + receivable
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
        marked = getattr(ctx, 'valuation_close', close).loc[wk]
        unplanned_holdings = [
            {"code": str(c), "market_value": round(pos[c]["shares"] * float(marked[c]), 0)}
            for c in pos if c not in planned_codes and c in marked.index and pd.notna(marked[c]) and float(marked[c]) > 0
        ]
        for holding in unplanned_holdings:
            code = holding["code"]
            shares = pos[code]["shares"]
            price = float(marked[code])
            before = pre_sh.get(code, shares)
            plan_recs.append({"date": wk, "pool": pos[code]["pool"], "code": code,
                              "rank": None, "cur_sh": shares, "target_sh": 0,
                              "cur_val": shares * price, "target_val": 0,
                              "pre_sh": before, "pre_val": before * price,
                              "pre_total": pre_total, "nav": total2, "price": price})
        from .valuation import restricted_assets
        restricted = restricted_assets(ctx, pos, wk)
        weekly_plans.append({"date": wk, "cash": cash, "nav": total2, "receivables": receivable, "recs": plan_recs,
                             "stock_exposure": exposure,
                             "exposure_in_band": EXPOSURE_MIN <= exposure <= EXPOSURE_MAX,
                             "missing_price_targets": missing_price_targets,
                             "blocked_limitup_targets": blocked_limitup_targets,
                             "blocked_exit_codes": blocked_exit_codes,
                             "exposure_cap_blocked_codes": cap_blocked,
                             "unplanned_holdings": unplanned_holdings, "restricted_assets": restricted,
                             "wa_eff": alloc_weights["A"], "wb_eff": alloc_weights["B"]})


    if cash < -1e-7 or not np.isfinite(cash):
        raise ValueError('模擬現金超支或不是有效數字')
    if any(p['shares'] < 0 or int(p['shares']) != p['shares'] for p in pos.values()):
        raise ValueError('模擬持股數量無效')
    return cash, trades, weekly_plans[0] if weekly_plans else None
