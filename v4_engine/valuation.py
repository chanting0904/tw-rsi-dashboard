"""Conservative delisting marks; never fabricate sale proceeds."""
import pandas as pd
from .security_status import events_by_code, status_at, can_trade


def prepare_valuation(ctx, frame):
    marked = frame.ffill().copy()
    if getattr(ctx.config, 'delisted_valuation', 'zero') == 'zero':
        for code, events in events_by_code(ctx).items():
            if code not in marked:
                continue
            state = False
            for i, event in enumerate(events):
                if event['status'] == 'DELISTED':state = True
                elif event['status'] == 'TRADABLE':state = False
                if state:
                    mask = marked.index >= pd.Timestamp(event['effective_date'])
                    if i+1 < len(events):mask &= marked.index < pd.Timestamp(events[i+1]['effective_date'])
                    marked.loc[mask, code] = 0.
    return marked


def restricted_assets(ctx, positions, day):
    rows = []
    for code, position in positions.items():
        available = position['shares']-position.get('locked_shares',0)
        if not available:continue
        if code not in ctx.close or not (ctx.close.at[day, code] > 0) or not can_trade(ctx, code, day):
            quotes = ctx.close.loc[:day, code].dropna() if code in ctx.close else pd.Series(dtype=float)
            value = available * float(ctx.valuation_close.at[day, code]) if code in ctx.valuation_close else 0.
            name = (ctx.name_map or {}).get(code,code)
            event = status_at(ctx, code, day)
            name = event.get('name') or name
            rows.append({'code': code, 'name':name, 'shares': available, 'pool': position['pool'],
                         'book_value': value, 'last_quote_date': str(quotes.index[-1].date()) if len(quotes) else None,
                         'last_quote': float(quotes.iloc[-1]) if len(quotes) else None,
                         'policy': getattr(ctx.config, 'delisted_valuation', 'zero')})
    return rows
