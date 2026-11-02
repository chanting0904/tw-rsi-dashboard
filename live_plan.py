"""Prepare an indicative close-rebalance plan from the previous trading day's data."""
import json
from pathlib import Path
import pandas as pd
import numpy as np
from publish_gate import prev_session_str, last_trading_day_of_week


CLOSURES_FILE = Path(__file__).with_name('market_closures.json')


def unscheduled_closures(index, holidays):
    """颱風等臨時休市日：已核對的歷史清單，加上休市表涵蓋年份中沒有行情的平日。"""
    days = set(json.loads(CLOSURES_FILE.read_text(encoding='utf-8'))['closures'])
    sessions = {day.date().isoformat() for day in index}
    years = {day[:4] for day in holidays}
    if years:
        for day in pd.bdate_range(f'{min(years)}-01-01', index.max()):
            text = day.date().isoformat()
            if text[:4] in years and text not in holidays and text not in sessions:
                days.add(text)
    return days


def completed_week_days(index, week_ends, holidays, closures=(), moves=None):
    latest = index.max()
    result = []
    for end in week_ends:
        start = end - pd.Timedelta(days=4)
        sessions = index[(index >= start) & (index <= end)]
        expected = last_trading_day_of_week(end.date(), holidays)
        if expected is not None and expected.isoformat() in closures:
            # 原定調倉日臨時休市：前一晚才公布，來不及提前在前一天收盤調倉；
            # 照實際規則延到之後第一個交易日收盤。
            later = index[index > pd.Timestamp(expected)]
            if len(later):
                result.append(later[0])
                if moves is not None:
                    moves[later[0]] = pd.Timestamp(expected)
            continue
        if len(sessions) and expected is not None and latest.date() >= expected:
            result.append(sessions[-1])
    return pd.DatetimeIndex(sorted(set(result)))


def prepare_prices(close, open_, high, low, turnover, rebalance_date, holidays):
    day = pd.Timestamp(rebalance_date)
    if day.date() != last_trading_day_of_week(day.date(), holidays):
        raise ValueError("調倉日必須是該週最後交易日")
    signal_day = pd.Timestamp(prev_session_str(day.date(), holidays))
    frames = [frame.loc[frame.index <= signal_day].copy()
              for frame in (close, open_, high, low, turnover)]
    for name,frame in zip(['收盤價','開盤價','最高價','最低價','成交金額'],frames):
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError(f'{name}日期重複或未排序，不產生本週計畫')
        if signal_day not in frame.index:
            raise ValueError(f'缺少訊號基準日 {signal_day.date()} 的{name}資料，不產生本週計畫')
        row=pd.to_numeric(frame.loc[signal_day],errors='coerce')
        if not (np.isfinite(row)&(row>0)).any():
            raise ValueError(f'訊號基準日 {signal_day.date()} 的{name}資料無有效值，不產生本週計畫')
    # The extra row supplies reference prices only. All selection indicators shift
    # by one session, so the rebalance decision sees actual signal-day data.
    reference = frames[0].loc[signal_day]
    for frame in frames[:4]:
        frame.loc[day] = reference.reindex(frame.columns)
    frames[4].loc[day] = frames[4].loc[signal_day]
    return (*frames, day, signal_day)
