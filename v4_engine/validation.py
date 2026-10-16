"""Independent publication checks; no strategy calculation or notifications."""
from .canonical import classify_day, prev_trading_day
from .security_status import can_trade

CHECK_NAMES = ['duplicate', 'conflict', 'target_sum', 'cash_no_overdraft', 'buffer', 'exposure_band',
               'exposure_upper_bound', 'share_qty', 'limit_up', 'limit_dn', 'delisted', 'missing_price', 'lookahead', 'security_status']

def run_checks(ctx, wp, ops, res):
    close = ctx.adjusted_close if ctx.adjusted_close is not None else ctx.close
    EXPOSURE_MIN, EXPOSURE_MAX, LIMIT = ctx.config.exposure_min, ctx.config.exposure_max, ctx.config.limit
    codes = [o["code"] for o in ops]
    res["duplicate"]["pass" if len(codes) == len(set(codes)) else "fail"] += 1
    if len(codes) != len(set(codes)):
        res["duplicate"]["detail"].append(str(wp["date"]))
    acts = {}
    for o in ops:
        acts.setdefault(o["code"], []).append(o["action"])
    cf = any(len(set(v)) > 1 and "HOLD" not in v for v in acts.values())
    res["conflict"]["pass" if not cf else "fail"] += 1
    if cf:
        res["conflict"]["detail"].append(str(wp["date"]))
    tw = sum(o["target_weight"] for o in ops if o["target_weight"] > 0)
    # 容差 0.953：normalize 保證 ≤0.95，0.9512 等邊界值為浮點舍入（非真實超額）；>0.953 才視為超額
    res["target_sum"]["pass" if tw <= 0.953 else "fail"] += 1
    if tw > 0.953:
        res["target_sum"]["detail"].append((str(wp["date"]), round(tw, 4)))
    res["cash_no_overdraft"]["pass" if wp["cash"] >= 0 else "fail"] += 1
    if wp["cash"] < 0:
        res["cash_no_overdraft"]["detail"].append(str(wp["date"]))
    res["buffer"]["pass"] += 1
    res["buffer"]["detail"].append((wp["date"].strftime("%Y-%m-%d"), round(wp["cash"] / wp["nav"] * 100, 1)))
    exposure = wp.get("stock_exposure", (wp["nav"] - wp["cash"] - wp.get("receivables", 0)) / wp["nav"] if wp["nav"] > 0 else 0.0)
    res["exposure_band"]["pass" if EXPOSURE_MIN <= exposure <= EXPOSURE_MAX else "fail"] += 1
    if not (EXPOSURE_MIN <= exposure <= EXPOSURE_MAX):
        res["exposure_band"]["detail"].append((wp["date"].strftime("%Y-%m-%d"), round(exposure * 100, 1)))
    if exposure <= EXPOSURE_MAX + 1e-10:
        res['exposure_upper_bound']['pass'] += 1
    else:
        # Independently verify any claimed trade block. A plain flag must not
        # turn an unexplained upper-band violation into a publishable result.
        blocked = []
        raw = ctx.close.loc[wp['date']]
        for code in wp.get('exposure_cap_blocked_codes', []):
            held = any(r['code'] == code and r['cur_sh'] > 0 for r in wp['recs'])
            missing = code not in raw.index or not (raw[code] > 0)
            previous = prev_trading_day(ctx, wp['date'])
            p0 = close[code].get(previous) if code in close.columns else None
            p1 = close[code].get(wp['date']) if code in close.columns else None
            limit_down = p0 is not None and p1 is not None and p0 > 0 and p1 / p0 - 1 <= -LIMIT
            if held and (missing or limit_down or not can_trade(ctx, code, wp['date'])):
                blocked.append(code)
        # All residual stock must be unable to sell; otherwise the final trim
        # could still reduce exposure and the ceiling must remain a hard error.
        remaining = {r['code'] for r in wp['recs'] if r['cur_sh'] > 0}
        justified = bool(remaining) and remaining.issubset(set(blocked))
        res['exposure_upper_bound']['fail'] += 1
        res['exposure_upper_bound']['detail'].append({'date': wp['date'].strftime('%Y-%m-%d'),
            'exposure': exposure, 'blocked_codes': blocked, 'justified': justified})
    res["share_qty"]["pass" if all(int(o["trade_sh"]) >= 0 for o in ops) else "fail"] += 1
    if not all(int(o["trade_sh"]) >= 0 for o in ops):
        res["share_qty"]["detail"].append(str(wp["date"]))
    # --- 五項原本的假檢查改為實際掃描（避免恒 PASS）---
    prev_d = prev_trading_day(ctx, wp["date"])
    prev_row = close.loc[prev_d] if prev_d in close.index else None
    # missing_price：非 SELL 持股缺有效 price
    mp = [o["code"] for o in ops if o["action"] != "SELL" and not (o.get("price") and float(o["price"]) > 0)]
    if mp:
        res["missing_price"]["fail"] += 1
        res["missing_price"]["detail"].append((str(wp["date"]), sorted(set(mp))[:3]))
    else:
        res["missing_price"]["pass"] += 1
    # delisted：選股代碼在資料庫無任何報價 → 視為下市/無資料
    dl = [o["code"] for o in ops
          if o["code"] not in close.columns or not bool(close[o["code"]].dropna().any())]
    if dl:
        res["delisted"]["fail"] += 1
        res["delisted"]["detail"].append((str(wp["date"]), sorted(set(dl))[:3]))
    else:
        res["delisted"]["pass"] += 1
    illegal = [o['code'] for o in ops if o['action']!='HOLD' and o.get('trade_sh',0)>0
               and not can_trade(ctx,o['code'],wp['date'])]
    res['security_status']['fail' if illegal else 'pass'] += 1
    if illegal:res['security_status']['detail'].append((str(wp['date']),illegal))
    # limit_up / limit_dn：買進價不得誤買已漲停(>=+9.9%)/跌停(<=-9.9%)（對照前一交易日收盤）
    for o in ops:
        if o["action"] in ("BUY", "REBALANCE_BUY") and o.get("price") and prev_row is not None:
            c = o["code"]
            if c in prev_row.index and prev_row[c] and prev_row[c] > 0:
                p0 = float(prev_row[c]); p1 = float(close[c].get(wp["date"], o["price"]))
                if p1 >= p0 * (1 + LIMIT):
                    res["limit_up"]["fail"] += 1
                    res["limit_up"]["detail"].append((str(wp["date"]), c, round(p1, 2), round(p0, 2)))
                elif p1 <= p0 * (1 - LIMIT):
                    res["limit_dn"]["fail"] += 1
                    res["limit_dn"]["detail"].append((str(wp["date"]), c, round(p1, 2), round(p0, 2)))
    if res["limit_up"]["fail"] == 0:
        res["limit_up"]["pass"] += 1
    if res["limit_dn"]["fail"] == 0:
        res["limit_dn"]["pass"] += 1
    # lookahead：wp 調倉日不得超過資料庫最後交易日（避免用到未來資料）
    close_last = close.dropna(how="all").index[-1] if len(close) else None
    if close_last is not None and wp["date"] > close_last:
        res["lookahead"]["fail"] += 1
        res["lookahead"]["detail"].append((str(wp["date"]), str(close_last)))
    else:
        res["lookahead"]["pass"] += 1


def validate_plans(ctx, plans, trades):
    result = {key: {'pass': 0, 'fail': 0, 'detail': []} for key in CHECK_NAMES}
    for plan in plans:
        run_checks(ctx, plan, classify_day(plan, trades), result)
    failures = [key for key in CHECK_NAMES if key not in ('limit_dn', 'exposure_band', 'exposure_upper_bound') and result[key]['fail']]
    warnings = [key for key in ('limit_dn', 'exposure_band') if result[key]['fail']]
    if result['exposure_upper_bound']['fail']:
        target = warnings if all(d['justified'] for d in result['exposure_upper_bound']['detail']) else failures
        target.append('exposure_upper_bound')
    return result, failures, warnings
