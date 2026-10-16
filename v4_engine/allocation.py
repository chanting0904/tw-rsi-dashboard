"""Full-portfolio whole-share preview with cash and exposure reconciliation."""
import math


def preview_allocation(targets, capital, buy_fee=.0004):
    if not math.isfinite(capital) or capital <= 0 or not 0 <= buy_fee < 1:
        raise ValueError('Invalid capital or fee')
    total = sum(float(row.get('target_weight', 0)) for row in targets)
    if not math.isfinite(total) or total > 1 + 1e-9:
        raise ValueError('Target weights exceed available capital')
    rows, quantities, missing, unaffordable, restricted = [], {}, [], [], []
    for row in targets:
        code, weight = str(row['code']), float(row.get('target_weight', 0))
        if code in quantities or not math.isfinite(weight) or weight < 0:
            raise ValueError('Invalid target list')
        quantities[code] = 0
        price = float(row['price']) if row.get('price') is not None else float('nan')
        reason = None
        if row.get('allocation_eligible') is False:
            restricted.append(code)
            reason = 'restricted'
        elif weight > 0 and (not math.isfinite(price) or price <= 0):
            missing.append(code)
            reason = 'missing_price'
        elif weight > 0 and capital*weight < price:
            unaffordable.append(code)
            reason = 'under_one_share'
        rows.append({'code':code,'weight':weight,'price':price,'excluded':reason,'pool':row.get('channel','default')})
    active = sorted([row for row in rows if row['weight'] > 0 and row['excluded'] is None], key=lambda row:row['code'])
    pools = {row['pool'] for row in active}
    pool_weights = {pool:sum(row['weight'] for row in rows if row['pool']==pool and row['excluded']!='restricted') for pool in pools}
    active_weights = {pool:sum(row['weight'] for row in active if row['pool']==pool) for pool in pools}
    sum_weight = sum(pool_weights.values())
    budget = .95*capital/(1+.95*buy_fee) if active else 0.
    ideals = {row['code']:budget*pool_weights[row['pool']]/sum_weight*row['weight']/active_weights[row['pool']] for row in active}
    for row in active:
        quantities[row['code']] = math.floor(ideals[row['code']]/row['price'])
    invested = sum(quantities[row['code']]*row['price'] for row in active)
    def headroom():
        nav = capital-invested*buy_fee
        return min(max(0., capital-invested*(1+buy_fee))/(1+buy_fee),max(0., .95*nav-invested)/(1+.95*buy_fee))
    if active and invested/(capital-invested*buy_fee) < .90:
        for _ in range(len(active)*3+3):
            nav = capital-invested*buy_fee
            if invested/nav >= .925:
                break
            available = headroom()
            candidates = [row for row in active if row['price'] <= available+1e-9]
            if not candidates:
                break
            row = min(candidates,key=lambda row:(-(ideals[row['code']]-quantities[row['code']]*row['price'])/ideals[row['code']],row['code']))
            current = quantities[row['code']]*row['price']
            needed = max(0., (.925*nav-invested)/(1+.925*buy_fee))
            gap = max(row['price'], ideals[row['code']]-current)
            qty = max(1, math.floor(min(available, needed, gap)/row['price']))
            if current >= ideals[row['code']]:
                qty = max(1, math.floor(min(available, needed)/row['price']))
            quantities[row['code']] += qty
            invested += qty*row['price']
    fees = invested*buy_fee
    cash, nav = capital-invested-fees, capital-fees
    if cash < -1e-7 or invested/nav > .95+1e-10:
        raise ValueError('Preview violates cash or exposure limit')
    details=[]
    for row in rows:
        code,price = row['code'],row['price']
        qty = quantities[code]
        amount = qty*price if qty else 0.
        target = ideals.get(code,capital*row['weight'])
        reason = row['excluded']
        if reason is None and row['weight'] > 0:
            if qty < math.floor(capital*row['weight']/price):reason='exposure_limit'
            elif amount > ideals[code]+1e-8:reason='rounding_repair'
            elif abs(target-capital*row['weight']) > price:reason='redistributed'
            else:reason='rounding'
            if qty == 0:unaffordable.append(code)
        details.append({'code':code,'quantity':qty,'target_amount':target,'original_target_amount':capital*row['weight'],
                        'invested':amount,'shortfall':max(0.,target-amount),'reason':reason})
    return {'quantities':quantities,'cash':max(0.,cash),'invested':invested,'fees':fees,'nav':nav,
            'exposure':invested/nav,'target_weight_total':.95 if active else 0.,'original_weight_total':total,
            'unaffordable':unaffordable,'missing':missing,'restricted':restricted,'details':details,
            'weights':{row['code']:(quantities[row['code']]*row['price']/nav if quantities[row['code']] else 0.) for row in rows},
            'shortfall':max(0.,budget-invested),'below_band_amount':max(0.,.90*nav-invested),
            'band_reachable':.90-1e-10 <= invested/nav <= .95+1e-10}
