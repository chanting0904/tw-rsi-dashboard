"""Explicit brokerage commission and sale tax, charged per consolidated order."""
import math

COMMISSION_RATE = .001425 * .28
MIN_COMMISSION = 1.
TAX_RATES = {'stock': .003, 'etf': .001}


def order_cost(amount, side, commission_rate=COMMISSION_RATE, minimum=MIN_COMMISSION,
               instrument='stock', eligible_day_trade=False, sale_rate=None):
    if not math.isfinite(amount) or amount < 0 or side not in ('BUY', 'SELL'):
        raise ValueError('Invalid order amount or side')
    if (not math.isfinite(commission_rate) or not 0 <= commission_rate < 1
            or not math.isfinite(minimum) or minimum < 0):
        raise ValueError('Invalid commission settings')
    if instrument not in TAX_RATES:
        raise ValueError('Unknown security tax category')
    if not amount:
        return {'commission': 0., 'tax': 0., 'fee': 0.}
    commission = max(minimum, amount * commission_rate)
    tax_rate = TAX_RATES[instrument] if sale_rate is None else sale_rate
    if eligible_day_trade and instrument == 'stock':
        tax_rate = .0015
    if not math.isfinite(tax_rate) or not 0 <= tax_rate < 1:
        raise ValueError('Invalid sale tax rate')
    tax = amount * tax_rate if side == 'SELL' else 0.
    return {'commission': commission, 'tax': tax, 'fee': commission + tax}


class OrderFees:
    """Merge internal sizing passes into one commission minimum per code/side/day."""
    def __init__(self, config, instruments=None):
        self.config = config
        self.instruments = instruments or {}
        self.amounts = {}

    def quote(self, code, side, amount):
        if not math.isfinite(amount) or amount<0:
            raise ValueError('Invalid incremental order amount')
        prior = self.amounts.get((code, side), 0.)
        settings = dict(commission_rate=self.config.buy_fee, minimum=self.config.minimum_commission,
                        instrument=self.instruments.get(code, 'stock'))
        # Explicit alternate model costs remain usable in sensitivity research.
        if settings['instrument'] == 'stock':
            settings['sale_rate'] = max(0., self.config.sell_fee - self.config.buy_fee)
        old = order_cost(prior, side, **settings)
        new = order_cost(prior + amount, side, **settings)
        return {key: new[key] - old[key] for key in ['commission', 'tax', 'fee']}

    def charge(self, code, side, amount):
        charge = self.quote(code, side, amount)
        self.amounts[(code, side)] = self.amounts.get((code, side), 0.) + amount
        return charge
