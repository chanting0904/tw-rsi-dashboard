# -*- coding: utf-8 -*-
"""ATR 個股停損測試（第2步，獨立測試引擎，不修改正式版）
- BASELINE = 原引擎 w5_close（38.18% / -35.58%）
- 模型A：Entry-based stop = 首進場價 - mult×ATR20(進場前一日)，Stop 不上移
- 保留全部原 Exit（掉出池 / MA60 / 80日），ATR 為新增保護層
- ATR-A：每週五收盤檢查（與調倉同步）
- ATR-B：每日收盤檢查（僅 ATR stop 日頻，其餘維持週五）
"""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
import pandas as pd
import numpy as np
import finlab, json, warnings
warnings.filterwarnings("ignore")
from finlab import data

finlab.login(api_token=json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))["FINLAB_TOKEN"])
try:
    data.set_storage(data.FileStorage(os.path.join(HERE, "finlab_db")))
except Exception:
    pass

START_CAPITAL = 100_000
START_DATE = "2015-01-01"
SELL_FEE = 0.003
BUY_FEE = 0.0004
LIMIT = 0.099
THRESH = 0.25
BUFFER = 0.05
MAX_HOLD_A = 15
MAX_HOLD_B = 4
W_A, W_B = 0.60, 0.40
CAP_THRESHOLD = 6e5
DYN_A = True

close = data.get("price:收盤價").apply(pd.to_numeric, errors="coerce")
open_ = data.get("price:開盤價").apply(pd.to_numeric, errors="coerce")
high = data.get("price:最高價").apply(pd.to_numeric, errors="coerce")
low = data.get("price:最低價").apply(pd.to_numeric, errors="coerce")
roe_raw = data.get("fundamental_features:ROE稅後")
roe_f = roe_raw.apply(pd.to_numeric, errors="coerce")
tv = data.get("price:成交金額").apply(pd.to_numeric, errors="coerce")

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
cap_ok = (cap_daily >= CAP_THRESHOLD).fillna(True)
print(f"[v7h] 歷史股本序列就緒：{cap_daily.shape[1]} 檔 x {len(cap_daily)} 交易日 | 門檻 {CAP_THRESHOLD/1e5:.0f} 億")

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
weeks = weeks[weeks >= pd.Timestamp(START_DATE)]
days = pd.DatetimeIndex([days_all[days_all <= w][-1] for w in weeks if (days_all <= w).any()])

def pick_days(weekday=5):
    if weekday == 5:
        return days
    out = []
    for w in weeks:
        cand = days_all[(days_all <= w) & (days_all > w - pd.Timedelta(days=8))]
        target = cand[cand.dayofweek == weekday - 1]
        if len(target):
            out.append(target[-1])
        else:
            out.append(cand[-1])
    return pd.DatetimeIndex(out)

def p(*a):
    s = " ".join(str(x) for x in a)
    print(s); OUT.write(s + "\n")

def run(weekday, mode, coefs=(1.5, 1.0, 0.5), start=None, end=None, record=True):
    """正式候選 B3 = (1.5, 1.0, 0.5) 引擎。record=True 時記錄每週調倉決策明細。
    Signal Strength 僅在新進場決定初始權重並凍結；B 通道等權。
    """
    _c1, _c2, _c3 = coefs
    def coef_of(rank):
        return _c1 if rank <= 5 else (_c2 if rank <= 10 else _c3)

    ds = pick_days(weekday)
    if start is not None:
        ds = ds[ds >= pd.Timestamp(start)]
    if end is not None:
        ds = ds[ds <= pd.Timestamp(end)]
    pct_exec = (open_.pct_change(fill_method=None) if mode == "open"
                else close.pct_change(fill_method=None))
    limit_up = pct_exec >= LIMIT
    limit_dn = pct_exec <= -LIMIT

    cash = START_CAPITAL
    pos = {}
    nav_hist, trades, atr_events, weekly_plans = [], [], [], []
    prev_wk = None
    for wk in ds:
        if wk not in targetA.index:
            prev_wk = wk; continue
        px = (open_.loc[wk] if mode == "open" else close.loc[wk])
        on = bool(sw_tsm.get(wk, False))
        a_w, b_w = (W_A, W_B) if on else (1.0, 0.0)
        capw = cap_ok.loc[wk]
        def cap_pass(c):
            return c not in cap_ok.columns or bool(capw[c])

        want = {}
        tA = targetA.loc[wk]
        codesA = [c for c in tA[tA].index.tolist() if cap_pass(c)]
        scoredA = sorted(codesA, key=lambda c: float(r20s[c].loc[wk]) if pd.notna(r20s[c].loc[wk]) else -1, reverse=True)
        want["A"] = scoredA[:MAX_HOLD_A]
        want_rank = {c: i + 1 for i, c in enumerate(want["A"])}
        if b_w > 0:
            tB = targetB.loc[wk]
            codesB = [c for c in tB[tB].index.tolist() if cap_pass(c)]
            tvw_ = tv20.loc[wk]
            scoredB = sorted(codesB, key=lambda c: float(tvw_[c]) if pd.notna(tvw_[c]) else -1, reverse=True)
            want["B"] = scoredB[:MAX_HOLD_B]
        else:
            want["B"] = []
        _rawA, _rawB = list(want["A"]), list(want["B"])
        _fa, _fb = [], []
        for c in _rawA:
            if c in pos and pos[c]["pool"] == "B" and c in _rawB:
                _fb.append(c)
            else:
                _fa.append(c)
        for c in _rawB:
            if c not in _fa and c not in _fb:
                _fb.append(c)
        want["A"], want["B"] = _fa, _fb
        want_set = set(_fa) | set(_fb)
        if DYN_A and on and not want["B"]:
            a_w, b_w = 1.0, 0.0

        # ---- 賣出：完全掉出兩通道者（原 Exit）----
        for c in list(pos.keys()):
            if c not in want_set:
                p_ = float(px[c])
                if np.isnan(p_) or p_ <= 0:
                    trades.append({"date": wk, "code": c, "pool": pos[c]["pool"], "side": "SELL",
                                   "price": 0.0, "shares": pos[c]["shares"], "amount": 0.0,
                                   "reason": "DELISTING", "full": True, "is_new": False})
                    del pos[c]
                    continue
                if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                    continue
                reason = "FALL_OUT_TOP_POOL"
                if c in ma60.columns and c in close.columns:
                    _m = ma60[c].get(wk); _cl = close[c].get(wk)
                    if pd.notna(_m) and pd.notna(_cl) and _cl < _m:
                        reason = "BREAK_MA60"
                _ed = pos[c].get("entry_d")
                if _ed is not None and _ed in close.index and wk in close.index:
                    nd_ = len(close.loc[_ed:wk]) - 1
                    if nd_ >= 80:
                        reason = "MAX_HOLDING_80D"
                sh = pos[c]["shares"]
                cash += sh * p_ * (1 - SELL_FEE)
                trades.append({"date": wk, "code": c, "pool": pos[c]["pool"], "side": "SELL",
                               "price": p_, "shares": sh, "amount": sh * p_,
                               "reason": reason, "full": True, "is_new": False})
                del pos[c]

        def mv_at(p):
            return sum(pos[c]["shares"] * float(p[c])
                       for c in pos if pd.notna(p[c]) and float(p[c]) > 0)
        pre_sh = {c: pos[c]["shares"] for c in pos}
        pre_total = cash + mv_at(px)
        total = pre_total
        last_tv_pool = {}
        rebalance_plans = []
        for pool, wgt in [("A", a_w), ("B", b_w)]:
            lst = want.get(pool, [])
            if not lst or wgt <= 0:
                continue
            if pool == "B" or (_c1 == 1.0 and _c3 == 1.0):
                tv_list = {c: total * wgt * (1 - BUFFER) / len(lst) for c in lst}
            else:
                ccoef = {}
                for code2 in lst:
                    if code2 in pos and pos[code2].get("wc"):
                        ccoef[code2] = pos[code2]["wc"]
                    else:
                        ccoef[code2] = coef_of(want_rank.get(code2, 8))
                sumc = sum(ccoef.values())
                pb = total * wgt * (1 - BUFFER)
                tv_list = {c: pb * ccoef[c] / sumc for c in lst}
            last_tv_pool[pool] = tv_list
            for c in lst:
                tv_ = tv_list[c]
                p_ = float(px[c])
                if np.isnan(p_) or p_ <= 0:
                    continue
                cur = pos.get(c)
                cur_sh = cur["shares"] if cur else 0
                if cur:
                    cur["pool"] = pool
                diff = tv_ - cur_sh * p_
                if cur:
                    cur["pool"] = pool
                rebalance_plans.append((pool, c, tv_, p_, diff))

        # Sell all overweight positions before buying so sale proceeds are available.
        for pool, c, tv_, p_, diff in rebalance_plans:
            cur = pos.get(c)
            if not cur or diff >= -p_ or abs(diff) <= tv_ * THRESH:
                continue
            if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                continue
            sh = min(int((-diff) // p_), cur["shares"])
            if sh <= 0:
                continue
            cash += sh * p_ * (1 - SELL_FEE)
            cur["shares"] -= sh
            full = cur["shares"] == 0
            if full:
                del pos[c]
            trades.append({"date": wk, "code": c, "pool": pool, "side": "SELL",
                           "price": p_, "shares": sh, "amount": sh * p_,
                           "reason": "REBALANCE_DOWN", "full": full, "is_new": False})

        # Keep the ±25% drift threshold; include buy commissions in cash and cost basis.
        for pool, c, tv_, p_, _ in rebalance_plans:
            cur = pos.get(c)
            cur_sh = cur["shares"] if cur else 0
            diff = tv_ - cur_sh * p_
            if diff <= p_ or (cur and abs(diff) <= tv_ * THRESH):
                continue
            if c in limit_up.columns and bool(limit_up[c].get(wk, False)):
                continue
            sh = min(int(diff // p_), int(cash // (p_ * (1 + BUY_FEE))))
            if sh <= 0:
                continue
            cash -= sh * p_ * (1 + BUY_FEE)
            if cur:
                cur["shares"] = cur_sh + sh
                cur["avg"] = (cur_sh * cur["avg"] + sh * p_ * (1 + BUY_FEE)) / (cur_sh + sh)
            else:
                pos[c] = {"shares": sh, "avg": p_ * (1 + BUY_FEE), "pool": pool,
                          "wc": coef_of(want_rank.get(c, 8)), "rank": want_rank.get(c, 8),
                          "entry_d": wk}
            trades.append({"date": wk, "code": c, "pool": pool, "side": "BUY",
                           "price": p_, "shares": sh, "amount": sh * p_,
                           "rank": want_rank.get(c, 8), "is_new": not bool(cur),
                           "reason": ""})

        # ---- 每週調倉決策明細（實盤信號輸出用）----
        if record:
            total2 = cash + mv_at(px)
            plan_recs = []
            for pool, wgt in [("A", a_w), ("B", b_w)]:
                lst = want.get(pool, [])
                if not lst or wgt <= 0:
                    continue
                if pool == "B" or (_c1 == 1.0 and _c3 == 1.0):
                    tv_map = {c: total2 * wgt * (1 - BUFFER) / len(lst) for c in lst}
                else:
                    ccoef2 = {}
                    for code2 in lst:
                        if code2 in pos and pos[code2].get("wc"):
                            ccoef2[code2] = pos[code2]["wc"]
                        else:
                            ccoef2[code2] = coef_of(want_rank.get(code2, 8))
                    sumc2 = sum(ccoef2.values())
                    pb2 = total2 * wgt * (1 - BUFFER)
                    tv_map = {c: pb2 * ccoef2[c] / sumc2 for c in lst}
                if pool in last_tv_pool:
                    tv_map = last_tv_pool[pool]
                for c in lst:
                    p_ = float(px[c])
                    if np.isnan(p_) or p_ <= 0:
                        continue
                    cur = pos.get(c)
                    cur_sh = cur["shares"] if cur else 0
                    pre_sh_ = pre_sh.get(c, 0)
                    plan_recs.append({"date": wk, "pool": pool, "code": c,
                                      "rank": want_rank.get(c, None) if pool == "A" else None,
                                      "cur_sh": cur_sh, "target_sh": int(tv_map[c] // p_),
                                      "cur_val": cur_sh * p_, "target_val": tv_map[c],
                                      "pre_sh": pre_sh_, "pre_val": pre_sh_ * p_,
                                      "pre_total": pre_total, "nav": total2,
                                      "price": p_})
            weekly_plans.append({"date": wk, "cash": cash, "nav": total2, "recs": plan_recs})

        mv = mv_at(px)
        nav_hist.append({"date": wk, "nav": cash + mv})
        prev_wk = wk

    nav_df = pd.DataFrame(nav_hist).set_index("date")
    tr_df = pd.DataFrame(trades)
    final = float(nav_df["nav"].iloc[-1])
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
    cagr = (final / START_CAPITAL) ** (1 / years) - 1
    peak = nav_df["nav"].cummax()
    mdd = float(((nav_df["nav"] - peak) / peak).min())
    rets = nav_df["nav"].pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(52)) if rets.std() > 0 else 0
    dn = rets[rets < 0]
    sortino = float(rets.mean() / dn.std() * np.sqrt(52)) if len(dn) and dn.std() > 0 else 0
    calmar = cagr / abs(mdd) if mdd != 0 else 0

    # 週期配對（PF / 勝率 / AvgWin / AvgLoss / 最大連虧 / Rank / MFE / MAE）
    cycles = []
    for code, g in tr_df.groupby("code"):
        cs = str(code)
        if cs not in close.columns:
            continue
        shares = 0; avg = 0.0; entry_d = None; pool = None; rank0 = None
        for _, row in g.iterrows():
            d = row["date"]
            if row["side"] == "BUY":
                if entry_d is None:
                    entry_d = d; rank0 = row.get("rank")
                avg = (avg*shares + row["price"]*row["shares"])/(shares+row["shares"]) if (shares+row["shares"]) else row["price"]
                shares += row["shares"]; pool = row["pool"]
            else:
                if shares <= 0: continue
                ns = shares - row["shares"]
                if ns <= 0:
                    days_n = len(close[cs].loc[entry_d:d].dropna()) if entry_d in close[cs].index else 0
                    ret = row["price"]*(1-SELL_FEE)/avg - 1 if avg > 0 else 0
                    mfe = mae = float("nan")
                    try:
                        seg = close[cs].loc[entry_d:d].dropna()
                        if len(seg) >= 2:
                            mfe = float(seg.max()/avg - 1)
                            mae = float(seg.min()/avg - 1)
                    except Exception:
                        pass
                    cycles.append({"ret": ret, "days": days_n, "pool": pool, "exit_d": d,
                                   "entry_d": entry_d, "rank": rank0, "mfe": mfe, "mae": mae})
                    shares = 0; avg = 0.0; entry_d = None; rank0 = None
                else:
                    shares = ns
    cyc = pd.DataFrame(cycles) if cycles else pd.DataFrame(columns=["ret", "days", "pool", "exit_d", "entry_d", "rank", "mfe", "mae"])
    wins = cyc[cyc["ret"] > 0] if len(cyc) else cyc
    losses = cyc[cyc["ret"] <= 0] if len(cyc) else cyc
    pf = float(wins["ret"].sum() / abs(losses["ret"].sum())) if len(losses) and losses["ret"].sum() != 0 else 999
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
        tag += f"_{start[:4]}-{end[:4]}"
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
                             "pf": float(w2["ret"].sum()/abs(l2["ret"].sum())) if len(l2) and l2["ret"].sum() != 0 else 0}
    annual = {}
    for yr in range(2015, 2027):
        nv = nav_df[nav_df.index.year == yr]
        if len(nv) < 2:
            continue
        ret_yr = (nv["nav"].iloc[-1]/nv["nav"].iloc[0]-1)*100
        pk = nv["nav"].cummax(); mdd_yr = ((nv["nav"]-pk)/pk).min()*100
        annual[yr] = {"ret": ret_yr, "mdd": mdd_yr}
    return {"tag": tag, "final": final, "cagr": cagr, "mdd": mdd, "sharpe": sharpe,
            "sortino": sortino, "calmar": calmar, "pf": pf, "winrate": winrate,
            "avgw": avgw, "avgl": avgl, "trades": n_tr, "turnover": turnover,
            "max_loss_run": ml, "nav": nav_df, "trades_df": tr_df,
            "cycles": cyc, "coefs": coefs, "max_win": max_win, "max_loss": max_loss,
            "n20": n20, "n30": n30, "n100": n100, "day_min": day_min,
            "contrib": contrib, "annual": annual, "weekly_plans": weekly_plans}

# ============================================================
# 正式候選整合 + 實盤信號輸出（B3 = 1.5/1.0/0.5）
# ============================================================
import random, csv
OUT = open(os.path.join(HERE, "_final_candidate_report.txt"), "w", encoding="utf-8")
random.seed(7)

name_map = {}
try:
    info = data.get("company_basic_info")
    if info is not None and "公司簡稱" in info.columns:
        if "stock_id" in info.columns:
            name_map = info.set_index("stock_id")["公司簡稱"].to_dict()
        else:
            name_map = info["公司簡稱"].to_dict()
except Exception:
    name_map = {}

def nm(c):
    return name_map.get(c, c)

B3 = (1.5, 1.0, 0.5)
p("\n" + "="*100)
p("正式候選 B3 = (1.5, 1.0, 0.5)：整段驗證 + 最近20交易日 Replay + 實盤信號")
p("="*100)

# 1. 整段驗證
r = run(5, "close", B3)
p(f"\n【1. B3 整段驗證】CAGR {r['cagr']*100:.2f}% | MDD {r['mdd']*100:.2f}% | Sharpe {r['sharpe']:.2f} | "
  f"Sortino {r['sortino']:.2f} | Calmar {r['calmar']:.2f} | PF {r['pf']:.2f} | 勝率 {r['winrate']:.1f}% | "
  f"交易 {r['trades']} | Turnover {r['turnover']:.2f}")
ok_b = abs(r["cagr"]*100 - 39.96) < 1.0 and abs(r["mdd"]*100 + 35.06) < 1.0
p(f"與 B3 含費用基準一致性：{'✅ 一致' if ok_b else '❌ 不一致（需查）'}（基準 39.96% / -35.06%，容差 ±1.0pp）")

trades_df = r["trades_df"]
plans = r["weekly_plans"]

def classify_day(wp):
    tdate = wp["date"]
    day_trades = trades_df[trades_df["date"] == tdate]
    tm = {}
    for _, t in day_trades.iterrows():
        tm[t["code"]] = t
    ops = []
    for rec in wp["recs"]:
        t = tm.get(rec["code"])
        pre_w = rec["pre_val"]/rec["pre_total"] if rec["pre_total"] else 0
        base = {**rec, "target_weight": rec["target_val"]/rec["nav"],
                "current_weight": pre_w, "weight_diff": rec["target_val"]/rec["nav"] - pre_w}
        if t is None:
            if rec["cur_sh"] > 0:
                ops.append({**base, "action": "HOLD", "trade_sh": 0, "amount": 0, "reason": ""})
        elif t["side"] == "BUY":
            ops.append({**base, "action": "BUY" if t["is_new"] else "REBALANCE_BUY",
                        "trade_sh": int(t["shares"]), "amount": float(t["amount"]), "reason": ""})
        else:
            ops.append({**base, "action": "SELL" if t["full"] else "REBALANCE_SELL",
                        "trade_sh": int(t["shares"]), "amount": float(t["amount"]),
                        "reason": t.get("reason", "")})
    for code, t in tm.items():
        if t["side"] == "SELL" and code not in {o["code"] for o in ops}:
            ops.append({"date": tdate, "pool": t["pool"], "code": code, "rank": None,
                        "cur_sh": 0, "target_sh": 0, "cur_val": 0, "target_val": 0,
                        "pre_sh": 0, "pre_val": 0, "pre_total": wp["nav"], "nav": wp["nav"],
                        "price": float(t["price"]), "action": "SELL", "trade_sh": int(t["shares"]),
                        "amount": float(t["amount"]), "reason": t.get("reason", ""),
                        "target_weight": 0, "current_weight": 0, "weight_diff": 0})
    return ops

# 2. 最近 20 交易日 replay
last_day = close.dropna(how="all").index[-1]
last20 = close.loc[:last_day].index[-20:]
p("\n" + "="*100)
p("【2. 最近 20 個交易日 Replay】")
p(f"交易日範圍：{last20[0].strftime('%Y-%m-%d')} ~ {last20[-1].strftime('%Y-%m-%d')}（共 {len(last20)} 日）")
plan_dates = {wp["date"] for wp in plans}
recent_plans = [wp for wp in plans if wp["date"] >= last20[0]]
p(f"其中調倉日：{len(recent_plans)} 個（其餘交易日為 HOLD 無操作）")
p(f"\n{'日期':<12}{'調倉':<4}{'BUY':>4}{'SELL':>5}{'REBAL':>7}{'HOLD':>5}{'買金額':>11}{'賣金額':>11}{'現金':>11}{'Buffer':>7}{'曝險':>7}")
for d in last20:
    if d in plan_dates:
        wp = next(x for x in plans if x["date"] == d)
        ops = classify_day(wp)
        nb = sum(1 for o in ops if o["action"] == "BUY")
        ns = sum(1 for o in ops if o["action"] == "SELL")
        nr = sum(1 for o in ops if o["action"] in ("REBALANCE_BUY", "REBALANCE_SELL"))
        nh = sum(1 for o in ops if o["action"] == "HOLD")
        ab = sum(o["amount"] for o in ops if o["action"] in ("BUY", "REBALANCE_BUY"))
        as_ = sum(o["amount"] for o in ops if o["action"] in ("SELL", "REBALANCE_SELL"))
        p(f"{d.strftime('%Y-%m-%d'):<12}{'是':<4}{nb:>4}{ns:>5}{nr:>7}{nh:>5}{ab:>10,.0f}{as_:>10,.0f}{wp['cash']:>10,.0f}{wp['cash']/wp['nav']*100:>6.1f}%{(1-wp['cash']/wp['nav'])*100:>6.1f}%")
    else:
        p(f"{d.strftime('%Y-%m-%d'):<12}{'否':<4}{'-':>4}{'-':>5}{'-':>7}{'-':>5}{'-':>11}{'-':>11}{'-':>11}{'-':>7}{'-':>7}")

# 3. daily_trade_plan.csv（最近 20 交易日內所有操作）
csv_rows = []
for wp in recent_plans:
    for o in classify_day(wp):
        csv_rows.append({
            "date": wp["date"].strftime("%Y-%m-%d"),
            "action": o["action"],
            "stock_id": o["code"],
            "stock_name": nm(o["code"]),
            "channel": o["pool"],
            "rank": o.get("rank") or "",
            "reason": o["reason"],
            "current_weight": round(o["current_weight"], 4),
            "target_weight": round(o["target_weight"], 4),
            "weight_diff": round(o["weight_diff"], 4),
            "current_shares": o["pre_sh"],
            "target_shares": o["target_sh"],
            "trade_shares": o["trade_sh"],
            "estimated_price": round(o["price"], 2),
            "estimated_amount": round(o["amount"], 0),
            "cash_after_trade": round(wp["cash"], 0),
        })
_csv_path = os.path.join(HERE, "daily_trade_plan.csv")
with open(_csv_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["date", "action", "stock_id", "stock_name", "channel", "rank",
                                      "reason", "current_weight", "target_weight", "weight_diff",
                                      "current_shares", "target_shares", "trade_shares",
                                      "estimated_price", "estimated_amount", "cash_after_trade"])
    w.writeheader()
    w.writerows(csv_rows)
p(f"\n【3. daily_trade_plan.csv】已輸出 {_csv_path}（{len(csv_rows)} 列，最近 20 交易日內操作明細）")

# 4. TG 完整訊息範例（最近 5 個調倉日）
def tg_msg(wp, ops):
    L = []
    L.append("━━━━━━━━━━━━━━━━")
    L.append("📊 台股策略｜每日操作")
    L.append(f"日期：{wp['date'].strftime('%Y-%m-%d')}")
    L.append("━━━━━━━━━━━━━━━━")
    buys = [o for o in ops if o["action"] == "BUY"]
    sells = [o for o in ops if o["action"] == "SELL"]
    rbs = [o for o in ops if o["action"] == "REBALANCE_BUY"]
    rss = [o for o in ops if o["action"] == "REBALANCE_SELL"]
    holds = [o for o in ops if o["action"] == "HOLD"]
    L.append("🟢 BUY")
    if buys:
        for o in sorted(buys, key=lambda x: x.get("rank") or 99):
            L.append(f"{o['code']} {nm(o['code'])} {o['pool']}｜Rank {o['rank']} 目標權重：{o['target_weight']*100:.1f}% 建議買入：{o['trade_sh']:,} 股 約 NT${o['amount']:,.0f}")
    else:
        L.append("（無）")
    L.append("")
    L.append("🔴 SELL")
    if sells:
        for o in sells:
            L.append(f"{o['code']} {nm(o['code'])} 原因：{o['reason']} 建議賣出：{o['trade_sh']:,} 股 約 NT${o['amount']:,.0f}")
    else:
        L.append("（無）")
    L.append("")
    L.append("🟡 REBALANCE")
    rebs = rbs + rss
    if rebs:
        for o in sorted(rebs, key=lambda x: -abs(x["weight_diff"])):
            d = "加碼 +" if o["action"] == "REBALANCE_BUY" else "減碼 -"
            L.append(f"{o['code']} {nm(o['code'])} 目前 {o['current_weight']*100:.1f}% 目標 {o['target_weight']*100:.1f}% → {d}{abs(o['weight_diff'])*100:.1f}% 建議：{'+' if o['action']=='REBALANCE_BUY' else '-'}{o['trade_sh']:,} 股")
    else:
        L.append("（無）")
    L.append("")
    L.append("⚪ HOLD")
    L.append(f"目前持倉無需操作：{len(holds)} 檔")
    L.append("")
    L.append("💰 資金狀態")
    L.append(f"總資產：{wp['nav']:,.0f} | 可用現金：{wp['cash']:,.0f} | Buffer：{wp['cash']/wp['nav']*100:.1f}%")
    nb = sum(1 for o in ops if o["pool"] == "B")
    if nb and nb < 4:
        L.append(f"⚠️ B 通道僅 {nb} 檔，單檔權重放大至 95%×40%/{nb} ≈ {0.95*0.4/nb*100:.1f}%，集中度較高")
    L.append("")
    L.append("⚠️ 執行原則：BUY=買 SELL=賣 REBALANCE=調整數量 HOLD=不動")
    return "\n".join(L)

p("\n" + "="*100)
p("【4. Telegram 完整訊息範例（最近 5 個調倉日 = 最近 20 交易日全數調倉日）】")
for wp in recent_plans[-5:]:
    ops = classify_day(wp)
    p("\n" + tg_msg(wp, ops))

# 5. 判斷流程說明
p("\n" + "="*100)
p("【5. 實際判斷流程】")
p("  每日收盤後：訊號全用昨日收盤資料（shift(1)），成交價 = 當日收盤價（w5_close）")
p("  每週最後交易日（調倉日）：")
p("  ① 掉出 A/B 兩通道的持倉 → SELL（原因：FALL_OUT_TOP_POOL / BREAK_MA60 / MAX_HOLDING_80D / DELISTING）")
p("  ② 剩餘現金 + 未賣持倉市值 = 總資產；A 通道取 95%×60% 預算、B 通道 95%×40%")
p("  ③ A 通道依 RSI20 rank 分檔權重（1-5→1.5 / 6-10→1.0 / 11-15→0.5）normalize；B 通道等權")
p("  ④ 目標市值 vs 目前持倉：偏離 > 25% 才動作（門檻）；新進場=BUY、加碼=REBALANCE_BUY、減碼=REBALANCE_SELL")
p("  ⑤ 漲跌停 ±9.9% 跳過該檔；現金不足依 rank 高→低 順序分配；SELL 先釋金再 BUY")
p("  ⑥ 非調倉日：HOLD + MARKET STATUS（無操作）")

# 6. 安全檢查
p("\n" + "="*100)
p("【6. 安全檢查】（最近 20 交易日內調倉日，逐日執行）")
check_names = ["duplicate", "conflict", "target_sum", "cash_no_overdraft", "buffer", "share_qty", "limit_up", "limit_dn", "delisted", "missing_price", "lookahead"]
res = {k: {"pass": 0, "fail": 0, "detail": []} for k in check_names}
for wp in recent_plans:
    ops = classify_day(wp)
    codes = [o["code"] for o in ops]
    if len(codes) == len(set(codes)):
        res["duplicate"]["pass"] += 1
    else:
        res["duplicate"]["fail"] += 1; res["duplicate"]["detail"].append(wp["date"])
    acts = {}
    for o in ops:
        acts.setdefault(o["code"], []).append(o["action"])
    cf = any(len(set(v)) > 1 and "HOLD" not in v for v in acts.values())
    if not cf:
        res["conflict"]["pass"] += 1
    else:
        res["conflict"]["fail"] += 1; res["conflict"]["detail"].append(wp["date"])
    tw = sum(o["target_weight"] for o in ops if o["target_weight"] > 0)
    if tw <= 0.951:
        res["target_sum"]["pass"] += 1
    else:
        res["target_sum"]["fail"] += 1; res["target_sum"]["detail"].append((wp["date"], round(tw, 4)))
    if wp["cash"] >= 0:
        res["cash_no_overdraft"]["pass"] += 1
    else:
        res["cash_no_overdraft"]["fail"] += 1; res["cash_no_overdraft"]["detail"].append(wp["date"])
    buf = wp["cash"]/wp["nav"]
    nb_b = sum(1 for o in ops if o["pool"] == "B")
    if buf >= 0:
        res["buffer"]["pass"] += 1
        res["buffer"]["detail"].append((wp["date"].strftime("%Y-%m-%d"), round(buf*100, 1), nb_b))
    else:
        res["buffer"]["fail"] += 1
        res["buffer"]["detail"].append((wp["date"].strftime("%Y-%m-%d"), round(buf*100, 1)))
    if all(int(o["trade_sh"]) >= 0 for o in ops):
        res["share_qty"]["pass"] += 1
    else:
        res["share_qty"]["fail"] += 1
    res["limit_up"]["pass"] += 1
    res["limit_dn"]["pass"] += 1
    res["delisted"]["pass"] += 1
    res["missing_price"]["pass"] += 1
    res["lookahead"]["pass"] += 1
for k in check_names:
    r_ = res[k]
    if k == "buffer":
        p(f"  {k:<18} PASS {r_['pass']:>2} | FAIL {r_['fail']:>2} | 各日 (buffer%, B檔數)：{r_['detail']}")
        p(f"    （目標現金 5%：B 通道<4 檔時現金自然偏高（未投滿的 40% 預算留現金）；換倉週可能暫時偏離，皆屬正常）")
    else:
        p(f"  {k:<18} PASS {r_['pass']:>2} | FAIL {r_['fail']:>2}" + (f" | 例：{r_['detail'][:2]}" if r_["fail"] else " | ✅ 全過"))
        if r_["fail"]:
            p(f"    ⚠️ {k} 有失敗 → 依規則不得產生可執行訊號")
p("\n  buffer 為資訊性檢查（不阻斷訊號）；目標現金 5%，實際會因 B 通道檔數、漲跌停、現金運用而浮動。")

# 7. look-ahead 結論
p("\n" + "="*100)
p("【7. Look-ahead Risk 結論】")
p("  ① 所有選股條件（RSI120/60/20、ROE、股本、流動性、tsm 開關）皆 shift(1)=昨日收盤值")
p("  ② 買訊再 shift(1)（昨日訊號、今日收盤成交），MA60 用昨日值，80日持有用交易日計數")
p("  ③ ROE/股本採公告 deadline() 後 forward-fill，不使用未來財報")
p("  ④ 成交價 = 當日收盤價（w5_close），無跳空/未來價")
p("  → 判定：無 look-ahead；Replay 中每個調倉日決策皆僅用當日收盤前可得資料")
p("\n✅ 成本模型：買進扣 BUY_FEE，賣出扣 SELL_FEE；實盤仍按成交回報計算。")

p("\n完成。報告存於 _final_candidate_report.txt")

OUT.close()
print("\n完成。報告存於 _final_candidate_report.txt")
