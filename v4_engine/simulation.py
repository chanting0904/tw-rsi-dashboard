"""Pure cash/share simulation; no authentication or file writes on import."""
import pandas as pd
from .execution import execute_week
from .performance import summarize_simulation


def run(ctx, weekday=5, mode='close', coefs=(1.5, 1.0, .5), start=None, end=None, record=True):
    if mode not in ('close', 'open'):
        raise ValueError('Unsupported execution mode')
    from .live_actions import prepare_live_actions
    prepare_live_actions(ctx)
    dates = ctx.pick_days(weekday)
    if ctx.live_day is not None:dates=dates[dates<=ctx.live_day]
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
    ledger = CorporateLedger(ctx.corporate_events, getattr(ctx,'share_delivery_events',None))
    sessions = ctx.close.index[ctx.close.index >= first_day]
    if ctx.live_day is not None:sessions=sessions[sessions<=ctx.live_day]
    if end is not None:
        sessions = sessions[sessions <= pd.Timestamp(end)]
    rebalance_days = set(dates)
    for day in sessions:
        if ctx.live_day is None or day <= ctx.signal_day:
            cash, corporate_records = ledger.advance(day, positions, cash, ctx.close.loc[day])
            trades.extend(corporate_records)
        else:
            unknown=set(ctx.live_action_unverified)&set(positions)
            if unknown:
                raise ValueError('持股除權息公告日期未核實，停止產生計畫：'+','.join(sorted(unknown)))
            cash,corporate_records=ledger.advance(day,positions,cash,ctx.close.loc[day],
                day_events=ctx.live_action_events,known_through=ctx.signal_day)
            trades.extend(corporate_records)
        marks = ctx.valuation_close.loc[day]
        pending = ledger.pending_assets(day, positions, marks, known_through=ctx.signal_day if ctx.live_day is not None and day>ctx.signal_day else day)
        ctx.share_entitlements = pending
        ctx.receivable_total = ledger.value+sum(asset['book_value'] for asset in pending)
        if day in rebalance_days and day in ctx.targetA.index:
            cash, fills, plan = execute_week(ctx, positions, cash, day, mode, coefs, limit_up, limit_dn, record)
            trades.extend(fills)
            if plan is not None:
                plans.append(plan)
        prices = ctx.valuation_close.loc[day]
        market_value = sum((pos['shares']-pos.get('locked_shares',0)) * float(prices[code]) for code, pos in positions.items()
                           if pd.notna(prices[code]) and float(prices[code]) > 0)
        nav_hist.append({'date': day, 'nav': cash + market_value + ctx.receivable_total,
                         'cash': cash, 'stocks': market_value, 'receivables': ctx.receivable_total,
                         'pending_stock_value': sum(asset['book_value'] for asset in pending)})
    if not nav_hist:
        raise ValueError('沒有可模擬的已完成交易週')
    result = summarize_simulation(nav_hist, trades, ctx.close, ctx.config, weekday, mode, coefs, start, end)
    result['weekly_plans'] = plans
    result['corporate_action_audit'] = ledger.audit
    result['receivables'] = ledger.receivables
    result['pending_assets'] = ctx.share_entitlements
    return result
