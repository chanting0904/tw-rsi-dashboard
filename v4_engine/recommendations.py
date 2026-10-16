"""Separate executable allocation advice from historical blocked model assets."""
import json
from functools import lru_cache
from pathlib import Path
import pandas as pd
from .security_status import status_at


@lru_cache(maxsize=1)
def security_events():
    return json.loads(Path(__file__).with_name('security_events.json').read_text(encoding='utf-8'))


def recommendation_metadata(ctx, wp, row):
    code, day = row['code'], wp['date']
    basis = ctx.signal_day if ctx.live_day is not None and day == ctx.live_day else day
    quotes = ctx.close.loc[ctx.close.index <= basis, code].dropna() if code in ctx.close else pd.Series(dtype=float)
    quotes = quotes[quotes > 0]
    quote_date = quotes.index[-1] if len(quotes) else None
    current_quote = quote_date is not None and quote_date == basis
    status, label, source, known_name = 'TRADABLE', '', None, None
    event = status_at(ctx, code, day)
    status, label, source, known_name = event['status'], event.get('label', ''), event.get('source'), event.get('name')
    if status == 'TRADABLE' and not current_quote:
        status, label = 'NO_CURRENT_QUOTE', '缺少基準日有效報價'
    planned = row['target_val'] > 0
    if status == 'TRADABLE' and not planned and row['cur_sh'] > 0:
        status, label = 'OUTSIDE_TARGET', '已退出目標但模擬持股尚未賣出'
    eligible = status == 'TRADABLE' and current_quote and planned
    note = ''
    projection=(getattr(ctx,'live_action_prices',{}) or {}).get(code) if ctx.live_day is not None and day==ctx.live_day else None
    if not eligible and row['cur_sh'] > 0:
        note = label + '；僅保留歷史模擬紀錄，不列入下單或本金試算。'
    elif (row['action'] == 'BUY' and 0 < row['cur_sh'] < row['target_sh'] - 1):
        reason = '95%倉位上限' if wp.get('stock_exposure', 0) >= ctx.config.exposure_max - .0001 else '總倉位配置或可用資金'
        note = f"受{reason}限制，僅部分配置；原排名目標 {row['target_weight']:.2%}，實際模擬配置 {row['cur_val']/wp['nav']:.2%}。"
    return {'allocation_eligible': eligible, 'trading_status': status,
            'price_kind': 'EX_RIGHTS_REFERENCE' if projection else 'CLOSE',
            'price_valid_from':str(day.date()) if projection else (str(quote_date.date()) if quote_date is not None else None),
            'price_date': quote_date.strftime('%Y-%m-%d') if quote_date is not None else None,
            'recommendation_note': note, 'security_status_source': source,
            'known_name': known_name}
