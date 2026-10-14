"""Prepare an indicative close-rebalance plan from the previous trading day's data."""
import pandas as pd
from publish_gate import prev_trading_day_str, last_trading_day_of_week


def completed_week_days(index, week_ends, holidays):
    latest = index.max()
    result = []
    for end in week_ends:
        start = end - pd.Timedelta(days=4)
        sessions = index[(index >= start) & (index <= end)]
        expected = last_trading_day_of_week(end.date(), holidays)
        if len(sessions) and expected is not None and latest.date() >= expected:
            result.append(sessions[-1])
    return pd.DatetimeIndex(result)


def prepare_prices(close, open_, high, low, turnover, rebalance_date, holidays):
    day = pd.Timestamp(rebalance_date)
    if day.date() != last_trading_day_of_week(day.date(), holidays):
        raise ValueError("調倉日必須是該週最後交易日")
    signal_day = pd.Timestamp(prev_trading_day_str(day.date(), holidays))
    frames = [frame.loc[frame.index <= signal_day].copy()
              for frame in (close, open_, high, low, turnover)]
    if frames[0].dropna(how="all").index[-1] != signal_day:
        raise ValueError(f"缺少訊號基準日 {signal_day.date()} 的收盤資料，不產生本週計畫")
    # The extra row supplies reference prices only. All selection indicators shift
    # by one session, so the rebalance decision sees actual signal-day data.
    reference = frames[0].loc[signal_day]
    for frame in frames[:4]:
        frame.loc[day] = reference.reindex(frame.columns)
    frames[4].loc[day] = frames[4].loc[signal_day]
    return (*frames, day, signal_day)
