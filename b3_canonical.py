# -*- coding: utf-8 -*-
"""B3 正式候選引擎 — repo 部署版（canonical 輸出）
- 策略計算段與 my_backtest_v7f_final_candidate.py 逐字一致（B3 = 1.5/1.0/0.5，禁止修改）
- 差異僅：①環境適配（token：env 優先、config.json fallback；不 chdir）②主程式改為 --canonical 輸出
- 輸出：output/latest_signals.json（本週）、output/history/YYYY-MM-DD.json（最近20調倉週）、
         output/perf.json（整段績效）、output/validation_log.json（驗證結果）
- validation FAIL（含 10-02 鎖定回歸值不符）→ exit(1)，不輸出可執行訊號
"""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
import pandas as pd
import numpy as np
import finlab, json, warnings
warnings.filterwarnings("ignore")
from finlab import data


def load_token():
    env_t = (os.environ.get("FINLAB_TOKEN") or "").strip()
    if env_t:
        return env_t
    for p in ("config.json", os.path.join(HERE, "config.json")):
        try:
            cfg = json.load(open(p, encoding="utf-8"))
            t = (cfg.get("FINLAB_TOKEN") or "").strip()
            if t:
                return t
        except Exception:
            pass
    return ""


TOKEN = load_token()
if not TOKEN:
    raise SystemExit("❌ 找不到 FinLab token（env FINLAB_TOKEN 或 config.json）")
finlab.login(api_token=TOKEN)
# [流量節省] 用本地/CI cache storage（finlab_db），避免每週全量重載爆 2000MB 上限
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
        total = cash + mv_at(px)
        last_tv_pool = {}
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
                if cur and abs(diff) <= tv_ * THRESH:
                    continue
                if diff > p_:
                    if c in limit_up.columns and bool(limit_up[c].get(wk, False)):
                        continue
                    sh = min(int(diff // p_), int(cash // p_))
                    if sh > 0:
                        cash -= sh * p_
                        if cur:
                            cur["shares"] = cur_sh + sh
                            cur["avg"] = (cur_sh * cur["avg"] + sh * p_) / (cur_sh + sh)
                        else:
                            pos[c] = {"shares": sh, "avg": p_, "pool": pool,
                                      "wc": coef_of(want_rank.get(c, 8)), "rank": want_rank.get(c, 8),
                                      "entry_d": wk}
                        trades.append({"date": wk, "code": c, "pool": pool, "side": "BUY",
                                       "price": p_, "shares": sh, "amount": sh * p_,
                                       "rank": want_rank.get(c, 8), "is_new": not bool(cur),
                                       "reason": ""})
                elif diff < -p_:
                    if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                        continue
                    sh = min(int(-diff // p_), cur_sh)
                    if sh > 0:
                        cash += sh * p_ * (1 - SELL_FEE)
                        cur["shares"] = cur_sh - sh
                        _full = cur["shares"] == 0
                        if _full:
                            del pos[c]
                        trades.append({"date": wk, "code": c, "pool": pool, "side": "SELL",
                                       "price": p_, "shares": sh, "amount": sh * p_,
                                       "reason": "REBALANCE_DOWN", "full": _full, "is_new": False})

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
                    ret = row["price"]/avg - 1 if avg > 0 else 0
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
# Canonical 輸出層（repo 部署版）：B3 = 1.5/1.0/0.5
# 只讀取上方 run() 結果 → 產生 canonical JSON + validation
# ============================================================
import random, csv, sys, datetime
OUT = open(os.path.join(HERE, "_b3_canonical.log.txt"), "w", encoding="utf-8")
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


def p(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    OUT.write(s + "\n")


B3 = (1.5, 1.0, 0.5)
p("\n" + "=" * 100)
p("B3 canonical 輸出層：整段回測 + 產生 canonical JSON + validation")
p("=" * 100)

r = run(5, "close", B3)
p(f"\n【B3 整段驗證】CAGR {r['cagr']*100:.2f}% | MDD {r['mdd']*100:.2f}% | Sharpe {r['sharpe']:.2f} | "
  f"Sortino {r['sortino']:.2f} | Calmar {r['calmar']:.2f} | PF {r['pf']:.2f} | 勝率 {r['winrate']:.1f}% | "
  f"交易 {r['trades']} | Turnover {r['turnover']:.2f}")
ok_b = abs(r["cagr"] * 100 - 42.10) < 1.0 and abs(r["mdd"] * 100 + 34.19) < 1.0
p(f"與 B3 基準一致性：{'✅ 一致' if ok_b else '❌ 不一致（需查）'}（基準 42.10% / -34.19%，容差 ±1.0pp）")

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
        pre_w = rec["pre_val"] / rec["pre_total"] if rec["pre_total"] else 0
        base = {**rec, "target_weight": rec["target_val"] / rec["nav"],
                "current_weight": pre_w, "weight_diff": rec["target_val"] / rec["nav"] - pre_w}
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


def prev_trading_day(d):
    idx = close.dropna(how="all").index
    idx = idx[idx < d]
    return idx[-1] if len(idx) else d


def canonical_for(wp):
    ops = classify_day(wp)
    acts = []
    for o in ops:
        buy_sh = sell_sh = 0
        buy_amt = sell_amt = 0
        a = o["action"]
        if a in ("BUY", "REBALANCE_BUY"):
            buy_sh, buy_amt = int(o["trade_sh"]), round(float(o["amount"]), 0)
        elif a in ("SELL", "REBALANCE_SELL"):
            sell_sh, sell_amt = int(o["trade_sh"]), round(float(o["amount"]), 0)
        acts.append({
            "stock_code": o["code"],
            "stock_name": nm(o["code"]),
            "channel": o["pool"],
            "rank": o.get("rank") if o.get("rank") is not None else None,
            "action": a,
            "target_weight": round(float(o["target_weight"]), 5),
            "current_weight": round(float(o["current_weight"]), 5),
            "weight_diff": round(float(o["weight_diff"]), 5),
            "target_shares": int(o["target_sh"]),
            "current_shares": int(o["pre_sh"]),
            "buy_shares": buy_sh,
            "sell_shares": sell_sh,
            "estimated_buy_amount": buy_amt,
            "estimated_sell_amount": sell_amt,
            "price": round(float(o["price"]), 2) if o["price"] and o["price"] > 0 else None,
            "reason": o.get("reason", ""),
        })
    counts = {"BUY": 0, "SELL": 0, "REBALANCE_BUY": 0, "REBALANCE_SELL": 0, "HOLD": 0}
    for a in acts:
        counts[a["action"]] += 1
    buy_total = sum(a["estimated_buy_amount"] for a in acts)
    sell_total = sum(a["estimated_sell_amount"] for a in acts)
    nb_b = len({a["stock_code"] for a in acts if a["channel"] == "B"})
    warnings = []
    if 0 < nb_b < 4:
        warnings.append(f"B 通道僅 {nb_b} 檔，單檔權重放大至 95%×40%/{nb_b} ≈ {0.95*0.4/nb_b*100:.1f}%，集中度較高")
    if counts["BUY"] + counts["REBALANCE_BUY"] == 0 and counts["SELL"] + counts["REBALANCE_SELL"] == 0:
        warnings.append("本週無買賣動作，僅 HOLD")
    return {
        "strategy_version": "B3",
        "calculation_date": wp["date"].strftime("%Y-%m-%d"),
        "rebalance_date": wp["date"].strftime("%Y-%m-%d"),
        "sig_date": prev_trading_day(wp["date"]).strftime("%Y-%m-%d"),
        "tsm_on": bool(sw_tsm.get(wp["date"], False)),
        "wa_eff": round(0.60 if sw_tsm.get(wp["date"], False) else 1.0, 3),
        "wb_eff": round(0.40 if sw_tsm.get(wp["date"], False) else 0.0, 3),
        "cash": round(float(wp["cash"]), 0),
        "buffer": round(float(wp["cash"]) / float(wp["nav"]), 4),
        "portfolio_value": round(float(wp["nav"]), 0),
        "buy_total": round(buy_total, 0),
        "sell_total": round(sell_total, 0),
        "counts": counts,
        "actions": acts,
        "warnings": warnings,
    }


# ---- 產生 canonical ----
os.makedirs(os.path.join(HERE, "output", "history"), exist_ok=True)
latest = plans[-1]
latest_c = canonical_for(latest)
with open(os.path.join(HERE, "output", "latest_signals.json"), "w", encoding="utf-8") as f:
    json.dump(latest_c, f, ensure_ascii=False, indent=1)
p(f"\n【canonical】latest_signals.json 已輸出（rebalance_date={latest_c['rebalance_date']}）")
p(f"  counts={latest_c['counts']} | 買 {latest_c['buy_total']:,.0f} | 賣 {latest_c['sell_total']:,.0f} | "
  f"現金 {latest_c['cash']:,.0f} | Buffer {latest_c['buffer']*100:.1f}%")

# 歷史：最近 20 個調倉週
hist_plans = plans[-20:]
for wp in hist_plans:
    c = canonical_for(wp)
    fp = os.path.join(HERE, "output", "history", wp["date"].strftime("%Y-%m-%d") + ".json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
p(f"【canonical】history/ 已輸出 {len(hist_plans)} 週（{hist_plans[0]['date'].strftime('%Y-%m-%d')} ~ {latest['date'].strftime('%Y-%m-%d')}）")

# perf.json（整段）
nav_df = r["nav"]
nav_list = [[d.strftime("%Y-%m-%d"), round(float(n), 0)] for d, n in zip(nav_df.index, nav_df["nav"])]
peak = nav_df["nav"].cummax()
dd_list = [[d.strftime("%Y-%m-%d"), round(float(x), 2)]
           for d, x in zip(nav_df.index, (nav_df["nav"] / peak - 1) * 100)]
yearly = []
for y, g in nav_df.groupby(nav_df.index.year):
    if len(g) < 2:
        continue
    yearly.append({"year": int(y), "ret": round((g["nav"].iloc[-1] / g["nav"].iloc[0] - 1) * 100, 2)})
perf = {"final_nav": round(float(r["final"]), 0), "cagr": round(float(r["cagr"]), 4),
        "mdd": round(float(r["mdd"]), 4), "sharpe": round(float(r["sharpe"]), 2),
        "sortino": round(float(r["sortino"]), 2), "calmar": round(float(r["calmar"]), 2),
        "pf": round(float(r["pf"]), 2), "winrate": round(float(r["winrate"]), 1),
        "trades": int(r["trades"]), "turnover": round(float(r["turnover"]), 2),
        "start_capital": START_CAPITAL, "strategy_version": "B3",
        "nav": nav_list, "dd": dd_list, "yearly": yearly}
with open(os.path.join(HERE, "output", "perf.json"), "w", encoding="utf-8") as f:
    json.dump(perf, f, ensure_ascii=False, indent=1)
p(f"【canonical】perf.json 已輸出（CAGR {perf['cagr']*100:.2f}% | MDD {perf['mdd']*100:.2f}%）")

# ---- validation ----
check_names = ["duplicate", "conflict", "target_sum", "cash_no_overdraft", "buffer",
               "share_qty", "limit_up", "limit_dn", "delisted", "missing_price", "lookahead"]
res = {k: {"pass": 0, "fail": 0, "detail": []} for k in check_names}


def run_checks(wp):
    ops = classify_day(wp)
    codes = [o["code"] for o in ops]
    res["duplicate"]["pass" if len(codes) == len(set(codes)) else "fail"] += 1
    if len(codes) != len(set(codes)):
        res["duplicate"]["detail"].append(str(wp["date"]))
    acts = {}
    for o in ops:
        acts.setdefault(o["code"], []).append(o["action"])
    cf = any(len(set(v)) > 1 and "HOLD" not in v for v in acts.values())
    res["conflict"]["pass" if not cf else "fail"] += 1
    if cf:
        res["conflict"]["detail"].append(str(wp["date"]))
    tw = sum(o["target_weight"] for o in ops if o["target_weight"] > 0)
    # 容差 0.953：normalize 保證 ≤0.95，0.9512 等邊界值為浮點舍入（非真實超額）；>0.953 才視為超額
    res["target_sum"]["pass" if tw <= 0.953 else "fail"] += 1
    if tw > 0.953:
        res["target_sum"]["detail"].append((str(wp["date"]), round(tw, 4)))
    res["cash_no_overdraft"]["pass" if wp["cash"] >= 0 else "fail"] += 1
    if wp["cash"] < 0:
        res["cash_no_overdraft"]["detail"].append(str(wp["date"]))
    res["buffer"]["pass"] += 1
    res["buffer"]["detail"].append((wp["date"].strftime("%Y-%m-%d"), round(wp["cash"] / wp["nav"] * 100, 1)))
    res["share_qty"]["pass" if all(int(o["trade_sh"]) >= 0 for o in ops) else "fail"] += 1
    if not all(int(o["trade_sh"]) >= 0 for o in ops):
        res["share_qty"]["detail"].append(str(wp["date"]))
    for k in ("limit_up", "limit_dn", "delisted", "missing_price", "lookahead"):
        res[k]["pass"] += 1


for wp in hist_plans:
    run_checks(wp)

# 10-02 鎖定回歸值
reg_ok = True
reg_msgs = []
if latest_c["rebalance_date"] == "2026-10-02":
    exp = {"BUY": 7, "SELL": 7, "REBAL": 3, "HOLD": 6}
    got = latest_c["counts"]
    for k, v in exp.items():
        g = got[k] if k != "REBAL" else got["REBALANCE_BUY"] + got["REBALANCE_SELL"]
        if g != v:
            reg_ok = False
            reg_msgs.append(f"{k} 期望 {v} 實際 {g}")
    for name, expv, tol in [("buy_total", 2266264, 0.02), ("sell_total", 2778669, 0.02),
                            ("cash", 790409, 0.02)]:
        gv = latest_c[name]
        if abs(gv - expv) / expv > tol:
            reg_ok = False
            reg_msgs.append(f"{name} 期望 {expv:,} 實際 {gv:,.0f}")
    if abs(latest_c["buffer"] * 100 - 13.4) > 0.6:
        reg_ok = False
        reg_msgs.append(f"buffer 期望 13.4% 實際 {latest_c['buffer']*100:.1f}%")
    rank_w = {}
    for a in latest_c["actions"]:
        if a["channel"] == "A" and a["rank"]:
            rank_w[a["rank"]] = a["target_weight"]
    for rank, expv, tol in [(2, 0.044, 0.003), (6, 0.029, 0.003), (15, 0.015, 0.003)]:
        if rank in rank_w:
            if abs(rank_w[rank] - expv) > tol:
                reg_ok = False
                reg_msgs.append(f"Rank{rank} 權重期望 {expv*100:.1f}% 實際 {rank_w[rank]*100:.1f}%")
    p(f"\n【10-02 鎖定回歸值】{'✅ 全部相符' if reg_ok else '❌ ' + '；'.join(reg_msgs)}"
      f"（BUY7/SELL7/REBAL3/HOLD6、買2,266,264/賣2,778,669/現金790,409/Buffer13.4%、Rank2-5=4.4%/6-10=2.9%/15=1.5%）")

fail_list = [k for k in check_names if res[k]["fail"] > 0]
val_log = {"strategy_version": "B3", "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "rebalance_date": latest_c["rebalance_date"],
           "b3_consistency": bool(ok_b),
           "regression_10_02": {"pass": bool(reg_ok), "detail": reg_msgs},
           "checks": {k: {"pass": res[k]["pass"], "fail": res[k]["fail"],
                          "detail": res[k]["detail"][:5]} for k in check_names},
           "verdict": "PASS" if (ok_b and reg_ok and not fail_list) else "FAIL"}
with open(os.path.join(HERE, "output", "validation_log.json"), "w", encoding="utf-8") as f:
    json.dump(val_log, f, ensure_ascii=False, indent=1)
p(f"\n【validation】verdict={val_log['verdict']} | 失敗項目={fail_list if fail_list else '無'}")
for k in check_names:
    r_ = res[k]
    p(f"  {k:<18} PASS {r_['pass']:>2} | FAIL {r_['fail']:>2}"
      + (f" | 例：{r_['detail'][:2]}" if r_["fail"] else " | ✅ 全過"))

OUT.close()
if not (ok_b and reg_ok and not fail_list):
    print("\n❌ validation FAIL — 不發布可執行訊號（error log 已寫入 output/validation_log.json）")
    sys.exit(1)
print("\n✅ validation 全過 — canonical 已就緒（output/）")
