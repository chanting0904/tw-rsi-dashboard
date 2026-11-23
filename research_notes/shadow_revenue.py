# -*- coding: utf-8 -*-
"""月營收 K 組合／衰減＋營收 影子紀錄（研究用，不影響正式計畫）。

K 組合：近 3 月營收年增 >30% 資金係數 ×1.5、<0 ×0.5；衰減：targetA 連續成立 ≥45 交易日 ×0.1
（參數 2026-11-23 固定，2027-02 複審）。
每週排程執行一次：
  1. 本週已發布計畫：逐檔記下營收年增、訊號天數、K 與衰減係數，及套用後的近似權重
  2. 同一套引擎跑 V6 原版、V6+K、V6+衰減+K（週五調倉）：實盤起點 2026-10-02 與參數固定日 2026-11-23
     之後的報酬，以及各版本最新的模型持股
  結果寫入 shadow_revenue.md（最新）與 shadow_revenue.jsonl（歷週）。
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
AGE_S, AGE_M = 45, 0.1


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


def signal_age(target):
    """targetA 連續成立的交易日數（與研究定義相同）。"""
    t = target.astype(bool)
    c = t.cumsum()
    return c - c.where(~t).ffill().fillna(0)


def holdings_of(result):
    st = result["closing_state"]
    vals = {}
    for code, p in st["positions"].items():
        sh = p.get("shares", p.get("sh", p.get("qty", 0))) if isinstance(p, dict) else p
        vals[code] = float(sh) * st["valuation_prices"].get(code, 0.0)
    nav = float(st["nav"])
    return {c: v / nav for c, v in sorted(vals.items(), key=lambda kv: -kv[1]) if v > 0}


def main():
    E, SIM = patch_engine()
    import v5_engine.market as M
    from v5_engine.config import StrategyConfig
    ctx = M.load_market(StrategyConfig(), ".")
    y = yoy3_daily(ctx.close.index, ctx.close.columns)
    age = signal_age(ctx.targetA)
    kdf = pd.DataFrame(np.select([y > HI, y < LO], [HI_X, LO_X], 1.0), index=y.index, columns=y.columns)
    ddf = (age.reindex(index=kdf.index, columns=kdf.columns).fillna(0) >= AGE_S).astype(float) * (AGE_M - 1) + 1
    variants = {"V6 原版": None, "V6 + 營收 K": kdf, "V6 + 衰減 + 營收 K": kdf * ddf}

    # 1. 本週已發布計畫，逐檔套用係數的近似權重
    sig = json.load(open("output/latest_signals.json", encoding="utf-8"))
    day = pd.Timestamp(sig["rebalance_date"])
    on = y.index[y.index <= day][-1]
    acts = [a for a in sig["actions"] if a.get("strategy_target_weight")]
    total = sum(a["strategy_target_weight"] for a in acts)
    rows = []
    for a in acts:
        c = a["stock_code"]
        yy = y.at[on, c] if c in y.columns else np.nan
        g = int(age.at[on, c]) if c in age.columns else 0
        k = k_of(yy)
        d = AGE_M if g >= AGE_S else 1.0
        rows.append(dict(code=c, rank=a.get("rank"), weight=a["strategy_target_weight"],
                         yoy3=None if pd.isna(yy) else round(float(yy), 4), age=g, k=k, decay=d))
    for key, f in (("shadow_weight", lambda r: r["k"]), ("shadow_weight_dk", lambda r: r["k"] * r["decay"])):
        s = sum(r["weight"] * f(r) for r in rows)
        for r in rows:
            r[key] = round(r["weight"] * f(r) * total / s, 5) if s else r["weight"]
    for r in rows:
        r["weight"] = round(r["weight"], 5)

    # 2. 同引擎回測三個版本（週五調倉），取實盤區間報酬與最新模型持股
    res = {}
    for name, df in variants.items():
        if df is None:
            E._AM = lambda c, w: 1.0
        else:
            cols = {c: i for i, c in enumerate(df.columns)}
            idx = {d: i for i, d in enumerate(df.index)}
            arr = df.values
            E._AM = lambda c, w, arr=arr, cols=cols, idx=idx: float(arr[idx[w], cols[c]]) if c in cols and w in idx else 1.0
        r = SIM.run_reference(ctx, 5, "close", record=False)
        nav = r["nav"].set_index("date")["nav"] if "date" in r["nav"].columns else r["nav"]["nav"]
        res[name] = dict(live=segment(nav, LIVE_START), fixed=segment(nav, FIXED), cagr=r["cagr"], mdd=r["mdd"],
                         last=str(pd.Timestamp(nav.index[-1]).date()), holdings=holdings_of(r))
    E._AM = lambda c, w: 1.0

    snap = dict(rebalance_date=sig["rebalance_date"], strategy_version=sig["strategy_version"],
                data_through=res["V6 原版"]["last"], holdings=rows,
                model={n: dict(live=v["live"], fixed=v["fixed"],
                               holdings={c: round(w, 5) for c, w in v["holdings"].items()}) for n, v in res.items()})
    old = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []
    old = [o for o in old if o["rebalance_date"] != snap["rebalance_date"]] + [snap]
    LOG.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in sorted(old, key=lambda o: o["rebalance_date"])),
                   encoding="utf-8")

    pct = lambda v: "—" if v is None else f"{v * 100:+.2f}%"
    lines = ["# 月營收／衰減＋營收 影子紀錄", "",
             "研究用，不影響正式計畫。參數 2026-11-23 固定，2027-02 複審：",
             f"- 營收 K：近 3 月營收年增 >30% 資金係數 ×{HI_X}、<0 ×{LO_X}",
             f"- 衰減：targetA 連續成立 ≥{AGE_S} 交易日係數 ×{AGE_M}",
             "每週六排程自動更新（research_notes/shadow_revenue.py）。", "",
             f"## 同引擎比較（週五調倉，資料至 {res['V6 原版']['last']}）", "",
             f"| 版本 | 實盤起點 {LIVE_START} 起 | 參數固定 {FIXED} 起 | 2015 起年化 | MDD |", "|---|---|---|---|---|"]
    for n, v in res.items():
        lines.append(f"| {n} | {pct(v['live'])} | {pct(v['fixed'])} | {v['cagr'] * 100:.2f}% | {v['mdd'] * 100:.2f}% |")
    lines += ["", f"## 本週已發布計畫（調倉日 {sig['rebalance_date']}）逐檔套用係數的近似權重", "",
              "| 代號 | 名次 | 正式權重 | 近 3 月營收年增 | 訊號天數 | K | 衰減 | 只加營收 | 衰減＋營收 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for h in rows:
        yy = "—" if h["yoy3"] is None else f"{h['yoy3'] * 100:+.1f}%"
        lines.append(f"| {h['code']} | {h['rank']} | {h['weight'] * 100:.2f}% | {yy} | {h['age']} | ×{h['k']} | ×{h['decay']} | "
                     f"{h['shadow_weight'] * 100:.2f}% | {h['shadow_weight_dk'] * 100:.2f}% |")
    lines += ["", "## 模型持股（各版本自 2015 起連續回測到最新資料日的持股權重）", ""]
    codes = list(dict.fromkeys(c for v in res.values() for c in v["holdings"]))
    lines += ["| 代號 | " + " | ".join(res) + " |", "|---" * (len(res) + 1) + "|"]
    for c in codes:
        lines.append(f"| {c} | " + " | ".join(f"{v['holdings'].get(c, 0) * 100:.1f}%" if c in v["holdings"] else "—"
                                             for v in res.values()) + " |")
    lines += ["", f"歷週紀錄共 {len(old)} 週，見 shadow_revenue.jsonl。", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
