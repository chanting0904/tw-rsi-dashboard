"""Whole-share target preview, independent of brokerage holdings and fills."""
import math


def preview_allocation(targets, capital, buy_fee=.0004):
    if not math.isfinite(capital) or capital <= 0 or not 0 <= buy_fee < 1:
        raise ValueError('Invalid capital or fee')
    weights = sum(float(row.get('target_weight', 0)) for row in targets)
    if not math.isfinite(weights) or weights > 1 + 1e-9:
        raise ValueError('Target weights exceed available capital')
    cash, invested, fees = float(capital), 0.0, 0.0
    quantities, unaffordable, missing = {}, [], []
    for row in targets:
        code = str(row['code'])
        weight = float(row.get('target_weight', 0))
        price = row.get('price')
        if code in quantities or not math.isfinite(weight) or weight < 0:
            raise ValueError('Invalid target list')
        qty = 0
        if weight > 0:
            if price is None or not math.isfinite(float(price)) or float(price) <= 0:
                missing.append(code)
            else:
                price = float(price)
                qty = max(0, min(math.floor(capital * weight / price), math.floor(max(cash, 0) / (price * (1 + buy_fee)))))
                if qty == 0:
                    unaffordable.append(code)
                amount, fee = qty * price, qty * price * buy_fee
                cash -= amount + fee
                invested += amount
                fees += fee
        quantities[code] = qty
    nav = cash + invested
    return {'quantities': quantities, 'cash': max(0, cash), 'invested': invested,
            'fees': fees, 'nav': nav, 'exposure': invested / nav if nav else 0,
            'target_weight_total': weights, 'unaffordable': unaffordable, 'missing': missing}
