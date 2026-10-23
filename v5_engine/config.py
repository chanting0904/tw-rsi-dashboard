from dataclasses import dataclass
import math


@dataclass(frozen=True)
class StrategyConfig:
    start_capital: float = 100_000
    start_date: str = '2015-01-01'
    buy_fee: float = .000399
    sell_fee: float = .003399
    minimum_commission: float = 1.
    limit: float = .099
    threshold: float = 1.0
    buffer: float = .05
    exposure_min: float = .90
    exposure_max: float = .95
    exposure_aim: float = .925
    max_hold_a: int = 15
    a_rank_days: int = 3
    a_retention_rank: int = 20
    cap_threshold: float = 6e5
    delisted_valuation: str = 'zero'

    def __post_init__(self):
        if not math.isfinite(self.start_capital) or self.start_capital <= 0:
            raise ValueError('本金必須是有限的正數')
        if not (0 <= self.buy_fee < 1 and 0 <= self.sell_fee < 1):
            raise ValueError('Invalid modeled transaction fees')
        if (not math.isfinite(self.minimum_commission) or self.minimum_commission < 0
                or self.sell_fee < self.buy_fee):
            raise ValueError('Invalid minimum commission or sale tax')
        if not isinstance(self.a_rank_days, int) or self.a_rank_days < 1:
            raise ValueError('A ranking window must be a positive integer')
        if not isinstance(self.a_retention_rank, int) or self.a_retention_rank < self.max_hold_a:
            raise ValueError('A retention rank must cover the entry rank limit')
        if self.delisted_valuation not in ('zero', 'last_quote'):
            raise ValueError('Unknown delisted asset valuation policy')


V5_COEFFICIENTS = (1.5, 1.0, .5)
