"""Deterministic, order-independent integer allocation within a money budget."""
import math


def proportional_quantities(requests, budget):
    rows = sorted(requests, key=lambda row: str(row['code']))
    if len({row['code'] for row in rows}) != len(rows):
        raise ValueError('Duplicate allocation request')
    if not math.isfinite(budget) or any(not math.isfinite(row['price']) or row['price']<=0 or
            row['quantity']<0 or int(row['quantity'])!=row['quantity'] for row in rows):
        raise ValueError('Invalid allocation request or budget')
    budget = max(0., float(budget))
    total = sum(row['quantity'] * row['price'] for row in rows)
    scale = min(1., budget / total) if total > 0 else 0.
    ideal = {row['code']: row['quantity'] * scale for row in rows}
    quantities = {row['code']: max(0, min(int(row['quantity']), math.floor(ideal[row['code']]))) for row in rows}
    remaining = budget - sum(quantities[row['code']] * row['price'] for row in rows)
    # Each candidate receives at most its next whole share above the ideal.
    candidates = sorted(rows, key=lambda row: (-(ideal[row['code']] - quantities[row['code']]), str(row['code'])))
    for row in candidates:
        code, price = row['code'], row['price']
        if quantities[code] < row['quantity'] and price <= remaining + 1e-9:
            quantities[code] += 1
            remaining -= price
    spent = sum(quantities[row['code']] * row['price'] for row in rows)
    if spent > budget + 1e-7:
        raise ValueError('Integer allocation exceeds budget')
    return quantities


def proportional_sales(requests, required):
    rows = sorted(requests, key=lambda row: str(row['code']))
    total = sum(row['quantity'] * row['price'] for row in rows)
    required = min(max(0., required), total)
    quantities = proportional_quantities(rows, required)
    sold = sum(quantities[row['code']] * row['price'] for row in rows)
    while sold + 1e-8 < required:
        candidates = [row for row in rows if quantities[row['code']] < row['quantity']]
        if not candidates:
            break
        row = min(candidates, key=lambda row: (abs(row['price'] - (required-sold)), str(row['code'])))
        quantities[row['code']] += 1
        sold += row['price']
    return quantities
