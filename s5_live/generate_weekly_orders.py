# -*- coding: utf-8 -*-
"""S5 每週下單清單產生器
從 output/latest_signals.json（B3 canonical）產生當週 A、B 通道下單清單，
其中 A 通道依 S5 執行，B 通道維持原本下單方式；
並 append 到 20 週累計檔 S5_live_20w.csv 與 portfolio 檔。

用法（週五 14:00 前跑）：
    python s5_live/generate_weekly_orders.py            # 用最新 canonical
    python s5_live/generate_weekly_orders.py --dry-run  # 只印清單不寫檔（測試用）
"""
import os, sys, json, io, argparse
from datetime import datetime, date
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(BASE, "output", "latest_signals.json")
ORDERS_DIR = os.path.join(BASE, "s5_live", "orders")
LIVE_CSV = os.path.join(BASE, "s5_live", "S5_live_20w.csv")
PORT_CSV = os.path.join(BASE, "s5_live", "S5_live_20w_portfolio.csv")

ORDER_COLS = ["week", "signal_date", "execution_date", "pool", "side", "symbol",
              "target_qty", "after_hours_order_qty", "after_hours_filled_qty",
              "after_hours_fill_rate", "unfilled_qty", "next_day_order_qty",
              "next_day_filled_qty", "next_day_fill_rate", "final_filled_qty",
              "final_fill_rate", "after_hours_price", "next_day_open_price",
              "final_avg_price", "reference_price", "slippage", "transaction_cost", "note"]
PORT_COLS = ["week", "execution_date", "A_target_exposure", "A_actual_exposure", "A_closing_value", "A_cash",
             "A_target_orders", "A_filled_orders", "A_unfilled_orders", "A_after_hours_fill_rate",
             "A_final_fill_rate", "A_exposure_drift", "A_cash_drag", "A_execution_slippage",
             "A_weekly_return", "A_cumulative_return", "whole_nav", "whole_exposure",
             "b_exposure", "total_cash", "note"]

def side_target(a):
    """依 action 決定下單方向與目標股數（canonical 慣例：BUY 看 buy_shares、SELL 看 sell_shares）"""
    act = a["action"]
    if act in ("BUY", "REBALANCE_BUY"):
        return act, int(a["buy_shares"])
    if act in ("SELL", "REBALANCE_SELL"):
        return act, int(a["sell_shares"])
    return None, 0   # HOLD 不掛單

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    d = json.load(io.open(CANON, encoding="utf-8"))
    exec_date = datetime.strptime(d["rebalance_date"], "%Y-%m-%d").date()
    sig_date = d["sig_date"]
    # ISO 週次
    iso = exec_date.isocalendar()
    week = "%d-W%02d" % (iso.year, iso.week)

    rows = []
    a_target_exposure = 0.0
    a_orders = 0
    for a in d["actions"]:
        if a["channel"] == "A":
            a_target_exposure += a["target_weight"]   # 含 HOLD：A 通道目標總曝險
        side, qty = side_target(a)
        if side is None or qty <= 0:
            continue
        if a["channel"] == "A":
            a_orders += 1
        rows.append({
            "week": week, "signal_date": sig_date, "execution_date": str(exec_date),
            "pool": a["channel"], "side": side, "symbol": "%s %s" % (a["stock_code"], a["stock_name"]),
            "target_qty": qty, "after_hours_order_qty": qty, "after_hours_filled_qty": "",
            "after_hours_fill_rate": "", "unfilled_qty": "", "next_day_order_qty": "",
            "next_day_filled_qty": "", "next_day_fill_rate": "", "final_filled_qty": "",
            "final_fill_rate": "", "after_hours_price": "", "next_day_open_price": "",
            "final_avg_price": "", "reference_price": a["price"], "slippage": "",
            "transaction_cost": "", "note": ""})
    if not rows:
        print("本週無下單（HOLD 或空清單）")
        return

    odf = pd.DataFrame(rows, columns=ORDER_COLS)
    fname = "%s_%s.csv" % (week, exec_date)
    print(odf.to_string(index=False))
    if args.dry_run:
        print("[dry-run] 不寫檔，將產生:", fname)
        return

    # 該週已存在 → 不重複產生
    if os.path.exists(os.path.join(ORDERS_DIR, fname)):
        print("本週清單已存在:", fname, "（跳過，避免重複）")
        return
    os.makedirs(ORDERS_DIR, exist_ok=True)
    odf.to_csv(os.path.join(ORDERS_DIR, fname), index=False, encoding="utf-8-sig")
    # append 累計檔（首週建 header）
    odf.to_csv(LIVE_CSV, mode="a", header=not os.path.exists(LIVE_CSV),
               index=False, encoding="utf-8-sig")

    # portfolio 層骨架
    port = {"week": week, "execution_date": str(exec_date),
            "A_target_exposure": round(a_target_exposure, 4),
            "A_actual_exposure": "", "A_cash": "", "A_target_orders": a_orders,
            "A_filled_orders": "", "A_unfilled_orders": "", "A_after_hours_fill_rate": "",
            "A_final_fill_rate": "", "A_exposure_drift": "", "A_cash_drag": "",
            "A_execution_slippage": "", "A_weekly_return": "", "A_cumulative_return": "",
            "whole_nav": d["portfolio_value"], "whole_exposure": 1 - d["buffer"],
            "b_exposure": d.get("wb_eff", 0.4), "total_cash": d["cash"], "note": ""}
    pdf = pd.DataFrame([port], columns=PORT_COLS)
    pdf.to_csv(PORT_CSV, mode="a", header=not os.path.exists(PORT_CSV),
               index=False, encoding="utf-8-sig")
    print("已寫入:", fname, "| 累計檔已更新 | A 目標曝險 %.1f%% | A 訂單 %d 筆" % (a_target_exposure * 100, a_orders))

if __name__ == "__main__":
    main()
