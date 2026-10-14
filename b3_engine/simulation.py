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
    changes = price_frame.pct_change(fill_method=None)
    limit_up, limit_dn = changes >= ctx.config.limit, changes <= -ctx.config.limit
    cash, positions = float(ctx.config.start_capital), {}
    nav_hist, trades, plans = [], [], []
    for day in dates:
        if day not in ctx.targetA.index:
            continue
        cash, fills, plan = execute_week(ctx, positions, cash, day, mode, coefs, limit_up, limit_dn, record)
        trades.extend(fills)
        if plan is not None:
            plans.append(plan)
        prices = price_frame.loc[day]
        market_value = sum(pos['shares'] * float(prices[code]) for code, pos in positions.items()
                           if pd.notna(prices[code]) and float(prices[code]) > 0)
        nav_hist.append({'date': day, 'nav': cash + market_value})
    if not nav_hist:
        raise ValueError('沒有可模擬的已完成交易週')
    result = summarize_simulation(nav_hist, trades, ctx.close, ctx.config, weekday, mode, coefs, start, end)
    result['weekly_plans'] = plans
    return result
