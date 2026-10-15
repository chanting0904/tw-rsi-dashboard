"""Pure cash/share simulation; no authentication or file writes on import."""
import pandas as pd
from .execution import execute_week
from .performance import summarize_simulation


def run(ctx, weekday=5, mode='close', coefs=(1.5, 1.0, .5), start=None, end=None, record=True):
    if mode not in ('close', 'open'):
        raise ValueError('Unsupported execution mode')
    dates = ctx.pick_days(weekday)
    if start is not None:
        dates = dates[dates >= pd.Timestamp(start)]
    if end is not None:
        dates = dates[dates <= pd.Timestamp(end)]
    price_frame = ctx.open_ if mode == 'open' else ctx.close
    adjusted = ctx.adjusted_close if ctx.adjusted_close is not None else price_frame
    ctx.valuation_close = price_frame.ffill()
    changes = adjusted.pct_change(fill_method=None)
    limit_up, limit_dn = changes >= ctx.config.limit, changes <= -ctx.config.limit
    cash, positions = float(ctx.config.start_capital), {}
    nav_hist, trades, plans = [], [], []
    from .corporate_actions import CorporateLedger
    ledger = CorporateLedger(ctx.corporate_events)
    previous_day = None
    for day in dates:
        between = ctx.close.index[(ctx.close.index <= day) & (ctx.close.index > previous_day)] if previous_day is not None else ctx.close.index[ctx.close.index == day]
        for action_day in between:
            if ctx.live_day is not None and action_day > ctx.signal_day:
                continue
            cash, corporate_records = ledger.advance(action_day, positions, cash, ctx.close.loc[action_day])
            trades.extend(corporate_records)
        previous_day = day
        ctx.receivable_total = ledger.value
        if day not in ctx.targetA.index:
            continue
        cash, fills, plan = execute_week(ctx, positions, cash, day, mode, coefs, limit_up, limit_dn, record)
        trades.extend(fills)
        if plan is not None:
            plans.append(plan)
        prices = ctx.valuation_close.loc[day]
        market_value = sum(pos['shares'] * float(prices[code]) for code, pos in positions.items()
                           if pd.notna(prices[code]) and float(prices[code]) > 0)
        nav_hist.append({'date': day, 'nav': cash + market_value + ledger.value})
    if not nav_hist:
        raise ValueError('沒有可模擬的已完成交易週')
    result = summarize_simulation(nav_hist, trades, ctx.close, ctx.config, weekday, mode, coefs, start, end)
    result['weekly_plans'] = plans
    result['corporate_action_audit'] = ledger.audit
    result['receivables'] = ledger.receivables
    return result
