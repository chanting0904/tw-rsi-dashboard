"""Dated reference quotes for optional browser-only order differences."""
import math
import re
from types import SimpleNamespace
from .security_status import can_trade


def build_order_quotes(signal, prices, price_date, status):
    if not status:return {}
    context=SimpleNamespace(security_status=status)
    checked=status['checked_for'];listed=status.get('listed',{})
    result={code:dict(name=row.get('name',code),price=None,date=None,tradable=can_trade(context,code,checked),status_date=checked)
        for code,row in listed.items() if re.fullmatch(r'[1-9]\d{3}',code)}
    result=refresh_order_quotes(result,prices,price_date)
    for row in signal.get('actions',[]):
        code=row['stock_code'];previous=result.get(code,{})
        date=row.get('price_date') or signal.get('price_basis_date')
        minimum=max(date or '',row.get('price_valid_from') or '')
        newer=previous.get('date') and previous['date']>=minimum
        result[code]=dict(name=row['stock_name'],price=previous['price'] if newer else row.get('price'),
            date=previous['date'] if newer else date,tradable=can_trade(context,code,checked),status_date=checked,
            price_valid_from=minimum,
            buyable=row.get('buy_allowed',True) if (previous.get('date') if newer else date)==signal.get('price_basis_date') else True,
            sellable=row.get('sell_allowed',True) if (previous.get('date') if newer else date)==signal.get('price_basis_date') else True)
    return result


def refresh_order_quotes(previous, prices, date):
    result={code:dict(row) for code,row in previous.items()}
    for code,row in result.items():
        price=prices.get(code)
        if isinstance(price,(int,float)) and math.isfinite(price) and price>0 and date>=max(row.get('date') or '',row.get('price_valid_from') or ''):
            if row.get('date') and date>row['date']:
                row.pop('buyable',None);row.pop('sellable',None)
            row.update(price=float(price),date=date)
    return result
