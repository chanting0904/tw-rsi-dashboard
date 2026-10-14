"""Account for every fill in a completed position cycle and every calendar year."""
import numpy as np
import pandas as pd


CYCLE_COLUMNS = ['code', 'ret', 'pnl', 'buy_cost', 'sell_proceeds', 'days', 'pool',
                 'exit_d', 'entry_d', 'rank', 'mfe', 'mae']


def trade_cycles(trades, close, buy_fee, sell_fee):
    if trades.empty:
        return pd.DataFrame(columns=CYCLE_COLUMNS)
    cycles = []
    for code, group in trades.groupby('code', sort=False):
        shares = 0
        avg = buy_cost = sell_proceeds = 0.0
        entry = pool = rank = None
        for row in group.sort_values('date', kind='stable').to_dict('records'):
            day, qty, price = row['date'], int(row['shares']), float(row['price'])
            if qty <= 0:
                continue
            if row['side'] == 'BUY':
                cost = qty * price * (1 + buy_fee)
                if shares == 0:
                    entry, rank = day, row.get('rank')
                avg = (avg * shares + cost) / (shares + qty)
                shares += qty
                buy_cost += cost
                pool = row['pool']
            elif row['side'] == 'SELL':
                if qty > shares:
                    raise ValueError(f'{code}: sold {qty} shares but only {shares} recorded')
                sell_proceeds += qty * price * (1 - sell_fee)
                shares -= qty
                if shares:
                    continue
                pnl = sell_proceeds - buy_cost
                segment = close[str(code)].loc[entry:day].dropna() if str(code) in close.columns else pd.Series(dtype=float)
                cycles.append({'code': str(code), 'ret': pnl / buy_cost if buy_cost else 0,
                               'pnl': pnl, 'buy_cost': buy_cost, 'sell_proceeds': sell_proceeds,
                               'days': len(segment), 'pool': pool, 'exit_d': day,
                               'entry_d': entry, 'rank': rank,
                               'mfe': float(segment.max() / avg - 1) if len(segment) >= 2 and avg > 0 else np.nan,
                               'mae': float(segment.min() / avg - 1) if len(segment) >= 2 and avg > 0 else np.nan})
                avg = buy_cost = sell_proceeds = 0.0
                entry = pool = rank = None
    result = pd.DataFrame(cycles, columns=CYCLE_COLUMNS)
    if not result.empty:
        result = result.sort_values(['exit_d', 'entry_d', 'code'], kind='stable').reset_index(drop=True)
    return result


def profit_factor(cycles):
    if cycles.empty:
        return float('nan')
    gains = float(cycles.loc[cycles.pnl > 0, 'pnl'].sum())
    losses = abs(float(cycles.loc[cycles.pnl < 0, 'pnl'].sum()))
    return gains / losses if losses else (float('inf') if gains else float('nan'))


def annual_performance(nav, start_capital):
    """Returns telescope to full-period growth, including the year's first fill."""
    series = nav['nav'].sort_index()
    if series.empty:
        return []
    result = []
    basis = float(start_capital)
    basis_date = None
    for year, values in series.groupby(series.index.year):
        end = float(values.iloc[-1])
        path = pd.Series([basis, *values.astype(float).tolist()])
        drawdown = float((path / path.cummax() - 1).min())
        result.append({'year': int(year), 'ret': (end / basis - 1) * 100,
                       'mdd': drawdown * 100, 'basis_nav': basis,
                       'basis_date': basis_date, 'end_date': values.index[-1].strftime('%Y-%m-%d')})
        basis, basis_date = end, values.index[-1].strftime('%Y-%m-%d')
    return result


def perf_document(result, config, anchor=None):
    nav = result['nav']
    series = nav['nav']
    annual = annual_performance(nav, config.start_capital)
    return {'final_nav': round(float(result['final']), 0), 'cagr': round(float(result['cagr']), 4),
            'mdd': round(float(result['mdd']), 4), 'sharpe': round(float(result['sharpe']), 2),
            'sortino': round(float(result['sortino']), 2), 'calmar': round(float(result['calmar']), 2),
            'pf': float(result['pf']), 'winrate': round(float(result['winrate']), 1),
            'trades': int(result['trades']), 'turnover': round(float(result['turnover']), 2),
            'start_capital': config.start_capital, 'strategy_version': 'B3',
            'buy_fee': config.buy_fee, 'sell_fee': config.sell_fee,
            'completed_through': nav.index[-1].strftime('%Y-%m-%d'),
            'fixed_anchor': anchor,
            'nav': [[day.strftime('%Y-%m-%d'), round(float(value), 0)] for day, value in series.items()],
            'dd': [[day.strftime('%Y-%m-%d'), round(float(value), 2)] for day, value in ((series / series.cummax() - 1) * 100).items()],
            'yearly': [{**item, 'ret': round(item['ret'], 2), 'mdd': round(item['mdd'], 2)} for item in annual],
            'accounting_version': 'all_fills_and_prior_year_nav'}


def summarize_simulation(nav_hist, trades, close, config, weekday, mode, coefs, start=None, end=None):
    _c1, _c2, _c3 = coefs
    nav_df = pd.DataFrame(nav_hist).set_index("date")
    tr_df = pd.DataFrame(trades, columns=["date", "code", "pool", "side", "price", "shares", "amount", "rank", "reason", "full", "is_new"])
    final = float(nav_df["nav"].iloc[-1])
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
    cagr = (final / config.start_capital) ** (1 / years) - 1 if years > 0 else 0.0
    peak = nav_df["nav"].cummax()
    mdd = float(((nav_df["nav"] - peak) / peak).min())
    rets = nav_df["nav"].pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(52)) if rets.std() > 0 else 0
    dn = rets[rets < 0]
    sortino = float(rets.mean() / dn.std() * np.sqrt(52)) if len(dn) and dn.std() > 0 else 0
    calmar = cagr / abs(mdd) if mdd != 0 else 0

    # 週期配對（PF / 勝率 / AvgWin / AvgLoss / 最大連虧 / Rank / MFE / MAE）
    cyc = trade_cycles(tr_df, close, config.buy_fee, config.sell_fee)
    wins = cyc[cyc["ret"] > 0] if len(cyc) else cyc
    losses = cyc[cyc["ret"] <= 0] if len(cyc) else cyc
    pf = profit_factor(cyc)
    winrate = len(wins)/len(cyc)*100 if len(cyc) else 0
    avgw = wins["ret"].mean()*100 if len(wins) else 0
    avgl = losses["ret"].mean()*100 if len(losses) else 0
    sgn = (cyc["ret"] > 0).astype(int).values if len(cyc) else np.array([])
    mw = ml = cw = cl = 0
    for s in sgn:
        if s: cw += 1; cl = 0
        else: cl += 1; cw = 0
        mw = max(mw, cw); ml = max(ml, cl)
    turnover = float(tr_df["amount"].sum() / nav_df["nav"].mean()) if len(tr_df) else 0
    n_tr = len(tr_df)

    tag = f"w{weekday}_{mode}_c{_c1:.2f}-{_c2:.2f}-{_c3:.2f}"
    if start is not None:
        tag += f"_{pd.Timestamp(start).year}-{pd.Timestamp(end or nav_df.index[-1]).year}"
    print(f"[{tag}] 最終 {final:,.0f} CAGR {cagr*100:.2f}% MDD {mdd*100:.2f}% PF {pf:.2f} 勝率 {winrate:.1f}%")
    # 額外統計
    max_win = float(cyc["ret"].max()*100) if len(cyc) else 0
    max_loss = float(cyc["ret"].min()*100) if len(cyc) else 0
    n20 = int((cyc["ret"] <= -0.20).sum()) if len(cyc) else 0
    n30 = int((cyc["ret"] <= -0.30).sum()) if len(cyc) else 0
    n100 = int((cyc["ret"] <= -0.99).sum()) if len(cyc) else 0
    day_ret_ = nav_df["nav"].pct_change().dropna()
    day_min = float(day_ret_.min()*100) if len(day_ret_) else 0
    contrib = {}
    if len(cyc):
        for pool in ["A", "B"]:
            sub = cyc[cyc["pool"] == pool]
            if not len(sub):
                continue
            w2 = sub[sub["ret"] > 0]; l2 = sub[sub["ret"] <= 0]
            contrib[pool] = {"n": int(len(sub)), "win": len(w2)/len(sub)*100,
                             "avg": sub["ret"].mean()*100, "sum": sub["ret"].sum()*100,
                             "pf": profit_factor(sub)}
    annual = {row["year"]: {"ret": row["ret"], "mdd": row["mdd"]} for row in annual_performance(nav_df, config.start_capital)}
    return {"tag": tag, "final": final, "cagr": cagr, "mdd": mdd, "sharpe": sharpe,
            "sortino": sortino, "calmar": calmar, "pf": pf, "winrate": winrate,
            "avgw": avgw, "avgl": avgl, "trades": n_tr, "turnover": turnover,
            "max_loss_run": ml, "nav": nav_df, "trades_df": tr_df,
            "cycles": cyc, "coefs": coefs, "max_win": max_win, "max_loss": max_loss,
            "n20": n20, "n30": n30, "n100": n100, "day_min": day_min,
            "contrib": contrib, "annual": annual}
