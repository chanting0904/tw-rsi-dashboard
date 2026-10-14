# -*- coding: utf-8 -*-
"""S5 每週回填後重算（成交回報後跑）
讀 s5_live/orders/ 下各週 CSV，依回填的成交欄重算 fill rate / slippage / drift，
並更新 20 週累計檔 S5_live_20w.csv 與 portfolio 檔。

用法（每週回報成交後）：
    python s5_live/recalc_weekly.py                # 重算所有週
"""
import os, sys, glob, json, io
import pandas as pd
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDERS_DIR = os.path.join(BASE, "s5_live", "orders")
LIVE_CSV = os.path.join(BASE, "s5_live", "S5_live_20w.csv")
PORT_CSV = os.path.join(BASE, "s5_live", "S5_live_20w_portfolio.csv")

def pct(x):
    return round(float(x) * 100, 2) if x == x and x is not None and x != "" else ""

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan

def main():
    files = sorted(glob.glob(os.path.join(ORDERS_DIR, "*.csv")))
    if not files:
        print("沒有週清單")
        return
    all_rows = []
    for f in files:
        df = pd.read_csv(f, encoding="utf-8-sig")
        df = df.fillna("")
        for i, r in df.iterrows():
            ref = num(r.get("reference_price"))
            ah_order = num(r.get("after_hours_order_qty"))
            ah_filled = num(r.get("after_hours_filled_qty"))
            nd_filled = num(r.get("next_day_filled_qty"))
            nd_order = num(r.get("next_day_order_qty"))
            tgt = num(r.get("target_qty"))
            if np.isnan(ah_order) or np.isnan(ah_filled):
                ah_filled = 0
            if np.isnan(ah_order):
                ah_order = 0
            if np.isnan(nd_filled):
                nd_filled = 0
            unfilled = max(ah_order - ah_filled, 0)
            if np.isnan(nd_order):
                nd_order = unfilled
            final = ah_filled + nd_filled
            ah_rate = ah_filled / ah_order if ah_order > 0 else 1.0
            nd_rate = nd_filled / nd_order if nd_order > 0 else (1.0 if unfilled == 0 else 0.0)
            final_rate = final / tgt if tgt > 0 else 0.0
            # 加權成交價
            ah_p = num(r.get("after_hours_price"))
            nd_p = num(r.get("next_day_open_price"))
            complete_prices = (ah_filled == 0 or (not np.isnan(ah_p) and ah_p > 0)) and (nd_filled == 0 or (not np.isnan(nd_p) and nd_p > 0))
            if final > 0 and complete_prices:
                w_sum = 0.0
                if not np.isnan(ah_p):
                    w_sum += ah_filled * ah_p
                if not np.isnan(nd_p):
                    w_sum += nd_filled * nd_p
                avg_p = w_sum / final
            else:
                avg_p = np.nan
            slip = ((avg_p - ref) / ref) if (not np.isnan(avg_p) and not np.isnan(ref) and ref != 0) else np.nan
            df.at[i, "after_hours_fill_rate"] = pct(ah_rate)
            df.at[i, "unfilled_qty"] = int(unfilled)
            df.at[i, "next_day_order_qty"] = int(nd_order)
            df.at[i, "next_day_fill_rate"] = pct(nd_rate)
            df.at[i, "final_filled_qty"] = int(final)
            df.at[i, "final_fill_rate"] = pct(final_rate)
            df.at[i, "final_avg_price"] = round(avg_p, 2) if not np.isnan(avg_p) else ""
            df.at[i, "slippage"] = round(slip * 100, 3) if not np.isnan(slip) else ""
        # 未填成交價但已填成交數 → 提示
        for i, r in df.iterrows():
            if num(r.get("after_hours_filled_qty")) > 0 and r.get("after_hours_price") in ("", None, np.nan):
                print("⚠ %s %s 有成交股數但缺盤後成交價" % (os.path.basename(f), r.get("symbol")))
            if num(r.get("next_day_filled_qty")) > 0 and r.get("next_day_open_price") in ("", None, np.nan):
                print("⚠ %s %s 有補單股數但缺補單成交價" % (os.path.basename(f), r.get("symbol")))
        df.to_csv(f, index=False, encoding="utf-8-sig")
        all_rows.append(df)
    live = pd.concat(all_rows, ignore_index=True)
    live.to_csv(LIVE_CSV, index=False, encoding="utf-8-sig")

    # portfolio 層：重算每週 A 層指標
    port = pd.read_csv(PORT_CSV, encoding="utf-8-sig") if os.path.exists(PORT_CSV) else pd.DataFrame()
    for i, r in port.iterrows():
        wk = r["week"]
        sub = live[(live["week"] == wk) & (live["execution_date"].astype(str) == str(r["execution_date"]))]
        a_sub = sub[sub["pool"] == "A"]
        target_orders = int(a_sub.shape[0])
        filled_orders = int((a_sub["final_filled_qty"].astype(float) > 0).sum()) if target_orders else 0
        unfilled_orders = int((a_sub["final_filled_qty"].astype(float) < a_sub["target_qty"].astype(float)).sum()) if target_orders else 0
        ah_rate = a_sub["after_hours_fill_rate"].astype(float).mean() / 100 if target_orders else np.nan
        fin_rate = a_sub["final_fill_rate"].astype(float).mean() / 100 if target_orders else np.nan
        # Orders alone do not contain carried holdings. Require a closing market
        # value rather than treating BUY and SELL order amounts as stock exposure.
        actual_val = num(r.get("A_closing_value"))
        nav = num(r.get("whole_nav"))
        actual_exp = actual_val / nav if not np.isnan(nav) and nav > 0 else np.nan
        target_exp = num(r.get("A_target_exposure"))
        drift = abs(actual_exp - target_exp) / target_exp if not np.isnan(actual_exp) and not np.isnan(target_exp) and target_exp != 0 else np.nan
        slip = pd.to_numeric(a_sub["slippage"], errors="coerce").abs().mean() if target_orders else np.nan
        # A_cash_drag：未成交 BUY（含 REBALANCE_BUY）保留現金佔目標買入金額比例
        buy_sub = a_sub[a_sub["side"].isin(["BUY", "REBALANCE_BUY"])]
        if len(buy_sub):
            unfilled_val = ((buy_sub["target_qty"].astype(float) - buy_sub["final_filled_qty"].astype(float))
                            * buy_sub["reference_price"].astype(float)).sum()
            tgt_val = (buy_sub["target_qty"].astype(float) * buy_sub["reference_price"].astype(float)).sum()
            cash_drag = unfilled_val / tgt_val if tgt_val > 0 else np.nan
        else:
            cash_drag = np.nan
        port.at[i, "A_target_orders"] = target_orders
        port.at[i, "A_filled_orders"] = filled_orders
        port.at[i, "A_unfilled_orders"] = unfilled_orders
        port.at[i, "A_after_hours_fill_rate"] = pct(ah_rate)
        port.at[i, "A_final_fill_rate"] = pct(fin_rate)
        port.at[i, "A_actual_exposure"] = round(actual_exp, 4) if not np.isnan(actual_exp) else ""
        port.at[i, "A_exposure_drift"] = round(drift, 4) if not np.isnan(drift) else ""
        port.at[i, "A_execution_slippage"] = round(slip, 3) if not np.isnan(slip) else ""
        port.at[i, "A_cash_drag"] = round(cash_drag * 100, 2) if not np.isnan(cash_drag) else ""
        # 週報酬（需 next 週 nav）
        port.at[i, "A_weekly_return"] = np.nan
    # 週報酬與累計
    # Whole-strategy NAV cannot establish A-channel real return from fills alone.
    port["A_weekly_return"] = np.nan
    port["A_cumulative_return"] = np.nan
    port.to_csv(PORT_CSV, index=False, encoding="utf-8-sig")
    print("重算完成：%d 週，%d 筆訂單" % (port.shape[0], live.shape[0]))
    print(port[["week", "execution_date", "A_target_orders", "A_after_hours_fill_rate",
                "A_final_fill_rate", "A_exposure_drift", "A_execution_slippage",
                "A_weekly_return", "A_cumulative_return"]].to_string(index=False))

if __name__ == "__main__":
    main()
