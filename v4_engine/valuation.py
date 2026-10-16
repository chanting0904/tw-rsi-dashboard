"""Conservative delisting marks; never fabricate sale proceeds."""
import pandas as pd
from .recommendations import security_events


def prepare_valuation(ctx, frame):
    marked = frame.ffill().copy()
    if getattr(ctx.config, 'delisted_valuation', 'zero') == 'zero':
        for code, events in security_events().items():
            if code not in marked:
                continue
            for event in events:
                if event['status'] == 'DELISTED':
                    marked.loc[marked.index >= pd.Timestamp(event['effective_date']), code] = 0.
    return marked


def restricted_assets(ctx, positions, day):
    rows = []
    for code, position in positions.items():
        if code not in ctx.close or not (ctx.close.at[day, code] > 0):
            quotes = ctx.close.loc[:day, code].dropna() if code in ctx.close else pd.Series(dtype=float)
            value = position['shares'] * float(ctx.valuation_close.at[day, code]) if code in ctx.valuation_close else 0.
            name = (ctx.name_map or {}).get(code,code)
            for event in security_events().get(code,[]):
                if event['effective_date'] <= str(day.date()):name=event['name']
            rows.append({'code': code, 'name':name, 'shares': position['shares'], 'pool': position['pool'],
                         'book_value': value, 'last_quote_date': str(quotes.index[-1].date()) if len(quotes) else None,
                         'last_quote': float(quotes.iloc[-1]) if len(quotes) else None,
                         'policy': getattr(ctx.config, 'delisted_valuation', 'zero')})
    return rows
