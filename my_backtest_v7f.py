# -*- coding: utf-8 -*-
"""
回測 v7f（擬真修正版）：60/40 雙通道（A=v7e 前60% 15檔 / B=權值龍頭 4檔，tsm_long 開關）
=== 2026-10-01 兩大擬真修正 ===
1) 還權價 -> 未還權收盤價（price:收盤價）：除息除權跳空真實反映在 RSI/MA/出場
2) ROE -> 公告 deadline 對齊（deadline 次日起才可使用，消除季頻前視偏差）
3) 修正 rsi() 的 l 計算 bug（(-d).clip(upper=0) -> (-d).clip(lower=0)）
擬真結果（2015-01 ~ 2026-09）：CAGR 37.67%、MDD -40.87%、最終 4,281,209
"""
import os
os.chdir(r"D:\RSI選股器")
import sys
import pandas as pd
import numpy as np
import finlab, json, warnings
warnings.filterwarnings("ignore")
from finlab import data

finlab.login(api_token=json.load(open(r"D:\RSI選股器\config.json", encoding="utf-8"))["FINLAB_TOKEN"])

# ===== [v7g-3] 歷史股本門檻（仟元）鎖定 6 億 + 動態A（正式預設）=====
# 正式（無 argv）= 6億 + 動態A（B空手→A吃95%）；敏感度：argv1=門檻、argv2=0/1 關閉/開啟動態A
CAP_THRESHOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 6e5
DYN_A = len(sys.argv) <= 2 or sys.argv[2] != "0"
IS_SENS = len(sys.argv) > 1
CAP_TAG = f"cap{int(CAP_THRESHOLD / 1e5)}e8"  # cap2e8 / cap6e8 ...
DYN_TAG = "_dyn" if DYN_A else ""
NAV_OUT = rf"D:\RSI選股器\my_nav_v7f{CAP_TAG if IS_SENS else ''}{DYN_TAG if IS_SENS else ''}.csv"
TRD_OUT = rf"D:\RSI選股器\my_trades_v7f{CAP_TAG if IS_SENS else ''}{DYN_TAG if IS_SENS else ''}.csv"
HLD_OUT = rf"D:\RSI選股器\my_holdings_weekly{CAP_TAG if IS_SENS else ''}{DYN_TAG if IS_SENS else ''}.csv"
SUM_OUT = rf"D:\RSI選股器\v7f_summary{CAP_TAG if IS_SENS else ''}{DYN_TAG if IS_SENS else ''}.json"

START_CAPITAL = 100_000
START_DATE = "2015-01-01"
SELL_FEE = 0.003
LIMIT = 0.099
THRESH = 0.25
BUFFER = 0.05
MAX_HOLD_A = 15
MAX_HOLD_B = 4
W_A, W_B = 0.60, 0.40

# ===== [修正1] 主價格改用未還權收盤價 =====
close = data.get("price:收盤價").apply(pd.to_numeric, errors="coerce")
roe_raw = data.get("fundamental_features:ROE稅後")
roe_f = roe_raw.apply(pd.to_numeric, errors="coerce")
tv = data.get("price:成交金額").apply(pd.to_numeric, errors="coerce")

def common(c):
    s = str(c)
    return len(s) == 4 and s.isdigit() and not s.startswith("0")
keep = [c for c in close.columns if common(c)]

# ===== [v7g] 排除創新板（-創）＝實盤可操作性（創新板 2021 後才有，回測早期無資料，無污染） =====
excl_innov = set()
try:
    _info = data.get("company_basic_info")
    if _info is not None and "公司簡稱" in _info.columns:
        _key = "stock_id" if "stock_id" in _info.columns else _info.index.name
        excl_innov = set(_info[_info["公司簡稱"].astype(str).str.contains("-創", na=False, regex=False)][_key].astype(str))
except Exception:
    pass
keep = [c for c in keep if c not in excl_innov]
print(f"[v7g] 排除創新板 {len(excl_innov)} 檔，選股池 {len(keep)} 檔")

close = close[keep]
roe_f = roe_f[[c for c in keep if c in roe_f.columns]]
tv = tv[[c for c in keep if c in tv.columns]]

# ===== [修正2] ROE 公告 deadline 對齊（無前視） =====
# FinLab deadline() = 公告日軸 DataFrame（各股在「自己公告日」才出現 ROE 值）
roe_dl = roe_raw.deadline()
roe_dl = roe_dl[[c for c in keep if c in roe_dl.columns]]
roe_daily = roe_dl.reindex(close.index).ffill().shift(1)
# 公告日(含)之前 → NaN → 視為未知（放行）；公告日次日起 → 使用該季 ROE
roe_ok = (roe_daily > 0).fillna(True)

# ===== [v7g-2] 歷史股本（季頻財報 deadline 對齊）＝消除未來資料污染 =====
# financial_statement:股本 單位＝仟元；門檻 6 億 = 6e5（仟元）
# 用法：該股「當時最新公告」的股本 < 5 億 → 當週禁止進場/續持
cap_q = data.get("financial_statement:股本").apply(pd.to_numeric, errors="coerce")
cap_q = cap_q[[c for c in keep if c in cap_q.columns]]
cap_dl = cap_q.deadline()
cap_daily = cap_dl.reindex(close.index).ffill().shift(1)
cap_ok = (cap_daily >= CAP_THRESHOLD).fillna(True)  # NaN（早期無公告）→ 放行
print(f"[v7g-2] 歷史股本序列就緒：{cap_daily.shape[1]} 檔 x {len(cap_daily)} 交易日 | 門檻 {CAP_THRESHOLD/1e5:.0f} 億")

# ===== [修正3] rsi() 的 l 計算 bug =====
def rsi(c, n):
    d = c.diff(); g = d.clip(lower=0); l = (-d).clip(lower=0)
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
buyA_gh = buyA.shift(1)
sellA = (close.shift(1) < ma60.shift(1)) | buyA_gh.shift(80).fillna(False)
targetA = buyA_gh.hold_until(sellA)

rank_tv = tv20.rank(axis=1, ascending=False)
big = rank_tv.shift(1) <= 15
bull = close.shift(1) > ma60.shift(1)
lt_up = (r120 > 60).shift(1)
not_hot = r20s < 88
buyB = big & bull & lt_up & not_hot & roe_ok
buyB_gh = buyB.shift(1)
sellB = (close.shift(1) < ma60.shift(1)) | buyB_gh.shift(80).fillna(False)
targetB = buyB_gh.hold_until(sellB)

# tsm_long 開關
tsm = close["2330"] if "2330" in close.columns else None
if tsm is not None:
    sw_tsm = ((tsm.rolling(20).mean().shift(1) >= tsm.rolling(60).mean().shift(1))
              & (tsm.shift(1) >= tsm.rolling(60).mean().shift(1)))
else:
    sw_tsm = pd.Series(False, index=close.index)

pct = close.pct_change(fill_method=None)
limit_up = pct >= LIMIT
limit_dn = pct <= -LIMIT

weeks = close.resample("W-FRI").last().index
weeks = weeks[weeks >= pd.Timestamp(START_DATE)]
tdays = close.index
weeks = pd.DatetimeIndex([tdays[tdays <= w][-1] for w in weeks if (tdays <= w).any()])

cash = START_CAPITAL
pos = {}
nav_hist, trades, snap_hist = [], [], []
for wk in weeks:
    if wk not in targetA.index:
        continue
    px = close.loc[wk]
    on = bool(sw_tsm.get(wk, False))
    a_w, b_w = (W_A, W_B) if on else (1.0, 0.0)
    # [v7g-2] 當週歷史股本 ≥ 5 億才允許進場（消除快照污染）；無財報資料→放行
    capw = cap_ok.loc[wk]
    def cap_pass(c):
        return c not in cap_ok.columns or bool(capw[c])
    want = {}
    tA = targetA.loc[wk]
    codesA = [c for c in tA[tA].index.tolist() if cap_pass(c)]
    scoredA = sorted(codesA, key=lambda c: float(r20s[c].loc[wk]) if pd.notna(r20s[c].loc[wk]) else -1, reverse=True)
    want["A"] = scoredA[:MAX_HOLD_A]
    if b_w > 0:
        tB = targetB.loc[wk]
        codesB = [c for c in tB[tB].index.tolist() if cap_pass(c)]
        tvw = tv20.loc[wk]
        scoredB = sorted(codesB, key=lambda c: float(tvw[c]) if pd.notna(tvw[c]) else -1, reverse=True)
        want["B"] = scoredB[:MAX_HOLD_B]
    else:
        want["B"] = []
    # ===== [v7h 修正] 通道唯一歸屬（A/B 重疊時）=====
    # 規則：已持有→保留原通道；新進→A 優先；跨通道→淨額調倉（不全賣全買）
    _rawA, _rawB = list(want["A"]), list(want["B"])
    _fa, _fb = [], []
    for c in _rawA:
        if c in pos and pos[c]["pool"] == "B" and c in _rawB:
            _fb.append(c)          # 原 B 持有，本週仍在 B → 留 B
        else:
            _fa.append(c)
    for c in _rawB:
        if c not in _fa and c not in _fb:
            _fb.append(c)
    want["A"], want["B"] = _fa, _fb
    want_set = set(_fa) | set(_fb)
    # [動態A] B 空手 → 38% 併入 A（A 吃 95%）；B 有候選 → 恢復 A60/B40
    if DYN_A and on and not want["B"]:
        a_w, b_w = 1.0, 0.0

    # 賣出：僅「完全掉出兩通道」者（跨通道者保留，交調倉迴圈淨額處理）
    for c in list(pos.keys()):
        if c not in want_set:
            p = float(px[c])
            if np.isnan(p) or p <= 0:
                continue
            if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                continue
            sh = pos[c]["shares"]
            cash += sh * p * (1 - SELL_FEE)
            trades.append({"date": wk, "code": c, "pool": pos[c]["pool"], "side": "SELL",
                           "price": p, "shares": sh, "amount": sh * p})
            del pos[c]

    total = cash + sum(pos[c]["shares"] * float(px[c])
                       for c in pos if pd.notna(px[c]) and float(px[c]) > 0)
    for pool, wgt in [("A", a_w), ("B", b_w)]:
        lst = want.get(pool, [])
        if not lst or wgt <= 0:
            continue
        tv_ = total * wgt * (1 - BUFFER) / len(lst)
        for c in lst:
            p = float(px[c])
            if np.isnan(p) or p <= 0:
                continue
            cur = pos.get(c)
            cur_sh = cur["shares"] if cur else 0
            if cur:
                cur["pool"] = pool   # 通道標記同步（跨通道轉換即使不交易也更新）
            diff = tv_ - cur_sh * p
            if cur and abs(diff) <= tv_ * THRESH:
                continue
            if diff > p:
                if c in limit_up.columns and bool(limit_up[c].get(wk, False)):
                    continue
                sh = min(int(diff // p), int(cash // p))
                if sh > 0:
                    cash -= sh * p
                    if cur:
                        cur["shares"] = cur_sh + sh
                        cur["avg"] = (cur_sh * cur["avg"] + sh * p) / (cur_sh + sh)
                    else:
                        pos[c] = {"shares": sh, "avg": p, "pool": pool}
                    trades.append({"date": wk, "code": c, "pool": pool, "side": "BUY",
                                   "price": p, "shares": sh, "amount": sh * p})
            elif diff < -p:
                if c in limit_dn.columns and bool(limit_dn[c].get(wk, False)):
                    continue
                sh = min(int(-diff // p), cur_sh)
                if sh > 0:
                    cash += sh * p * (1 - SELL_FEE)
                    cur["shares"] = cur_sh - sh
                    if cur["shares"] == 0:
                        del pos[c]
                    trades.append({"date": wk, "code": c, "pool": pool, "side": "SELL",
                                   "price": p, "shares": sh, "amount": sh * p})

    mv = sum(pos[c]["shares"] * float(px[c])
             for c in pos if pd.notna(px[c]) and float(px[c]) > 0)
    nav_hist.append({"date": wk, "nav": cash + mv, "nA": len([c for c in pos if pos[c]["pool"] == "A"]),
                     "nB": len([c for c in pos if pos[c]["pool"] == "B"])})

    for c, info in pos.items():
        snap_hist.append({"date": wk, "code": c, "pool": info["pool"],
                          "shares": int(info["shares"]), "avg": round(float(info["avg"]), 3)})

nav_df = pd.DataFrame(nav_hist).set_index("date")
tr_df = pd.DataFrame(trades)
final = float(nav_df["nav"].iloc[-1])
years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
cagr = (final / START_CAPITAL) ** (1 / years) - 1
peak = nav_df["nav"].cummax()
mdd = float(((nav_df["nav"] - peak) / peak).min())
print(f"最終資產: {final:,.0f}")
print(f"CAGR: {cagr*100:.2f}%")
print(f"MDD: {mdd*100:.2f}%  ({peak.idxmax().strftime('%Y-%m-%d')} 高峰)")
print(f"總交易: {len(tr_df)}（買 {(tr_df['side']=='BUY').sum()} / 賣 {(tr_df['side']=='SELL').sum()}）")

nav_df.to_csv(NAV_OUT, encoding="utf-8-sig")
tr_df[["date", "code", "pool", "side", "price", "shares", "amount"]].to_csv(
    TRD_OUT, encoding="utf-8-sig", index=False)
pd.DataFrame(snap_hist).to_csv(HLD_OUT, encoding="utf-8-sig", index=False)
json.dump({"final_nav": final, "cagr": cagr, "mdd": mdd,
           "sig_date": nav_df.index[-1].strftime("%Y-%m-%d"),
           "mode": f"v7g-2 歷史股本 ≥{CAP_THRESHOLD/1e5:.0f}億 (無污染)"},
          open(SUM_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"saved {NAV_OUT} / {TRD_OUT} / {HLD_OUT} / {SUM_OUT}")
