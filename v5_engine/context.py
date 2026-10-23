from dataclasses import dataclass
from typing import Any
from .config import StrategyConfig


@dataclass
class MarketContext:
    config: StrategyConfig
    close: Any
    open_: Any
    high: Any
    low: Any
    targetA: Any
    targetB: Any
    r20s: Any
    tv20: Any
    ma60: Any
    cap_ok: Any
    sw_tsm: dict
    days: Any
    days_all: Any
    weeks: Any
    live_day: Any = None
    signal_day: Any = None
    name_map: dict = None
    adjusted_close: Any = None
    corporate_events: dict = None
    receivable_total: float = 0.
    a_rank_scores: Any = None
    security_status: Any = None
    security_types: Any = None
    share_entitlements: Any = None
    share_delivery_events: Any = None

    def pick_days(self, weekday=5):
        import pandas as pd
        if weekday == 5:
            return self.days
        result = []
        for week in self.weeks:
            candidates = self.days_all[(self.days_all <= week) & (self.days_all > week - pd.Timedelta(days=8))]
            if not len(candidates):
                continue
            target = candidates[candidates.dayofweek == weekday - 1]
            result.append(target[-1] if len(target) else candidates[-1])
        return pd.DatetimeIndex(result)
