# -*- coding: utf-8 -*-
"""
ROE 本地庫更新腳本（每季財報公布後執行一次）
============================================================
- 先經 FinLab data.get() 觸發快取更新（ROE 資料量小，單次 ~2MB）
- 再直接讀 finlab_db/fundamental_features#ROE稅後.feather（公告日 index 版）
- 固化為 roe_local.feather（獨立本地庫；update_data.py 日常讀此檔、零 FinLab 流量）
- 財報公布時程（約）：Q1→5/15、Q2→8/14、Q3→11/14、Q4(年報)→3/31
- 用法：python update_roe.py
============================================================
"""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)


def load_token():
    env_t = (os.environ.get("FINLAB_TOKEN") or "").strip()
    if env_t:
        return env_t
    try:
        cfg = json.load(open("config.json", encoding="utf-8"))
        return (cfg.get("FINLAB_TOKEN") or "").strip()
    except Exception:
        return ""


TOKEN = load_token()
if not TOKEN:
    print("❌ 找不到 FinLab token（請在 config.json 填入 FINLAB_TOKEN）")
    sys.exit(1)

import pandas as pd
import finlab
from finlab import data

finlab.login(api_token=TOKEN)
data.set_storage(data.FileStorage("finlab_db"))

print("① 觸發 FinLab ROE 快取更新（每季一次，正常約 1~2 分鐘）…")
_ = data.get("fundamental_features:ROE稅後")  # 觸發重抓/更新 finlab_db

feather = os.path.join("finlab_db", "fundamental_features#ROE稅後.feather")
if not os.path.exists(feather):
    print("❌ 找不到 ROE 快取檔，改用 data.get() 回傳值直接固化")
    roe = data.get("fundamental_features:ROE稅後")
    roe_out = roe.reset_index()
    if "date" not in roe_out.columns:
        roe_out = roe_out.rename(columns={roe_out.columns[0]: "date"})
else:
    roe_out = pd.read_feather(feather)

roe_out["date"] = pd.to_datetime(roe_out["date"])
roe_out = roe_out.sort_values("date")
roe_out.to_feather("roe_local.feather")
print(f"② 已更新 roe_local.feather：{roe_out.shape}（{roe_out['date'].min().date()} ~ {roe_out['date'].max().date()}）")

# 回讀驗證
chk = pd.read_feather("roe_local.feather")
print(f"③ 回讀 OK：{chk.shape}，最後公告日 {chk['date'].max().date()}")
print("✅ 完成！下次 update_data.py 將直接讀本地 ROE，不耗 FinLab 流量。")
