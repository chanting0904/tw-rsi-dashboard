"""Load authenticated market data and compute V4 indicators once."""
import json
import os
from pathlib import Path
import pandas as pd
import numpy as np
from live_plan import prepare_prices, completed_week_days
from publish_gate import load_holidays
from .context import MarketContext


def load_token(root):
    token = (os.environ.get('FINLAB_TOKEN') or '').strip()
    if token:
        return token
    try:
        with open(Path(root) / 'config.json', encoding='utf-8-sig') as stream:
            return (json.load(stream).get('FINLAB_TOKEN') or '').strip()
    except (OSError, ValueError):
        return ''


def load_market(config, root, *, live_rebalance=None, data_root=None, token=None):
    import finlab
    from finlab import data
    token = token or load_token(root)
    if not token:
        raise ValueError('找不到 FinLab token（FINLAB_TOKEN 或本機 config.json）')
    finlab.login(api_token=token)
    data.set_storage(data.FileStorage(str(Path(data_root or root) / 'finlab_db')))
    LIVE_DAY = SIGNAL_DAY = None
    close = data.get("price:收盤價").apply(pd.to_numeric, errors="coerce")
    open_ = data.get("price:開盤價").apply(pd.to_numeric, errors="coerce")
    high = data.get("price:最高價").apply(pd.to_numeric, errors="coerce")
    low = data.get("price:最低價").apply(pd.to_numeric, errors="coerce")
    roe_raw = data.get("fundamental_features:ROE稅後")
    roe_f = roe_raw.apply(pd.to_numeric, errors="coerce")
    tv = data.get("price:成交金額").apply(pd.to_numeric, errors="coerce")
    if live_rebalance:
        close, open_, high, low, tv, LIVE_DAY, SIGNAL_DAY = prepare_prices(
            close, open_, high, low, tv, live_rebalance, load_holidays())

    def common(c):
        s = str(c)
        return len(s) == 4 and s.isdigit() and not s.startswith("0")
    keep = [c for c in close.columns if common(c)]

    # ===== 排除創新板（-創）=====
    excl_innov = set()
    try:
        _info = data.get("company_basic_info")
        if _info is not None and "公司簡稱" in _info.columns:
            _key = "stock_id" if "stock_id" in _info.columns else _info.index.name
            excl_innov = set(_info[_info["公司簡稱"].astype(str).str.contains("-創", na=False, regex=False)][_key].astype(str))
    except Exception:
        pass
    keep = [c for c in keep if c not in excl_innov]
    print(f"[v7h] 排除創新板 {len(excl_innov)} 檔，選股池 {len(keep)} 檔")

    close = close[keep]; open_ = open_[[c for c in keep if c in open_.columns]]
    high = high[[c for c in keep if c in high.columns]]; low = low[[c for c in keep if c in low.columns]]
    roe_f = roe_f[[c for c in keep if c in roe_f.columns]]; tv = tv[[c for c in keep if c in tv.columns]]

    # ===== ROE 公告 deadline 對齊（無前視）=====
    roe_dl = roe_raw.deadline()
    roe_dl = roe_dl[[c for c in keep if c in roe_dl.columns]]
    roe_daily = roe_dl.reindex(close.index).ffill().shift(1)
    roe_ok = (roe_daily > 0).fillna(True)

    # ===== 歷史股本（季頻財報 deadline 對齊）=====
    cap_q = data.get("financial_statement:股本").apply(pd.to_numeric, errors="coerce")
    cap_q = cap_q[[c for c in keep if c in cap_q.columns]]
    cap_dl = cap_q.deadline()
    cap_daily = cap_dl.reindex(close.index).ffill().shift(1)
    cap_ok = (cap_daily >= config.cap_threshold).fillna(True)
    print(f"[v7h] 歷史股本序列就緒：{cap_daily.shape[1]} 檔 x {len(cap_daily)} 交易日 | 門檻 {config.cap_threshold/1e5:.0f} 億")

    # Total-return signals prevent ex-dividend/split gaps from becoming exits.
    raw_close, raw_open, raw_high, raw_low = close, open_, high, low
    adjusted = data.get('etl:adj_close').reindex(index=close.index, columns=close.columns)
    if LIVE_DAY is not None:
        adjusted.loc[LIVE_DAY] = adjusted.loc[SIGNAL_DAY]
    factor = adjusted / close
    if (adjusted.isna() & close.notna()).any().any():
        raise ValueError('還原價格缺漏，不能混合未還原資料發布')
    close, open_, high, low = adjusted, open_*factor, high*factor, low*factor

    # ===== ATR20（Wilder，np.maximum 三元素）=====
    prev_close = close.shift(1)
    tr = np.maximum((high - low).abs(),
                    np.maximum((high - prev_close).abs(), (low - prev_close).abs()))
    atr20 = tr.ewm(alpha=1/20, min_periods=20, adjust=False).mean()
    atr20 = atr20[close.columns]

    def rsi(c, n):
        d = c.diff()
        g = d.clip(lower=0); l = (-d).clip(lower=0)
        ag = g.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
        al = l.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
        return 100 - 100/(1 + ag/al)

    r20, r60, r120 = rsi(close, 20), rsi(close, 60), rsi(close, 120)
    r20s = r20.shift(1)
    ma60 = close.rolling(60).mean()
    tv20 = tv.rolling(20).mean()
    liq = tv20.gt(tv20.quantile(0.40, axis=1), axis=0)

    long_up = (r120 > 55).shift(1)
    mid_ok  = (r60 < 75).shift(1)
    rally   = (r20.pct_change(3, fill_method=None) > 0.02).shift(1)
    stuck   = ((r20 > 75).rolling(3).sum() == 3).shift(1)
    buyA = long_up & mid_ok & rally & stuck & roe_ok & liq.shift(1).fillna(True)
    sellA = (close.shift(1) < ma60.shift(1)) | buyA.shift(80).fillna(False)
    targetA = buyA.hold_until(sellA)

    tvw = tv20.rank(axis=1, ascending=False)
    big = tvw.shift(1) <= 15
    buyB = big & (close.shift(1) > ma60.shift(1)) & (r120.shift(1) > 60) & (r20s < 88) & roe_ok
    sellB = (close.shift(1) < ma60.shift(1)) | buyB.shift(80).fillna(False)
    targetB = buyB.hold_until(sellB)

    sw_tsm = {}
    if "2330" in close.columns:
        _ma20 = close["2330"].rolling(20).mean()
        _ma60 = close["2330"].rolling(60).mean()
        _t = (_ma20.shift(1) >= _ma60.shift(1)) & (close["2330"].shift(1) >= _ma60.shift(1))
        sw_tsm = {d: bool(v) for d, v in _t.items()}

    days_all = close.index
    weeks = close.resample("W-FRI").last().index
    weeks = weeks[weeks >= pd.Timestamp(config.start_date)]
    days = completed_week_days(days_all, weeks, load_holidays())

    name_map = {}
    try:
        info = data.get('company_basic_info')
        if info is not None and '公司簡稱' in info.columns:
            name_map = info.set_index('stock_id')['公司簡稱'].to_dict() if 'stock_id' in info.columns else info['公司簡稱'].to_dict()
    except Exception:
        pass
    from .corporate_actions import build_events
    announcements = data.get('dividend_announcement')
    tse_type = data.get('dividend_tse:權息')
    tse_before = data.get('dividend_tse:除權息前收盤價')
    tse_reference = data.get('dividend_tse:除權息參考價')
    cash_fallback = (tse_before-tse_reference).where(tse_type.eq('息'))
    otc_cash = data.get('dividend_otc:現金股利')
    cash_fallback = cash_fallback.combine_first(otc_cash)
    stock_grants = data.get('dividend_otc:每千股無償配股')
    splits = data.get('etf_split:分割(反分割)比率')
    reductions = []
    for exchange, before_name, reference_name in [('tse','停止買賣前收盤價格','恢復買賣參考價'),('otc','最後交易之收盤價格','減資恢復買賣開始日參考價格')]:
        prefix='capital_reduction_'+exchange+':'
        reductions.append((data.get(prefix+'減資原因'),data.get(prefix+before_name),data.get(prefix+reference_name)))
    events = build_events(announcements, raw_close, adjusted, cash_fallback, stock_grants, splits, reductions)
    context = MarketContext(config, raw_close, raw_open, raw_high, raw_low, targetA, targetB, r20s, tv20,
                         ma60, cap_ok, sw_tsm, days, days_all, weeks, LIVE_DAY, SIGNAL_DAY, name_map)
    context.tv20_previous = tv20.shift(1)
    context.adjusted_close = adjusted
    context.corporate_events = events
    return context
