"""Separate executable allocation advice from historical blocked model assets."""
import json
import math
import re
from functools import lru_cache
from pathlib import Path
import numpy as np
import pandas as pd
from .security_status import status_at


def hidden_zero_asset_codes(signal):
    """Hide written-off history only; current executable positions stay visible."""
    active = {str(row['stock_code']) for row in signal.get('actions', [])
              if row.get('allocation_eligible', True)
              or (row.get('action') == 'SELL' and row.get('estimated_sell_amount', 0) > 0)}
    return {str(asset['code']) for asset in signal.get('restricted_assets', [])
            if asset.get('policy') == 'zero'
            and isinstance(asset.get('book_value'), (int, float))
            and not isinstance(asset['book_value'], bool)
            and math.isfinite(asset['book_value']) and asset['book_value'] == 0
            and str(asset['code']) not in active}


def public_warnings(signal):
    """Remove written-off codes from the blocked-exit notice, preserving live issues."""
    hidden = hidden_zero_asset_codes(signal)
    result = []
    for warning in signal.get('warnings', []):
        match = re.fullmatch(r'有 \d+ 檔持股因(.*?)：([\d、]+)', warning)
        if match:
            codes = [code for code in match[2].split('、') if code not in hidden]
            if codes:
                result.append(f'有 {len(codes)} 檔持股因{match[1]}：' + '、'.join(codes))
        else:
            result.append(warning)
    return result


@lru_cache(maxsize=1)
def security_events():
    return json.loads(Path(__file__).with_name('security_events.json').read_text(encoding='utf-8'))


def recommendation_metadata(ctx, wp, row):
    code, day = row['code'], wp['date']
    basis = ctx.signal_day if ctx.live_day is not None and day == ctx.live_day else day
    quote_date = None
    if code in ctx.close:
        prices = ctx.close[code].to_numpy(dtype=float, na_value=np.nan)
        valid = np.flatnonzero((prices > 0) & (ctx.close.index <= basis))
        if len(valid):
            quote_date = ctx.close.index[valid[-1]]
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
