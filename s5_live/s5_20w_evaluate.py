# -*- coding: utf-8 -*-
"""S5 20 週驗證評估
20 週完成後跑：計算 Q1-Q10、與回測 S1/S5 對照、給 PASS/CONDITIONAL/FAIL 判定。
判定閾值於 2026-10-04 事前固定，不看結果再改。

產出：
  s5_live/S5_20w_validation_report.md
  s5_live/S5_20w_orders.csv
  s5_live/S5_20w_portfolio.csv
  s5_live/S5_20w_summary.json
"""
import os, json, io
import pandas as pd
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_CSV = os.path.join(BASE, "s5_live", "S5_live_20w.csv")
PORT_CSV = os.path.join(BASE, "s5_live", "S5_live_20w_portfolio.csv")
OUT = os.path.join(BASE, "s5_live")

# 回測對照（Execution Layer Study）
BT = {"S1_ah_fill": 76.0, "S1_buy_fill": 80.9, "S1_sell_fill": 71.6,
      "S5_final_fill": 80.5, "S1_drift": 8.0, "S5_drift": 0.0,
      "S1_cagr_drag": 7.41, "S5_cagr_drag": 3.06}

# 事前固定判定閾值
TH = {"final_fill_pass": 85.0, "final_fill_improve": 5.0, "drift_pass": 6.0,
      "slip_mean_pass": 1.5, "slip_extreme": 5.0, "slip_extreme_cnt_pass": 2,
      "fill_improve_fail_min": 3.0, "slip_mean_fail": 2.0, "slip_extreme_cnt_fail": 5}

def main():
    live = pd.read_csv(LIVE_CSV, encoding="utf-8-sig")
    port = pd.read_csv(PORT_CSV, encoding="utf-8-sig")
    a = live[live["pool"] == "A"].copy()
    a["ah_fill"] = a["after_hours_fill_rate"].astype(float)
    a["fin_fill"] = a["final_fill_rate"].astype(float)
    a["slip"] = a["slippage"].astype(float).abs()

    # ---- Q1-Q10 ----
    q1 = a["ah_fill"].mean()
    q2 = a["fin_fill"].mean() - q1
    q3 = a[a["side"].isin(["BUY", "REBALANCE_BUY"])]["fin_fill"].mean() - a[a["side"].isin(["BUY", "REBALANCE_BUY"])]["ah_fill"].mean()
    q4 = a[a["side"].isin(["SELL", "REBALANCE_SELL"])]["fin_fill"].mean() - a[a["side"].isin(["SELL", "REBALANCE_SELL"])]["ah_fill"].mean()
    drift = port["A_exposure_drift"].astype(float).dropna()
    q5 = drift.mean() * 100
    # cash drag proxy：未成交 BUY 的現金保留比例
    buy = a[a["side"].isin(["BUY", "REBALANCE_BUY"])]
    cash_drag = (1 - buy["fin_fill"] / 100).mean() * 100 if len(buy) else np.nan
    q6 = cash_drag
    nd = a[a["next_day_filled_qty"].astype(float) > 0]
    q7 = nd["slip"].mean() if len(nd) else np.nan
    q8_extreme = a[a["slip"] > TH["slip_extreme"]]
    q8_cnt = len(q8_extreme)

    def drift_bt():
        return BT["S1_drift"]  # S1 回測 drift 8.0%

    q9 = "改善" if q2 >= TH["final_fill_improve"] and q5 <= drift_bt() else "未明顯改善"
    q10 = "一致" if q2 > 0 and (np.isnan(q5) or q5 <= drift_bt()) and (np.isnan(q7) or q7 <= 2.0) else "不一致"

    # ---- 判定 ----
    checks = {
        "final_fill_rate_ge_85": (a["fin_fill"].mean() >= TH["final_fill_pass"]),
        "improve_ge_5pp": (q2 >= TH["final_fill_improve"]),
        "drift_le_6": (q5 <= TH["drift_pass"]),
        "slip_mean_le_1.5": (np.isnan(q7) or q7 <= TH["slip_mean_pass"]),
        "slip_extreme_cnt_le_2": (q8_cnt <= TH["slip_extreme_cnt_pass"]),
    }
    checks = {key: bool(value) for key, value in checks.items()}
    passed = sum(checks.values())
    if len(port) < 20 or np.isnan(q5) or not len(a):
        verdict = "CONDITIONAL"
    elif (not np.isnan(q7) and q7 > TH["slip_mean_fail"]) or q8_cnt >= TH["slip_extreme_cnt_fail"] or q2 < TH["fill_improve_fail_min"]:
        verdict = "FAIL"
    elif passed == 5:
        verdict = "PASS"
    elif passed >= 3:
        verdict = "CONDITIONAL"
    else:
        verdict = "FAIL"

    summary = {
        "weeks": int(port.shape[0]), "a_orders": int(a.shape[0]),
        "answers": {
            "Q1_ah_fill_rate": round(q1, 2),
            "Q2_final_fill_improve": round(q2, 2),
            "Q3_buy_fill_improve": round(q3, 2),
            "Q4_sell_fill_improve": round(q4, 2),
            "Q5_exposure_drift": round(q5, 2),
            "Q6_cash_drag": round(q6, 2) if q6 == q6 else None,
            "Q7_nextday_avg_slippage": round(q7, 3) if q7 == q7 else None,
            "Q8_extreme_slippage_cnt_gt5pct": q8_cnt,
            "Q9_feasibility_improved": q9,
            "Q10_direction_matches": q10,
        },
        "backtest_reference": BT,
        "checklist": checks, "passed_count": passed,
        "verdict": verdict,
        "thresholds": TH,
        "note": "判定閾值於 2026-10-04 事前固定；20 週樣本僅驗證執行機制，不驗證 CAGR。",
    }
    io.open(os.path.join(OUT, "S5_20w_summary.json"), "w", encoding="utf-8").write(
        json.dumps(summary, ensure_ascii=False, indent=1))
    live.to_csv(os.path.join(OUT, "S5_20w_orders.csv"), index=False, encoding="utf-8-sig")
    port.to_csv(os.path.join(OUT, "S5_20w_portfolio.csv"), index=False, encoding="utf-8-sig")

    md = []
    md.append("# S5 20 週實盤驗證報告")
    md.append("")
    md.append("判定：**%s**（檢查 %d/5 通過）" % (verdict, passed))
    md.append("")
    md.append("| # | 問題 | 結果 |")
    md.append("|---|---|---|")
    md.append("| Q1 | A 盤後零股平均成交率 | %.2f%% |" % q1)
    md.append("| Q2 | 隔日補單後 Final Fill 提升 | +%.2fpp |" % q2)
    md.append("| Q3 | BUY fill 提升 | +%.2fpp |" % q3)
    md.append("| Q4 | SELL fill 提升 | +%.2fpp |" % q4)
    md.append("| Q5 | Exposure drift（回測 S1=8.0% / S5≈0%） | %.2f%% |" % q5)
    md.append("| Q6 | Cash drag（未成交 BUY 保留現金比） | %.2f%% |" % (q6 if q6 == q6 else 0))
    md.append("| Q7 | 隔日補單平均滑價 | %s%% |" % (round(q7, 3) if q7 == q7 else "無補單樣本"))
    md.append("| Q8 | >5%% 極端滑價筆數 | %d |" % q8_cnt)
    md.append("| Q9 | S5 是否改善 execution feasibility | %s |" % q9)
    md.append("| Q10 | 真實結果是否與回測方向一致 | %s |" % q10)
    md.append("")
    md.append("## 判定檢查表")
    md.append("")
    for k, v in checks.items():
        md.append("- [%s] %s" % ("x" if v else " ", k))
    md.append("")
    md.append("## 與回測對照")
    md.append("")
    md.append("| 指標 | 回測 | 實盤 20 週 |")
    md.append("|---|---|---|")
    md.append("| A 盤後 fill（S1） | %.1f%% | %.2f%% |" % (BT["S1_ah_fill"], q1))
    md.append("| A final fill（S5） | %.1f%% | %.2f%% |" % (BT["S5_final_fill"], a["fin_fill"].mean()))
    md.append("| Exposure drift | S1 %.1f%% / S5 %.1f%% | %.2f%% |" % (BT["S1_drift"], BT["S5_drift"], q5))
    md.append("| CAGR drag | S1 %.2fpp / S5 %.2fpp | 樣本太短不評 |" % (BT["S1_cagr_drag"], BT["S5_cagr_drag"]))
    md.append("")
    md.append("## 異常事件")
    md.append("")
    notes = live[live["note"].astype(str).str.strip() != ""]
    if len(notes):
        for _, r in notes.iterrows():
            md.append("- %s %s %s：%s" % (r["execution_date"], r["symbol"], r["side"], r["note"]))
    else:
        md.append("（無）")
    md.append("")
    md.append("> 判定閾值事前固定（2026-10-04）；20 週僅驗證執行機制，不重新驗證策略 CAGR。")
    io.open(os.path.join(OUT, "S5_20w_validation_report.md"), "w", encoding="utf-8").write("\n".join(md))
    print(json.dumps(summary, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
