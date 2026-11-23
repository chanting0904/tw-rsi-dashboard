# -*- coding: utf-8 -*-
"""月營收資金係數 K 組合影子紀錄（研究用，不影響正式計畫）。

K 組合：近 3 月營收年增 >30% 資金係數 ×1.5、<0 ×0.5（參數 2026-11-23 固定，2027-02 複審）。
每週排程執行一次：
  1. 本週已發布計畫：記下每檔持股的營收年增、K 係數與套用 K 後的近似權重 → shadow_revenue.jsonl
  2. 用同一套引擎跑 V6 原版與 V6+K（週五調倉），比較實盤起點 2026-10-02 與參數固定日 2026-11-23 之後的報酬
     → shadow_revenue.md
營收只用 local_db/monthly_revenue.sqlite3 中 available 日之前的資料。
"""
import inspect
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
LOG = HERE / "shadow_revenue.jsonl"
REPORT = HERE / "shadow_revenue.md"
DB = ROOT / "local_db" / "monthly_revenue.sqlite3"
LIVE_START, FIXED = "2026-10-02", "2026-11-23"
HI, HI_X, LO, LO_X = 0.3, 1.5, 0.0, 0.5


def yoy3_daily(index, columns):
    con = sqlite3.connect(DB)
    d = pd.read_sql("SELECT ym, code, revenue, available FROM revenue", con)
    con.close()
    d["ym"] = pd.PeriodIndex(d.ym, freq="M")
    rev = d.pivot(index="ym", columns="code", values="revenue").astype(float)
    full = pd.period_range(rev.index.min(), rev.index.max(), freq="M")
    rev = rev.reindex(full)
    av = d.pivot(index="ym", columns="code", values="available").reindex(full)
    m = rev.rolling(3).sum() / rev.shift(12).rolling(3).sum() - 1
    m = m.where(rev.rolling(3).count() == 3).replace([np.inf, -np.inf], np.nan)
    s = m.stack().rename("v").reset_index()
    s.columns = ["ym", "code", "v"]
    a = av.stack().rename("a").reset_index()
    a.columns = ["ym", "code", "a"]
    s = s.merge(a, on=["ym", "code"])
    s["a"] = pd.to_datetime(s["a"])
    s = s.sort_values(["a", "ym"]).drop_duplicates(["a", "code"], keep="last")
    w = s.pivot(index="a", columns="code", values="v")
    out = w.reindex(w.index.union(index)).ffill().reindex(index).reindex(columns=columns)
    return out.shift(1)   # available 當天收盤後才能用


def k_of(y):
    if y is None or pd.isna(y):
        return 1.0
    return HI_X if y > HI else (LO_X if y < LO else 1.0)


def patch_engine():
    """在資金係數乘上 _AM(code, 週)；找不到錨點就直接失敗，避免引擎改版後默默算錯。"""
    import v5_engine.execution as E
    import v5_engine.simulation as SIM
    import v5_engine.security_status as SS
    SS.refresh_security_status = lambda *a, **k: json.load(open("output/security_status.json", encoding="utf-8"))
    src = inspect.getsource(E.execute_week)
    for a in ('ccoef[code2] = pos[code2]["wc"]', 'ccoef[code2] = coef_of(want_rank.get(code2, 8))',
              'ccoef2[code2] = pos[code2]["wc"]', 'ccoef2[code2] = coef_of(want_rank.get(code2, 8))'):
        if src.count(a) != 1:
            raise SystemExit("引擎資金係數程式已變更，影子紀錄需更新：" + a)
        src = src.replace(a, a + " * _AM(code2, wk)")
    E._AM = lambda c, w: 1.0
    exec(src, E.__dict__)
    SIM.execute_week = E.execute_week
    return E, SIM


def segment(nav, start):
    s = nav[nav.index >= pd.Timestamp(start)]
    return None if len(s) < 2 else float(s.iloc[-1] / s.iloc[0] - 1)


def main():
    E, SIM = patch_engine()
    import v5_engine.market as M
    from v5_engine.config import StrategyConfig
    ctx = M.load_market(StrategyConfig(), ".")
    y = yoy3_daily(ctx.close.index, ctx.close.columns)

    # 1. 本週已發布計畫
    sig = json.load(open("output/latest_signals.json", encoding="utf-8"))
    day = pd.Timestamp(sig["rebalance_date"])
    row = y.loc[y.index[y.index <= day][-1]] if (y.index <= day).any() else pd.Series(dtype=float)
    acts = [a for a in sig["actions"] if a.get("strategy_target_weight")]
    total = sum(a["strategy_target_weight"] for a in acts)
    scaled = {a["stock_code"]: a["strategy_target_weight"] * k_of(row.get(a["stock_code"])) for a in acts}
    norm = total / sum(scaled.values()) if scaled else 1.0
    snap = dict(rebalance_date=sig["rebalance_date"], strategy_version=sig["strategy_version"], holdings=[
        dict(code=a["stock_code"], rank=a.get("rank"), weight=round(a["strategy_target_weight"], 5),
             yoy3=None if pd.isna(row.get(a["stock_code"], np.nan)) else round(float(row[a["stock_code"]]), 4),
             k=k_of(row.get(a["stock_code"])), shadow_weight=round(scaled[a["stock_code"]] * norm, 5))
        for a in acts])
    old = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []
    old = [o for o in old if o["rebalance_date"] != snap["rebalance_date"]] + [snap]
    LOG.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in sorted(old, key=lambda o: o["rebalance_date"])),
                   encoding="utf-8")

    # 2. 原版 vs K（週五調倉，全歷史回測後取實盤區間）
    kdf = pd.DataFrame(np.select([y > HI, y < LO], [HI_X, LO_X], 1.0), index=y.index, columns=y.columns)
    cols = {c: i for i, c in enumerate(kdf.columns)}
    idx = {d: i for i, d in enumerate(kdf.index)}
    arr = kdf.values
    res = {}
    for name, am in (("V6 原版", lambda c, w: 1.0),
                     ("V6 + 營收 K", lambda c, w: float(arr[idx[w], cols[c]]) if c in cols and w in idx else 1.0)):
        E._AM = am
        r = SIM.run_reference(ctx, 5, "close", record=False)
        nav = r["nav"].set_index("date")["nav"] if "date" in r["nav"].columns else r["nav"]["nav"]
        res[name] = dict(live=segment(nav, LIVE_START), fixed=segment(nav, FIXED), cagr=r["cagr"], mdd=r["mdd"],
                         last=str(pd.Timestamp(nav.index[-1]).date()))
    E._AM = lambda c, w: 1.0

    pct = lambda v: "—" if v is None else f"{v * 100:+.2f}%"
    lines = ["# 月營收 K 組合影子紀錄", "",
             "研究用，不影響正式計畫。K：近 3 月營收年增 >30% 資金係數 ×1.5、<0 ×0.5（2026-11-23 固定，2027-02 複審）。",
             "每週六排程自動更新（research_notes/shadow_revenue.py）。", "",
             f"## 同引擎比較（週五調倉，資料至 {res['V6 原版']['last']}）", "",
             f"| 版本 | 實盤起點 {LIVE_START} 起 | 參數固定 {FIXED} 起 | 2015 起年化 | MDD |", "|---|---|---|---|---|"]
    for n, v in res.items():
        lines.append(f"| {n} | {pct(v['live'])} | {pct(v['fixed'])} | {v['cagr'] * 100:.2f}% | {v['mdd'] * 100:.2f}% |")
    lines += ["", f"## 本週計畫（調倉日 {snap['rebalance_date']}）套用 K 的近似權重", "",
              "| 代號 | 名次 | 正式權重 | 近 3 月營收年增 | K | 影子權重 |", "|---|---|---|---|---|---|"]
    for h in snap["holdings"]:
        yy = "—" if h["yoy3"] is None else f"{h['yoy3'] * 100:+.1f}%"
        lines.append(f"| {h['code']} | {h['rank']} | {h['weight'] * 100:.2f}% | {yy} | ×{h['k']} | {h['shadow_weight'] * 100:.2f}% |")
    lines += ["", f"歷週紀錄共 {len(old)} 週，見 shadow_revenue.jsonl。", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
