"""Pure cash/share simulation; no authentication or file writes on import."""
import pandas as pd
from .execution import execute_week
from .performance import summarize_simulation


def run(ctx, weekday=5, mode='close', coefs=(1.5, 1.0, .5), start=None, end=None, record=True):
    if mode not in ('close', 'open'):
        raise ValueError('Unsupported execution mode')
    dates = ctx.pick_days(weekday)
    dates = dates[dates >= pd.Timestamp(ctx.config.start_date)]
    first_day = pd.Timestamp(start or ctx.config.start_date)
    dates = dates[dates >= first_day]
    if end is not None:
        dates = dates[dates <= pd.Timestamp(end)]
    price_frame = ctx.open_ if mode == 'open' else ctx.close
    adjusted = ctx.adjusted_close if ctx.adjusted_close is not None else price_frame
    from .valuation import prepare_valuation
    ctx.valuation_close = prepare_valuation(ctx, price_frame)
    changes = adjusted.pct_change(fill_method=None)
    limit_up, limit_dn = changes >= ctx.config.limit, changes <= -ctx.config.limit
    cash, positions = float(ctx.config.start_capital), {}
    nav_hist, trades, plans = [], [], []
    from .corporate_actions import CorporateLedger
    ledger = CorporateLedger(ctx.corporate_events)
    sessions = ctx.close.index[ctx.close.index >= first_day]
    if end is not None:
        sessions = sessions[sessions <= pd.Timestamp(end)]
    rebalance_days = set(dates)
    for day in sessions:
        if ctx.live_day is None or day <= ctx.signal_day:
            cash, corporate_records = ledger.advance(day, positions, cash, ctx.close.loc[day])
            trades.extend(corporate_records)
        ctx.receivable_total = ledger.value
        if day in rebalance_days and day in ctx.targetA.index:
            cash, fills, plan = execute_week(ctx, positions, cash, day, mode, coefs, limit_up, limit_dn, record)
            trades.extend(fills)
            if plan is not None:
                plans.append(plan)
        prices = ctx.valuation_close.loc[day]
        market_value = sum(pos['shares'] * float(prices[code]) for code, pos in positions.items()
                           if pd.notna(prices[code]) and float(prices[code]) > 0)
        nav_hist.append({'date': day, 'nav': cash + market_value + ledger.value,
                         'cash': cash, 'stocks': market_value, 'receivables': ledger.value})
    if not nav_hist:
        raise ValueError('沒有可模擬的已完成交易週')
    result = summarize_simulation(nav_hist, trades, ctx.close, ctx.config, weekday, mode, coefs, start, end)
    result['weekly_plans'] = plans
    result['corporate_action_audit'] = ledger.audit
    result['receivables'] = ledger.receivables
    return result
